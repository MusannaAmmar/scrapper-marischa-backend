import json
import os
import time
from datetime import datetime, timezone

import requests


class PhilipsScrapper:
	"""
	Scrapes Philips global search results and applies user-selected filters.

	Source URL:
	  https://www.careers.philips.com/global/en/search-results

	The page embeds a JSON object under `eagerLoadRefineSearch` containing
	paginated jobs and facet aggregations. We paginate via `from` offset and
	keep only jobs matching configured country/category filters.
	"""

	SEARCH_URL = "https://www.careers.philips.com/global/en/search-results"
	COMPANY = "Philips"

	# Filters from the provided screenshot
	ALLOWED_COUNTRIES = {
		"Belgium",
		"Austria",
		"Finland",
		"Germany",
		"France",
		"Greece",
		"Hungary",
		"Netherlands",
		"Poland",
		"Portugal",
		"Switzerland",
	}

	ALLOWED_CATEGORIES = {
		"Experience Design",
		"Health, Clinical and Medical Safety",
		"Enterprise Excellence",
	}

	def __init__(self, output_file="json_files/philips_jobs.json"):
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
			"User-Agent": (
				"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
				"AppleWebKit/537.36 (KHTML, like Gecko) "
				"Chrome/122.0.0.0 Safari/537.36"
			),
			"Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
		}

	def _fetch_search_page_html(self, offset):
		params = {
			"from": str(offset),
			"s": "1",
		}

		for attempt in range(4):
			if attempt > 0:
				wait = 2 ** attempt
				print(f"  Retry {attempt}/3 for offset={offset} - waiting {wait}s...")
				time.sleep(wait)
			try:
				resp = requests.get(
					self.SEARCH_URL,
					params=params,
					headers=self._headers(),
					timeout=60,
				)
				if resp.status_code == 200:
					return resp.text
				print(f"  [warn] HTTP {resp.status_code} for offset={offset}")
			except Exception as e:
				print(f"  [error] Search page fetch failed at offset={offset}: {e}")

		return ""

	@staticmethod
	def _extract_json_object_after_key(text, key):
		key_idx = text.find(key)
		if key_idx == -1:
			return None

		start = text.find("{", key_idx + len(key))
		if start == -1:
			return None

		depth = 0
		in_string = False
		escape = False

		for i in range(start, len(text)):
			ch = text[i]

			if in_string:
				if escape:
					escape = False
				elif ch == "\\":
					escape = True
				elif ch == '"':
					in_string = False
				continue

			if ch == '"':
				in_string = True
				continue

			if ch == "{":
				depth += 1
			elif ch == "}":
				depth -= 1
				if depth == 0:
					raw = text[start : i + 1]
					try:
						return json.loads(raw)
					except Exception:
						return None

		return None

	def _extract_eager_payload(self, html):
		return self._extract_json_object_after_key(html, '"eagerLoadRefineSearch":')

	def _job_matches_filters(self, job):
		category_values = []
		if job.get("category"):
			category_values.append(str(job.get("category")).strip())

		for item in job.get("multi_category_array", []) or []:
			value = (item or {}).get("category")
			if value:
				category_values.append(str(value).strip())

		category_values = [v for v in category_values if v]
		matched_category = next((v for v in category_values if v in self.ALLOWED_CATEGORIES), "")
		if not matched_category:
			return False, "", ""

		country_values = []
		if job.get("country"):
			country_values.append(str(job.get("country")).strip())

		location_list = job.get("multi_location_array", []) or []
		for loc in location_list:
			loc_text = str((loc or {}).get("location") or "").strip()
			if loc_text:
				parts = [p.strip() for p in loc_text.split(",") if p.strip()]
				if parts:
					country_values.append(parts[-1])

		country_values = [v for v in country_values if v]
		matched_country = next((v for v in country_values if v in self.ALLOWED_COUNTRIES), "")
		if not matched_country:
			return False, "", ""

		return True, matched_country, matched_category

	def parse_job_listings(self, delay=0.2, max_pages=None):
		print("\nFetching Philips jobs and applying selected filters...")

		existing_index = {
			job.get("job_id"): idx
			for idx, job in enumerate(self.jobs)
			if job.get("job_id")
		}
		existing_ids = set(existing_index.keys())

		offset = 0
		page_size = 10
		total_hits = None

		seen_jobs = 0
		matched_jobs = 0
		total_new = 0
		total_updated = 0
		total_skipped_existing = 0
		pages_processed = 0

		while True:
			if max_pages is not None and pages_processed >= max_pages:
				print(f"Reached max_pages={max_pages}; stopping pagination.")
				break

			try:
				html = self._fetch_search_page_html(offset)
			except KeyboardInterrupt:
				print("\nInterrupted by user. Saving collected jobs and stopping...")
				self._save_jobs()
				break

			if not html:
				print("No HTML returned; stopping pagination.")
				break

			payload = self._extract_eager_payload(html)
			if not payload:
				print("Could not parse eagerLoadRefineSearch payload; stopping.")
				break

			if total_hits is None:
				total_hits = int(payload.get("totalHits", 0) or 0)

			page_hits = int(payload.get("hits", 0) or 0)
			data = payload.get("data", {}) or {}
			jobs = data.get("jobs", []) or []

			query = payload.get("query", {}) or {}
			try:
				page_size = int(query.get("size", page_size) or page_size)
			except Exception:
				page_size = 10

			print(
				f"  Page offset={offset}: got {len(jobs)} jobs "
				f"(page hits={page_hits}, reported total={total_hits})"
			)

			if not jobs:
				break

			for raw_job in jobs:
				seen_jobs += 1

				matched, matched_country, matched_category = self._job_matches_filters(raw_job)
				if not matched:
					continue

				matched_jobs += 1

				job_id = (
					str(raw_job.get("jobId") or "").strip()
					or str(raw_job.get("reqId") or "").strip()
					or str(raw_job.get("jobSeqNo") or "").strip()
				)
				if not job_id:
					continue

				title = str(raw_job.get("title") or "").strip()
				location = str(raw_job.get("location") or "").strip()
				city = str(raw_job.get("city") or "").strip()
				department = str(raw_job.get("department") or "").strip()
				job_type = str(raw_job.get("type") or "").strip()
				posted_date = str(raw_job.get("postedDate") or "").strip()
				link = str(raw_job.get("payType") or "").strip()
				apply_link = str(raw_job.get("applyUrl") or "").strip() or link

				description = (
					str(raw_job.get("descriptionTeaser") or "").strip()
					or str(((raw_job.get("ml_job_parser") or {}).get("descriptionTeaser_ats")) or "").strip()
				)

				skills = raw_job.get("ml_skills", []) or []
				if not isinstance(skills, list):
					skills = []

				job = {
					"title": title,
					"job_id": job_id,
					"job_seq_no": str(raw_job.get("jobSeqNo") or job_id).strip(),
					"link": link,
					"apply_link": apply_link,
					"location": location,
					"city": city,
					"country": matched_country,
					"job_type": job_type,
					"workplace_type": "",
					"posted_date": posted_date,
					"company": self.COMPANY,
					"category": matched_category,
					"department": department,
					"description": description,
					# "skills": skills,
					"status": "active",
					"source": "philips",
					"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
				}

				if job_id in existing_ids:
					total_skipped_existing += 1
					continue
				else:
					self.jobs.append(job)
					existing_ids.add(job_id)
					existing_index[job_id] = len(self.jobs) - 1
					total_new += 1
					print(f"  + {title[:70]} | {matched_country} | {matched_category}")

			self._save_jobs()
			pages_processed += 1

			offset += page_size
			if total_hits is not None and offset >= total_hits:
				break

			time.sleep(delay)

		print("\n" + "=" * 60)
		print(f"Jobs scanned       : {seen_jobs}")
		print(f"Jobs matched       : {matched_jobs}")
		print(f"New jobs found     : {total_new}")
		print(f"Jobs updated       : {total_updated}")
		print(f"Existing skipped   : {total_skipped_existing}")
		print(f"Total jobs stored  : {len(self.jobs)}")
		print("=" * 60)

	def run(self):
		self.parse_job_listings()


if __name__ == "__main__":
	print("Starting Philips job scraper...")
	print("=" * 60)

	scraper = PhilipsScrapper(output_file="json_files/philips_jobs.json")
	scraper.run()

	print("\n" + "=" * 60)
	print(f"Done! Total jobs: {len(scraper.jobs)}")
	print("=" * 60)
