import json
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests


class ElyLillyScraper:
	"""
	Scrapes Eli Lilly jobs from the Phenom careers site using server-rendered
	search pages, then stores only jobs matching selected countries/categories.

	Source page provided by user:
	https://careers.lilly.com/us/en/c/salesmarketing-jobs
	"""

	COMPANY = "Eli Lilly"
	SOURCE_URL = "https://careers.lilly.com/us/en/c/salesmarketing-jobs"
	SEARCH_URL = "https://careers.lilly.com/us/en/search-results"

	FILTER_COUNTRIES = [
		"Germany",
		"Hungary",
		"France",
		"Italy",
		"Spain",
		"Poland",
		"Netherlands",
		"Belgium",
		"Austria",
	]

	FILTER_CATEGORIES = [
		"Finance",
		"Business",
		"Research & Development",
		"Information Technology",
		"Manufacturing/Quality",
	]

	PAGE_SIZE = 10

	def __init__(self, output_file="json_files/ely_lily_jobs.json"):
		self.output_file = output_file
		self.jobs = []
		self._load_existing_jobs()

	# ------------------------------------------------------------------ #
	# Persistence helpers
	# ------------------------------------------------------------------ #

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

	def _get_existing_job_ids(self):
		return {job.get("job_id") for job in self.jobs if job.get("job_id")}

	# ------------------------------------------------------------------ #
	# HTTP and parsing helpers
	# ------------------------------------------------------------------ #

	@staticmethod
	def _headers():
		return {
			"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
			"Accept-Language": "en-US,en;q=0.9",
			"User-Agent": (
				"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
				"AppleWebKit/537.36 (KHTML, like Gecko) "
				"Chrome/124.0.0.0 Safari/537.36"
			),
		}

	def _fetch_search_page(self, country, category, offset=0):
		params = {
			"qcountry": country,
			"category": category,
			"from": offset,
		}
		query = urlencode(params, safe="/")
		url = f"{self.SEARCH_URL}?{query}"

		for attempt in range(4):
			if attempt > 0:
				wait = 2 ** attempt
				print(f"  Retry {attempt}/3 for {country} | {category} | from={offset} after {wait}s")
				time.sleep(wait)

			try:
				resp = requests.get(url, headers=self._headers(), timeout=40)
				if resp.status_code == 200:
					return resp.text
				print(f"  [warn] HTTP {resp.status_code} for {url}")
			except Exception as exc:
				print(f"  [warn] request failed for {url}: {exc}")

		return ""

	@staticmethod
	def _extract_total_hits(html):
		match = re.search(r'"totalHits":(\d+)', html)
		return int(match.group(1)) if match else 0

	@staticmethod
	def _extract_jobs_from_html(html):
		match = re.search(r'"jobs":(\[.*?\]),"aggregations"', html, re.DOTALL)
		if not match:
			return []

		try:
			return json.loads(match.group(1))
		except Exception:
			return []

	@staticmethod
	def _slugify(text):
		slug = (text or "").strip().lower()
		slug = re.sub(r"[^a-z0-9]+", "-", slug)
		slug = re.sub(r"-{2,}", "-", slug).strip("-")
		return slug or "job"

	@staticmethod
	def _normalize(value):
		return re.sub(r"\s+", " ", (value or "").strip().lower())

	# ------------------------------------------------------------------ #
	# Listings parser
	# ------------------------------------------------------------------ #

	def parse_job_listings(self):
		print("\nFetching filtered Eli Lilly jobs...")
		print(f"Source URL: {self.SOURCE_URL}")
		print(f"Countries: {', '.join(self.FILTER_COUNTRIES)}")
		print(f"Categories: {', '.join(self.FILTER_CATEGORIES)}")

		existing_ids = self._get_existing_job_ids()
		allowed_countries = {self._normalize(v) for v in self.FILTER_COUNTRIES}
		allowed_categories = {self._normalize(v) for v in self.FILTER_CATEGORIES}

		new_count = 0
		seen_query_jobs = set()

		for country in self.FILTER_COUNTRIES:
			for category in self.FILTER_CATEGORIES:
				print(f"\nQuerying {country} | {category}")
				offset = 0
				total_hits = None

				while True:
					html = self._fetch_search_page(country=country, category=category, offset=offset)
					if not html:
						break

					if total_hits is None:
						total_hits = self._extract_total_hits(html)
						print(f"  totalHits={total_hits}")

					jobs_data = self._extract_jobs_from_html(html)
					if not jobs_data:
						break

					for job_data in jobs_data:
						job_id = job_data.get("jobId") or job_data.get("reqId")
						if not job_id:
							continue

						if job_id in seen_query_jobs:
							continue
						seen_query_jobs.add(job_id)

						job_country = self._normalize(job_data.get("country", ""))
						job_category = self._normalize(job_data.get("category", ""))

						# Defensive filter: keep only exact requested countries/categories.
						if job_country not in allowed_countries:
							continue
						if job_category not in allowed_categories:
							continue

						if job_id in existing_ids:
							continue

						title = job_data.get("title", "")
						job_seq_no = job_data.get("jobSeqNo", "")
						job_url = (
							f"https://careers.lilly.com/us/en/job/{job_id}/{self._slugify(title)}"
							if job_id
							else ""
						)

						job = {
							"title": title,
							"job_id": job_id,
							"job_seq_no": job_seq_no,
							"link": job_url,
							"apply_link": job_data.get("applyUrl", ""),
							"location": job_data.get("location", ""),
							"city": job_data.get("city", ""),
							"country": job_data.get("country", ""),
							"job_type": job_data.get("type", ""),
							"posted_date": job_data.get("postedDate", ""),
							"company": self.COMPANY,
							"category": job_data.get("category", ""),
							"department": job_data.get("category", ""),
							"description": job_data.get("descriptionTeaser", ""),
							"status": "active",
							"source": "ely_lily",
							"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
						}

						self.jobs.append(job)
						existing_ids.add(job_id)
						new_count += 1
						print(f"  + {title} | {job_data.get('country', '')} | {job_data.get('category', '')}")

					offset += self.PAGE_SIZE
					if total_hits is not None and offset >= total_hits:
						break

		self._save_jobs()
		print("\n" + "=" * 60)
		print(f"New jobs added: {new_count}")
		print(f"Total stored jobs: {len(self.jobs)}")
		print(f"Saved to: {self.output_file}")
		print("=" * 60)

	def run(self):
		self.parse_job_listings()


if __name__ == "__main__":
	scraper = ElyLillyScraper(output_file="json_files/ely_lily_jobs.json")
	scraper.run()
