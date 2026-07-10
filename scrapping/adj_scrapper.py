import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


class AdjScrapper:
	"""
	Scrapes ADJ vacancies and stores normalized job entries.

	Source URL:
	  https://adj.nl/vacature-overzicht/
	"""

	SOURCE_URL = "https://adj.nl/vacature-overzicht/"
	COMPANY = "ADJ"
	SOURCE = "ADJ"

	def __init__(self, output_file="json_files/adj_jobs.json"):
		self.output_file = output_file
		self.jobs = []
		self._load_existing_jobs()

	def _load_existing_jobs(self):
		if os.path.exists(self.output_file):
			with open(self.output_file, "r", encoding="utf-8") as f:
				self.jobs = json.load(f)
			print(f"Loaded {len(self.jobs)} existing jobs from {self.output_file}")
		else:
			self.jobs = []

	def _save_jobs(self):
		with open(self.output_file, "w", encoding="utf-8") as f:
			json.dump(self.jobs, f, indent=2, ensure_ascii=False)

	@staticmethod
	def _headers():
		return {
			"Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
			"User-Agent": (
				"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
				"AppleWebKit/537.36 (KHTML, like Gecko) "
				"Chrome/122.0.0.0 Safari/537.36"
			),
		}

	def _fetch_html(self, url):
		resp = requests.get(url, headers=self._headers(), timeout=60)
		resp.raise_for_status()
		return resp.text

	@staticmethod
	def _clean_text(value):
		return " ".join(str(value or "").replace("\xa0", " ").split()).strip()

	@staticmethod
	def _normalize_multiline(text):
		if not text:
			return ""
		text = text.replace("\r", "")
		text = text.replace("\xa0", " ")
		text = re.sub(r"\n{3,}", "\n\n", text)
		return text.strip()

	@staticmethod
	def _job_id_from_url(url):
		path = (urlparse(url).path or "").strip("/")
		slug = path.split("/")[-1] if path else ""
		return slug

	@staticmethod
	def _slug_from_url(url):
		path = (urlparse(url).path or "").strip("/")
		return path.split("/")[-1] if path else ""

	def _collect_listing_pages(self, first_page_html):
		soup = BeautifulSoup(first_page_html, "html.parser")
		pages = [self.SOURCE_URL]
		for a in soup.select("a.pagination-link"):
			href = self._clean_text(a.get("href"))
			if not href:
				continue
			full = urljoin(self.SOURCE_URL, href)
			if full not in pages:
				pages.append(full)
		return pages

	def _extract_listing_cards(self, html):
		soup = BeautifulSoup(html, "html.parser")
		return soup.select("div.vacancies-list article.vacancy")

	def _extract_description_from_detail(self, html):
		soup = BeautifulSoup(html, "html.parser")
		container = soup.select_one("#vacancy-article-detail-content")
		if not container:
			meta_desc = soup.select_one("meta[name='description']")
			return self._clean_text(meta_desc.get("content") if meta_desc else "")

		chunks = []
		for heading in container.select("h3.owp-heading-3"):
			h = self._clean_text(heading.get_text(" ", strip=True))
			if h:
				chunks.append(h)
			next_block = heading.find_next_sibling("div", class_="vacancy-item-text")
			if next_block:
				t = self._normalize_multiline(next_block.get_text("\n", strip=True))
				if t:
					chunks.append(t)

		if chunks:
			return self._normalize_multiline("\n\n".join(chunks))

		fallback = self._normalize_multiline(container.get_text("\n", strip=True))
		if fallback:
			return fallback

		meta_desc = soup.select_one("meta[name='description']")
		return self._clean_text(meta_desc.get("content") if meta_desc else "")

	@staticmethod
	def _extract_posted_date(detail_soup):
		date_node = detail_soup.select_one("div.vacancy-date")
		if not date_node:
			return ""
		raw = date_node.get_text(" ", strip=True)
		m = re.search(r"(\d{2})-(\d{2})-(\d{4})", raw)
		if not m:
			return ""
		return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"

	def parse_job_listings(self):
		print(f"Fetching ADJ jobs from {self.SOURCE_URL}")
		first_html = self._fetch_html(self.SOURCE_URL)
		listing_pages = self._collect_listing_pages(first_html)
		print(f"Listing pages found: {len(listing_pages)}")

		existing_index = {
			str(job.get("job_id")): idx
			for idx, job in enumerate(self.jobs)
			if job.get("job_id")
		}

		detail_cache = {}
		seen_ids = set()
		new_count = 0
		updated_count = 0
		skipped_existing = 0
		parsed_count = 0

		for page_url in listing_pages:
			try:
				html = first_html if page_url == self.SOURCE_URL else self._fetch_html(page_url)
			except Exception as exc:
				print(f"  ! failed listing page fetch: {page_url} ({exc})")
				continue

			cards = self._extract_listing_cards(html)
			print(f"  - cards on page: {len(cards)} | {page_url}")

			for card in cards:
				anchor = card.select_one("a.vacancy-content")
				if not anchor:
					continue

				href = self._clean_text(anchor.get("href"))
				link = urljoin(self.SOURCE_URL, href)
				job_id = self._job_id_from_url(link)
				if not job_id:
					continue

				seen_ids.add(job_id)
				if job_id in existing_index:
					skipped_existing += 1
					continue

				title_node = card.select_one("h3.vacancy-title")
				title = self._clean_text(title_node.get_text(" ", strip=True) if title_node else "")

				summary_node = card.select_one("div.vacancy-text")
				summary = self._normalize_multiline(summary_node.get_text("\n", strip=True) if summary_node else "")

				job_type_node = card.select_one("div.vacancy-criteria-option[title='Dienstverband']")
				job_type = self._clean_text(job_type_node.get_text(" ", strip=True) if job_type_node else "")

				detail_html = ""
				posted_date = ""
				description = summary

				detail_html = detail_cache.get(link)
				if detail_html is None:
					try:
						detail_html = self._fetch_html(link)
					except Exception as exc:
						print(f"  ! failed detail fetch: {link} ({exc})")
						detail_html = ""
					detail_cache[link] = detail_html

				if detail_html:
					detail_soup = BeautifulSoup(detail_html, "html.parser")
					new_description = self._extract_description_from_detail(detail_html)
					new_posted_date = self._extract_posted_date(detail_soup)
					if new_description:
						description = new_description
					if new_posted_date:
						posted_date = new_posted_date

				status = "active"
				title_l = title.lower()
				if "reactietermijn verstreken" in title_l or "vervuld" in title_l:
					status = "inactive"

				job = {
					"title": title,
					"job_id": job_id,
					"job_seq_no": job_id,
					"link": link,
					"apply_link": urljoin(link + "/", "solliciteer/"),
					"location": "",
					"city": "",
					"country": "Netherlands",
					"job_type": job_type,
					"workplace_type": "",
					"posted_date": posted_date,
					"company": self.COMPANY,
					"category": "",
					"department": "",
					"description": description,
					"skills": [],
					"status": status,
					"source": self.SOURCE,
					"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
					"slug": self._slug_from_url(link),
				}

				parsed_count += 1

				self.jobs.append(job)
				existing_index[job_id] = len(self.jobs) - 1
				new_count += 1

				print(f"  + parsed: {title[:80]}")

		# Keep non-ADJ jobs and currently visible ADJ jobs.
		self.jobs = [
			j for j in self.jobs
			if str(j.get("source") or "").strip().lower() != self.SOURCE.lower()
			or str(j.get("job_id") or "").strip() in seen_ids
		]

		self._save_jobs()
		print("\n" + "=" * 60)
		print(f"Cards parsed        : {parsed_count}")
		print(f"Unique vacancies    : {len(seen_ids)}")
		print(f"New jobs stored     : {new_count}")
		print(f"Jobs updated        : {updated_count}")
		print(f"Existing skipped    : {skipped_existing}")
		print(f"Total jobs in file  : {len(self.jobs)}")
		print("=" * 60)

	def run(self):
		self.parse_job_listings()
		print(f"Done. Output written to {self.output_file}")


if __name__ == "__main__":
	scrapper = AdjScrapper()
	scrapper.run()
