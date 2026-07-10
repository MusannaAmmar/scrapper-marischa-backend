import json
import os
import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup


class DoctolibScrapper:
	"""
	Scrapes Doctolib jobs from careers.doctolib.fr and keeps only executive matches.

	Source URL:
	  https://careers.doctolib.fr/jobs/?search=executive

	Job data is embedded in `acfData.jobs[0].list` as a JSON string.
	"""

	SOURCE_URL = "https://careers.doctolib.fr/jobs/?search=executive"
	COMPANY = "Doctolib"
	SEARCH_TERM = "executive"

	def __init__(self, output_file="json_files/doctolib_jobs.json"):
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

	def _fetch_html(self):
		resp = requests.get(self.SOURCE_URL, headers=self._headers(), timeout=60)
		resp.raise_for_status()
		return resp.text

	@staticmethod
	def _extract_acf_data(html):
		match = re.search(r"var\s+acfData\s*=\s*(\{.*?\});", html, re.DOTALL)
		if not match:
			return {}
		try:
			return json.loads(match.group(1))
		except Exception:
			return {}

	@staticmethod
	def _extract_embedded_jobs(acf_data):
		jobs_blocks = acf_data.get("jobs")
		if not isinstance(jobs_blocks, list) or not jobs_blocks:
			return []

		jobs_list_raw = jobs_blocks[0].get("list")
		if not jobs_list_raw:
			return []

		try:
			jobs_list_data = json.loads(jobs_list_raw)
		except Exception:
			return []

		jobs = jobs_list_data.get("jobs") if isinstance(jobs_list_data, dict) else []
		return jobs if isinstance(jobs, list) else []

	@staticmethod
	def _to_text(html):
		if not html:
			return ""
		soup = BeautifulSoup(str(html), "html.parser")
		text = soup.get_text(separator="\n", strip=True)
		return re.sub(r"\n{3,}", "\n\n", text).strip()

	def _is_executive_match(self, offer):
		needle = self.SEARCH_TERM.lower()
		title = str(offer.get("title") or "").lower()
		slug = str(offer.get("slug") or "").lower()

		# Keep filter strict to avoid false positives from long descriptions.
		if needle in title or needle in slug:
			return True

		return False

	@staticmethod
	def _metadata_value(metadata, key_name):
		if not isinstance(metadata, list):
			return ""
		key_l = key_name.lower()
		for item in metadata:
			if str(item.get("name") or "").strip().lower() == key_l:
				return str(item.get("value") or "").strip()
		return ""

	def parse_job_listings(self):
		print(f"Fetching Doctolib jobs from {self.SOURCE_URL}")
		html = self._fetch_html()
		acf_data = self._extract_acf_data(html)
		offers = self._extract_embedded_jobs(acf_data)

		if not offers:
			print("No embedded jobs found in acfData payload")
			return

		print(f"Embedded jobs found: {len(offers)}")

		existing_index = {
			str(job.get("job_id")): idx
			for idx, job in enumerate(self.jobs)
			if job.get("job_id")
		}
		existing_ids = set(existing_index.keys())

		new_count = 0
		updated_count = 0
		skipped_existing = 0
		matched_count = 0

		for offer in offers:
			if not self._is_executive_match(offer):
				continue

			matched_count += 1
			job_id = str(offer.get("id") or "").strip()
			if not job_id:
				continue

			title = str(offer.get("title") or "").strip()
			slug = str(offer.get("slug") or "").strip()
			cpt_url = str(offer.get("cpt_url") or "").strip()
			absolute_url = str(offer.get("absolute_url") or "").strip()

			location_obj = offer.get("location") or {}
			location = str(location_obj.get("name") or "").strip()
			country = str(offer.get("country") or "").strip()
			city = location.split(",")[0].strip() if location else ""

			departments = offer.get("departments") or []
			department = ""
			if departments and isinstance(departments[0], dict):
				department = str(departments[0].get("name") or "").strip()

			metadata = offer.get("metadata") or []
			job_type = self._metadata_value(metadata, "Employment Type")
			posted_country = self._metadata_value(metadata, "Job Posting Country")
			if posted_country and not country:
				country = posted_country

			description = self._to_text(offer.get("content") or "")
			link = cpt_url or absolute_url

			job = {
				"title": title,
				"job_id": job_id,
				"job_seq_no": job_id,
				"link": link,
				"apply_link": absolute_url or link,
				"location": location,
				"city": city,
				"country": country,
				"job_type": job_type,
				"workplace_type": "",
				"posted_date": "",
				"company": self.COMPANY,
				"category": department,
				"department": department,
				"description": description,
				"skills": [],
				"status": "active",
				"source": "Doctolib",
				"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
				"slug": slug,
			}

			if job_id in existing_ids:
				skipped_existing += 1
				continue
			else:
				self.jobs.append(job)
				existing_index[job_id] = len(self.jobs) - 1
				existing_ids.add(job_id)
				new_count += 1

			print(f"  + executive match: {title[:80]} | {location}")

		# Keep only Doctolib executive matches generated by this source.
		self.jobs = [
			j for j in self.jobs
			if str(j.get("source") or "").strip().lower() != "doctolib"
			or self.SEARCH_TERM.lower() in f"{j.get('title', '')} {j.get('slug', '')}".lower()
		]

		self._save_jobs()
		print("\n" + "=" * 60)
		print(f"Executive matches  : {matched_count}")
		print(f"New jobs stored    : {new_count}")
		print(f"Jobs updated       : {updated_count}")
		print(f"Existing skipped   : {skipped_existing}")
		print(f"Total jobs in file : {len(self.jobs)}")
		print("=" * 60)

	def run(self):
		self.parse_job_listings()
		print(f"Done. Output written to {self.output_file}")


if __name__ == "__main__":
	scrapper = DoctolibScrapper()
	scrapper.run()
