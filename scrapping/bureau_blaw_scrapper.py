import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup


class BureauBlawScrapper:
	"""
	Scrapes Bureau Blaauw vacancies and stores normalized job entries.

	Source URL:
	  https://www.bureaublaauw.com/en/vacatures/
	"""

	SOURCE_URL = "https://www.bureaublaauw.com/en/vacatures/"
	COMPANY = "Bureau Blaauw"
	SOURCE = "Bureau Blaauw"

	def __init__(self, output_file="json_files/bureau_blauw_jobs.json"):
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
		text = str(value or "").replace("\xa0", " ")
		return " ".join(text.split()).strip()

	@staticmethod
	def _normalize_multiline(text):
		if not text:
			return ""
		text = text.replace("\r", "").replace("\xa0", " ")
		text = re.sub(r"\n{3,}", "\n\n", text)
		return text.strip()

	@staticmethod
	def _slug_from_url(url):
		path = (urlparse(url).path or "").strip("/")
		return path.split("/")[-1] if path else ""

	def _extract_listing_cards(self, html):
		soup = BeautifulSoup(html, "html.parser")
		return soup.select("div.vacancies.notranslate > a.item")

	def _extract_description(self, detail_html):
		soup = BeautifulSoup(detail_html, "html.parser")
		content_root = soup.select_one("div.notranslate")
		if not content_root:
			meta_desc = soup.select_one("meta[name='description']")
			return self._clean_text(meta_desc.get("content") if meta_desc else "")

		parts = []

		# Main vacancy blocks with long text.
		for section in content_root.select("section.vacancy, section.bg-pale"):
			headings = [h.get_text(" ", strip=True) for h in section.select("h2, h3")]
			for h in headings:
				hc = self._clean_text(h)
				if hc and hc.lower() not in {"interesse voor de vacature?"}:
					parts.append(hc)

			for block in section.select("div.is-content"):
				text = self._normalize_multiline(block.get_text("\n", strip=True))
				if text and len(text) > 40:
					parts.append(text)

			for ul in section.select("ul"):
				items = [self._clean_text(li.get_text(" ", strip=True)) for li in ul.select("li")]
				items = [i for i in items if i]
				if items:
					parts.append("\n".join(f"- {i}" for i in items))

		if parts:
			return self._normalize_multiline("\n\n".join(parts))

		meta_desc = soup.select_one("meta[name='description']")
		return self._clean_text(meta_desc.get("content") if meta_desc else "")

	def parse_job_listings(self):
		print(f"Fetching Bureau Blaauw jobs from {self.SOURCE_URL}")
		html = self._fetch_html(self.SOURCE_URL)
		cards = self._extract_listing_cards(html)

		if not cards:
			print("No vacancy cards found")
			return

		print(f"Vacancy cards found: {len(cards)}")

		existing_index = {
			str(job.get("job_id")): idx
			for idx, job in enumerate(self.jobs)
			if job.get("job_id")
		}

		seen_ids = set()
		new_count = 0
		updated_count = 0
		skipped_existing = 0
		detail_cache = {}

		for card in cards:
			link = self._clean_text(card.get("href"))
			if not link:
				continue

			title_node = card.select_one("h3.title")
			title = self._clean_text(title_node.get_text(" ", strip=True) if title_node else "")

			company_node = card.select_one("p.company")
			client_company = self._clean_text(company_node.get_text(" ", strip=True) if company_node else "")

			slug = self._slug_from_url(link)
			job_id = slug
			if not job_id:
				continue

			seen_ids.add(job_id)
			if job_id in existing_index:
				skipped_existing += 1
				continue

			detail_html = detail_cache.get(link)
			if detail_html is None:
				try:
					detail_html = self._fetch_html(link)
				except Exception as exc:
					print(f"  ! failed detail fetch: {link} ({exc})")
					detail_html = ""
				detail_cache[link] = detail_html

			detail_soup = BeautifulSoup(detail_html, "html.parser") if detail_html else BeautifulSoup("", "html.parser")
			description = self._extract_description(detail_html) if detail_html else ""

			apply_link = ""
			apply_btn = detail_soup.select_one("a.btn.btn--main[href*='apply']")
			if apply_btn:
				apply_link = self._clean_text(apply_btn.get("href"))
			if not apply_link:
				apply_link = link

			location = ""
			city = ""
			location_li = detail_soup.select_one("section.vacancy ul.detail li:nth-of-type(2)")
			if location_li:
				location = self._clean_text(location_li.get_text(" ", strip=True))
				city = location.split("/")[0].split(",")[0].strip()

			job = {
				"title": title,
				"job_id": job_id,
				"job_seq_no": job_id,
				"link": link,
				"apply_link": apply_link,
				"location": location,
				"city": city,
				"country": "Netherlands",
				"job_type": "",
				"workplace_type": "",
				"posted_date": "",
				"company": self.COMPANY,
				"category": "",
				"department": "",
				"description": description,
				# "skills": [],
				"status": "active",
				"source": self.SOURCE,
				"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
				"slug": slug,
				"client_company": client_company,
			}

			self.jobs.append(job)
			existing_index[job_id] = len(self.jobs) - 1
			new_count += 1

			print(f"  + parsed: {title[:80]}")

		# Keep non-Bureau Blaauw jobs and currently visible Bureau Blaauw jobs.
		self.jobs = [
			j for j in self.jobs
			if str(j.get("source") or "").strip().lower() != self.SOURCE.lower()
			or str(j.get("job_id") or "").strip() in seen_ids
		]

		self._save_jobs()
		print("\n" + "=" * 60)
		print(f"Vacancies parsed    : {len(seen_ids)}")
		print(f"New jobs stored     : {new_count}")
		print(f"Jobs updated        : {updated_count}")
		print(f"Existing skipped    : {skipped_existing}")
		print(f"Total jobs in file  : {len(self.jobs)}")
		print("=" * 60)

	def run(self):
		self.parse_job_listings()
		print(f"Done. Output written to {self.output_file}")


if __name__ == "__main__":
	scrapper = BureauBlawScrapper()
	scrapper.run()
