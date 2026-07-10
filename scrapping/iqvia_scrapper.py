import json
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup


class IQVIAScraper:
	"""
	Scrapes IQVIA jobs from the public jobs page payload and keeps only jobs
	matching countries/categories from the source URL filters.
	"""

	SOURCE_URL = (
		"https://jobs.iqvia.com/en/jobs"
		"?utm_source=iqvia.com"
		"&utm_medium=referral"
		"&utm_campaign=careersnav"
		"&_ga=2.174215202.1099381201.1775718578-1582164715.1775718578"
		"&locations=Netherlands,Belgium,Austria,Czech+Republic,Denmark,Finland,"
		"Germany,France,Greece,Ireland,Norway"
		"&categories=Clinical+Operations,Administration+and+Support"
	)

	COMPANY = "IQVIA"

	def __init__(self, output_file="json_files/iqvia_jobs.json"):
		self.output_file = output_file
		self.jobs = []
		self._load_existing_jobs()

		self.allowed_locations, self.allowed_categories = self._parse_source_filters(
			self.SOURCE_URL
		)

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
	# Filters and parsing helpers
	# ------------------------------------------------------------------ #

	@staticmethod
	def _normalize(value):
		return re.sub(r"\s+", " ", (value or "").strip().lower())

	def _parse_source_filters(self, source_url):
		parsed = urlparse(source_url)
		query = parse_qs(parsed.query)

		locations_raw = query.get("locations", [""])[0]
		categories_raw = query.get("categories", [""])[0]

		locations = [part.strip() for part in locations_raw.split(",") if part.strip()]
		categories = [part.strip() for part in categories_raw.split(",") if part.strip()]

		print("Using source URL filters:")
		print(f"  locations ({len(locations)}): {', '.join(locations)}")
		print(f"  categories ({len(categories)}): {', '.join(categories)}")

		return locations, categories

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

	def _fetch_source_html(self):
		response = requests.get(self.SOURCE_URL, headers=self._headers(), timeout=50)
		response.raise_for_status()
		return response.text

	@staticmethod
	def _extract_jobs_array_from_html(html):
		marker = r'\"jobs\":['
		start = html.find(marker)
		if start == -1:
			return []

		arr_start = start + len(marker) - 1
		depth = 0
		in_string = False
		escaped = False
		arr_end = -1

		for idx in range(arr_start, len(html)):
			ch = html[idx]

			if in_string:
				if escaped:
					escaped = False
				elif ch == "\\":
					escaped = True
				elif ch == '"':
					in_string = False
			else:
				if ch == '"':
					in_string = True
				elif ch == "[":
					depth += 1
				elif ch == "]":
					depth -= 1
					if depth == 0:
						arr_end = idx
						break

		if arr_end == -1:
			return []

		escaped_array = html[start + len(marker) - 1 : arr_end + 1]

		# Unescape only JSON key/value quotes while preserving UTF-8 text.
		cleaned_text = escaped_array.replace(r'\"', '"')

		# Parsed section can include trailing payload content; read only first array.
		parsed_obj, _ = json.JSONDecoder().raw_decode(cleaned_text)
		return parsed_obj if isinstance(parsed_obj, list) else []

	@staticmethod
	def _split_units(employment_unit):
		return [part.strip() for part in (employment_unit or "").split(";") if part.strip()]

	@staticmethod
	def _build_job_link(instance_id):
		return f"https://jobs.iqvia.com/en/jobs/{instance_id}" if instance_id else ""

	@staticmethod
	def _normalize_apply_link(link):
		if not link:
			return ""
		link = link.strip()
		if "myworkdayjobs.com" in link and not link.endswith("/apply"):
			return f"{link}/apply"
		return link

	@staticmethod
	def _extract_description_and_apply_from_job_html(html):
		soup = BeautifulSoup(html, "html.parser")

		apply_link = ""
		apply_anchor = soup.select_one("a[data-apply-link='true']")
		if apply_anchor and apply_anchor.get("href"):
			apply_link = apply_anchor.get("href", "").strip()

		desc_root = soup.select_one("[class*=jobDescription]")
		if not desc_root:
			return "", apply_link

		for block in desc_root.select("[class*=jobHeader], [class*=links], [class*=social]"):
			block.decompose()

		description_text = desc_root.get_text(" ", strip=True)
		description_text = re.sub(r"\s+", " ", description_text).strip()

		return description_text, apply_link

	def fetch_job_descriptions(self, delay=0.3):
		jobs_to_update = [job for job in self.jobs if job.get("link") and not job.get("description")]
		if not jobs_to_update:
			print("\nAll jobs already contain descriptions.")
			return

		print(f"\nFetching descriptions for {len(jobs_to_update)} jobs...")
		success_count = 0
		failed_count = 0

		for index, job in enumerate(jobs_to_update, start=1):
			link = job.get("link", "")
			if not link:
				failed_count += 1
				continue

			try:
				response = requests.get(link, headers=self._headers(), timeout=50)
				response.raise_for_status()
				description_text, apply_link = self._extract_description_and_apply_from_job_html(response.text)

				if description_text:
					job["description"] = description_text
					success_count += 1
				else:
					failed_count += 1

				if apply_link:
					job["apply_link"] = self._normalize_apply_link(apply_link)
				elif job.get("apply_link"):
					job["apply_link"] = self._normalize_apply_link(job.get("apply_link", ""))

				print(f"  [{index}/{len(jobs_to_update)}] {'ok' if description_text else 'no description'} - {job.get('title', '')}")
			except Exception:
				failed_count += 1
				print(f"  [{index}/{len(jobs_to_update)}] failed - {job.get('title', '')}")

			if delay > 0:
				time.sleep(delay)

		self._save_jobs()
		print("\n" + "=" * 60)
		print(f"Descriptions fetched: {success_count}")
		print(f"Descriptions missing/failed: {failed_count}")
		print(f"Updated file: {self.output_file}")
		print("=" * 60)

	# ------------------------------------------------------------------ #
	# Main parser
	# ------------------------------------------------------------------ #

	def parse_job_listings(self):
		allowed_locations = {self._normalize(v) for v in self.allowed_locations}
		allowed_categories = {self._normalize(v) for v in self.allowed_categories}

		print("\nFetching IQVIA source page...")
		html = self._fetch_source_html()

		print("Extracting embedded jobs payload...")
		all_jobs = self._extract_jobs_array_from_html(html)
		print(f"  embedded jobs found: {len(all_jobs)}")

		existing_ids = self._get_existing_job_ids()
		new_count = 0
		skipped_count = 0

		for item in all_jobs:
			address = ((item.get("job_location") or {}).get("address") or {})
			country = address.get("country", "")
			city = address.get("city") or ""
			region = address.get("region") or ""

			units = self._split_units(item.get("employment_unit", ""))
			normalized_units = {self._normalize(v) for v in units}

			# Exact source-filter matching by country and category token.
			if self._normalize(country) not in allowed_locations:
				continue
			if not (normalized_units & allowed_categories):
				continue

			job_id = item.get("instance_id") or item.get("job_req_id")
			if not job_id:
				continue

			if job_id in existing_ids:
				skipped_count += 1
				continue

			title = item.get("job_title", "")
			req_id = item.get("job_req_id", "")
			employment_type = item.get("employment_type", "")
			date_posted = item.get("date_posted", "")

			location_parts = [part for part in [city, region, country] if part]
			location = ", ".join(location_parts)

			job = {
				"title": title,
				"job_id": job_id,
				"job_req_id": req_id,
				"instance_id": item.get("instance_id", ""),
				"link": self._build_job_link(item.get("instance_id", "")),
				"apply_link": self._normalize_apply_link(item.get("application_url", "")),
				"location": location,
				"city": city,
				"state": region,
				"country": country,
				"job_type": employment_type,
				"posted_date": date_posted,
				"company": self.COMPANY,
				"category": "; ".join(units),
				"department": "; ".join(units),
				"description": "",
				"status": "active",
				"source": "iqvia",
				"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
			}

			self.jobs.append(job)
			existing_ids.add(job_id)
			new_count += 1

			print(f"  + {title} | {country} | {'; '.join(units)}")

		self._save_jobs()

		print("\n" + "=" * 60)
		print(f"New jobs added: {new_count}")
		print(f"Skipped duplicates: {skipped_count}")
		print(f"Total stored jobs: {len(self.jobs)}")
		print(f"Saved to: {self.output_file}")
		print("=" * 60)

	def run(self):
		self.parse_job_listings()
		self.fetch_job_descriptions()


if __name__ == "__main__":
	scraper = IQVIAScraper(output_file="json_files/iqvia_jobs.json")
	scraper.run()
