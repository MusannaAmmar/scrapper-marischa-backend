import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


class LeuwendalAdviceScrapper:
	"""
	Scrapes Leeuwendaal vacancies for Directie & Management filter.

	Source URL:
	  https://www.leeuwendaal.nl/vacatures/zoeken/?vakgebied=directie-management
	"""

	SOURCE_URL = "https://www.leeuwendaal.nl/vacatures/zoeken/?vakgebied=directie-management"
	COMPANY = "Leeuwendaal"
	SOURCE = "Leeuwendaal"

	def __init__(self, output_file="json_files/leuwendal_advice_jobs.json"):
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
				"Chrome/124.0.0.0 Safari/537.36"
			),
		}

	def _fetch_html(self, url):
		resp = requests.get(url, headers=self._headers(), timeout=60)
		resp.raise_for_status()
		return resp.text

	@staticmethod
	def _extract_job_id(link):
		path = urlparse(link).path.strip("/")
		slug = path.split("/")[-1] if path else ""
		if not slug:
			return ""

		match = re.search(r"-(a0[a-z0-9]+)$", slug, re.IGNORECASE)
		if match:
			return match.group(1)

		return slug

	@staticmethod
	def _next_page_url(soup, current_url):
		next_link = soup.select_one("link[rel='next']")
		if next_link and next_link.get("href"):
			next_url = urljoin(current_url, next_link.get("href"))
			# For filtered search pages, avoid drifting to generic archive pagination.
			if "vakgebied=" in current_url and "vakgebied=" not in next_url:
				return ""
			return next_url

		alt_next = soup.select_one("a[rel='next']")
		if alt_next and alt_next.get("href"):
			next_url = urljoin(current_url, alt_next.get("href"))
			if "vakgebied=" in current_url and "vakgebied=" not in next_url:
				return ""
			return next_url

		return ""

	@staticmethod
	def _field_from_card_text(text, field_name):
		pattern = rf"{re.escape(field_name)}\s*:\s*([^\n\r]+)"
		match = re.search(pattern, text, re.IGNORECASE)
		return match.group(1).strip() if match else ""

	def _extract_description(self, link):
		try:
			html = self._fetch_html(link)
		except Exception:
			return ""

		soup = BeautifulSoup(html, "html.parser")
		candidates = [
			soup.select_one("main"),
			soup.select_one("article"),
			soup.select_one(".entry-content"),
		]

		for node in candidates:
			if not node:
				continue
			text = node.get_text("\n", strip=True)
			text = re.sub(r"\n{3,}", "\n\n", text).strip()
			if len(text) >= 120:
				return text

		return ""

	def _parse_listing_cards(self, soup, page_url, page_category):
		# Desktop cards contain a single primary CTA anchor with class `btn-primary-blue`.
		cta_anchors = soup.select("a.btn-primary-blue[href*='/vacatures/']")
		jobs = []

		for cta in cta_anchors:
			link = urljoin(page_url, cta.get("href") or "")
			if not link:
				continue

			card = cta
			while card and card.name != "body":
				card = card.find_parent("div")
				if not card:
					break
				if card.select_one("h4") and card.select_one("a.btn-primary-blue"):
					break

			if not card:
				continue

			title_el = card.select_one("h4")
			title = title_el.get_text(" ", strip=True) if title_el else ""
			if not title:
				continue

			company = ""
			company_el = card.select_one("span.text-base")
			if company_el:
				company = company_el.get_text(" ", strip=True)

			card_text = card.get_text("\n", strip=True)
			location = self._field_from_card_text(card_text, "Locatie")
			closing_date = self._field_from_card_text(card_text, "Sluitingsdatum")

			job = {
				"title": title,
				"job_id": self._extract_job_id(link),
				"job_seq_no": self._extract_job_id(link),
				"link": link,
				"apply_link": link,
				"location": location,
				"city": location,
				"country": "Netherlands",
				"job_type": "",
				"workplace_type": "",
				"posted_date": closing_date,
				"company": company or self.COMPANY,
				"category": page_category,
				"department": page_category,
				"description": "",
				"skills": [],
				"status": "active",
				"source": self.SOURCE,
				"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
			}
			jobs.append(job)

		return jobs

	def parse_job_listings(self):
		print(f"Fetching Leeuwendaal jobs from {self.SOURCE_URL}")

		existing_index = {
			str(job.get("job_id")): idx
			for idx, job in enumerate(self.jobs)
			if job.get("job_id")
		}

		visited_pages = set()
		seen_job_ids = set()
		page_url = self.SOURCE_URL
		page_count = 0
		new_count = 0
		updated_count = 0
		duplicate_cards = 0
		skipped_existing = 0

		while page_url and page_url not in visited_pages:
			visited_pages.add(page_url)
			page_count += 1
			print(f"Parsing listing page {page_count}: {page_url}")

			html = self._fetch_html(page_url)
			soup = BeautifulSoup(html, "html.parser")
			page_category_el = soup.select_one("section.container h1")
			page_category = page_category_el.get_text(" ", strip=True) if page_category_el else ""

			page_jobs = self._parse_listing_cards(soup, page_url, page_category)
			print(f"  Vacancy cards found: {len(page_jobs)}")

			for job in page_jobs:
				job_id = str(job.get("job_id") or "").strip()
				if not job_id:
					continue

				if job_id in seen_job_ids:
					duplicate_cards += 1
					continue
				seen_job_ids.add(job_id)

				if job_id in existing_index:
					skipped_existing += 1
					continue

				job["description"] = self._extract_description(job.get("link") or "")
				self.jobs.append(job)
				existing_index[job_id] = len(self.jobs) - 1
				new_count += 1

				print(f"  + {job['title'][:90]} | {job.get('location', '')}")

			next_page = self._next_page_url(soup, page_url)
			page_url = next_page if next_page and next_page not in visited_pages else ""

		# Keep only records from current crawl for this source.
		self.jobs = [
			j
			for j in self.jobs
			if str(j.get("source") or "").strip().lower() != self.SOURCE.lower()
			or str(j.get("job_id") or "") in seen_job_ids
		]

		self._save_jobs()
		print("\n" + "=" * 60)
		print(f"Pages parsed          : {page_count}")
		print(f"Unique jobs seen      : {len(seen_job_ids)}")
		print(f"Duplicate cards skipped: {duplicate_cards}")
		print(f"New jobs stored       : {new_count}")
		print(f"Jobs updated          : {updated_count}")
		print(f"Existing skipped      : {skipped_existing}")
		print(f"Total jobs in file    : {len(self.jobs)}")
		print("=" * 60)

	def run(self):
		self.parse_job_listings()
		print(f"Done. Output written to {self.output_file}")


if __name__ == "__main__":
	scrapper = LeuwendalAdviceScrapper()
	scrapper.run()
