import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


class MeussenSearchScrapper:
	"""
	Scrapes Meussen Executive Search vacancies and stores normalized job entries.

	Source URL:
	  https://meussensearch.nl/open-posities/
	"""

	SOURCE_URL = "https://meussensearch.nl/open-posities/"
	COMPANY = "Meussen Executive Search"
	SOURCE = "MEUSSEN"

	def __init__(self, output_file="json_files/meussen_jobs.json"):
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

	

	def _extract_pagination_links(self, html, base_url):
		soup = BeautifulSoup(html, "html.parser")
		links = []
		for a in soup.select("div.pagination a.page-numbers[href]"):
			href = self._clean_text(a.get("href"))
			if not href:
				continue
			full = urljoin(base_url, href)
			full = full.split("#", 1)[0].split("?", 1)[0]
			if "/open-posities/" not in full:
				continue
			links.append(full)
		return links

	def _collect_listing_pages(self, first_page_html):
		queue = [self.SOURCE_URL]
		visited = set()
		pages = []
		html_cache = {self.SOURCE_URL: first_page_html}

		while queue:
			page_url = queue.pop(0)
			if page_url in visited:
				continue
			visited.add(page_url)

			html = html_cache.get(page_url)
			if html is None:
				try:
					html = self._fetch_html(page_url)
				except Exception as exc:
					print(f"  ! failed listing page fetch during discovery: {page_url} ({exc})")
					continue
				html_cache[page_url] = html

			pages.append(page_url)
			for next_link in self._extract_pagination_links(html, page_url):
				if next_link not in visited and next_link not in queue:
					queue.append(next_link)

		return pages, html_cache

	@staticmethod
	def _extract_listing_cards(html):
		soup = BeautifulSoup(html, "html.parser")
		return soup.select("div.vacancies-list > a.single-vacancy[href*='/vacancy/']")

	def _extract_description_from_detail(self, html, fallback_text=""):
		if not html:
			return self._normalize_multiline(fallback_text)

		soup = BeautifulSoup(html, "html.parser")
		chunks = []

		hero_summary = soup.select_one("div.vacancy-hero div.content p")
		if hero_summary:
			text = self._normalize_multiline(hero_summary.get_text("\n", strip=True))
			if text:
				chunks.append(text)

		for section in soup.select("section.split-text-block"):
			section_parts = []
			for block in section.select("div.flex-wrapper > div.title, div.flex-wrapper > div.content"):
				text = self._normalize_multiline(block.get_text("\n", strip=True))
				if text:
					section_parts.append(text)
			if section_parts:
				chunks.append("\n\n".join(section_parts))

		if chunks:
			return self._normalize_multiline("\n\n".join(chunks))

		meta_desc = soup.select_one("meta[name='description']")
		if meta_desc:
			return self._clean_text(meta_desc.get("content"))

		return self._normalize_multiline(fallback_text)

	def _extract_posted_date(self, detail_html):
		if not detail_html:
			return ""

		soup = BeautifulSoup(detail_html, "html.parser")
		for script in soup.select("script.yoast-schema-graph[type='application/ld+json']"):
			raw = (script.string or script.get_text() or "").strip()
			if not raw:
				continue

			try:
				data = json.loads(raw)
			except Exception:
				data = None

			if isinstance(data, dict):
				graph = data.get("@graph")
				if isinstance(graph, list):
					for item in graph:
						if not isinstance(item, dict):
							continue
						type_value = item.get("@type")
						is_webpage = type_value == "WebPage" or (
							isinstance(type_value, list) and "WebPage" in type_value
						)
						if not is_webpage:
							continue
						date_value = self._clean_text(item.get("datePublished"))
						m = re.match(r"(\d{4}-\d{2}-\d{2})", date_value)
						if m:
							return m.group(1)

			m = re.search(r'"datePublished"\s*:\s*"(\d{4}-\d{2}-\d{2})', raw)
			if m:
				return m.group(1)

		return ""



	def parse_job_listings(self):
		print(f"Fetching Meussen vacancies from {self.SOURCE_URL}")
		first_html = self._fetch_html(self.SOURCE_URL)
		listing_pages, listing_html_cache = self._collect_listing_pages(first_html)
		print(f"Listing pages found: {len(listing_pages)}")

		existing_index = {
			(
				str(job.get("source") or "").strip().lower(),
				str(job.get("job_id") or "").strip(),
			): idx
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
			html = listing_html_cache.get(page_url)
			if not html:
				try:
					html = self._fetch_html(page_url)
				except Exception as exc:
					print(f"  ! failed listing page fetch: {page_url} ({exc})")
					continue

			cards = self._extract_listing_cards(html)
			print(f"  - cards on page: {len(cards)} | {page_url}")

			for card in cards:
				href = self._clean_text(card.get("href"))
				if not href:
					continue
				link = urljoin(page_url, href).split("#", 1)[0].split("?", 1)[0]
				if "/vacancy/" not in (urlparse(link).path or ""):
					continue

				job_id = self._job_id_from_url(link)
				if not job_id or job_id in seen_ids:
					continue

				seen_ids.add(job_id)
				key = (self.SOURCE.lower(), job_id)
				if key in existing_index:
					skipped_existing += 1
					continue

				title_node = card.select_one("h4")
				title = self._clean_text(title_node.get_text(" ", strip=True) if title_node else "")

				location_node = card.select_one("span.term.location")
				location = self._clean_text(location_node.get_text(" ", strip=True) if location_node else "")
				discipline_node = card.select_one("span.term.discipline")
				discipline = self._clean_text(discipline_node.get_text(" ", strip=True) if discipline_node else "")
				salary_node = card.select_one("span.term.salary")
				salary = self._clean_text(salary_node.get_text(" ", strip=True) if salary_node else "")

				summary_parts = [
					f"Locatie: {location}" if location else "",
					f"Discipline: {discipline}" if discipline else "",
					f"Salaris: {salary}" if salary else "",
				]
				summary = self._normalize_multiline("\n".join([p for p in summary_parts if p]))

				detail_html = detail_cache.get(link)
				if detail_html is None:
					try:
						detail_html = self._fetch_html(link)
					except Exception as exc:
						print(f"  ! failed detail fetch: {link} ({exc})")
						detail_html = ""
					detail_cache[link] = detail_html

				description = self._extract_description_from_detail(detail_html, fallback_text=summary)
				posted_date = self._extract_posted_date(detail_html)

				button_node = card.select_one("span.btn")
				button_text = self._clean_text(button_node.get_text(" ", strip=True) if button_node else "")


				job = {
					"title": title,
					"job_id": job_id,
					"job_seq_no": job_id,
					"link": link,
					"apply_link": f"{link}#apply",
					"location": location,
					"city": location,
					"country": "Netherlands",
					"job_type": "",
					"workplace_type": "",
					"posted_date": posted_date,
					"company": self.COMPANY,
					"category": discipline,
					"description": description,
					"status": 'active',
					"source": self.SOURCE,
					"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
					"salary": salary,
				}

				parsed_count += 1

				self.jobs.append(job)
				existing_index[key] = len(self.jobs) - 1
				new_count += 1

				print(f"  + parsed: {title[:80]}")

		# Keep non-Meussen jobs and currently visible Meussen jobs.
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
	scrapper = MeussenSearchScrapper()
	scrapper.run()
