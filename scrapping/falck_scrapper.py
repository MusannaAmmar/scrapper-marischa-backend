import json
import os
import time
from html import unescape
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup


class FalckScrapper:
	"""
	Scrapes Falck jobs from Oracle Candidate Experience API.

	Source page:
	  https://fa-expf-saasfaeuraprod1.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001
	API endpoint:
	  GET /hcmRestApi/resources/latest/recruitingCEJobRequisitions
	  finder=findReqs;siteNumber=CX_1001,limit={n},offset={n}&expand=requisitionList
	"""

	API_BASE_URL = "https://fa-expf-saasfaeuraprod1.fa.ocs.oraclecloud.com"
	SITE_NUMBER = "CX_1001"
	COMPANY = "Falck"
	LIST_API_URL = f"{API_BASE_URL}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
	DETAIL_PAGE_URL = f"{API_BASE_URL}/hcmUI/CandidateExperience/en/sites/{SITE_NUMBER}/jobs/preview/{{job_id}}"
	APPLY_DEEPLINK_URL = (
		f"{API_BASE_URL}/fscmUI/faces/deeplink"
		"?objType=IRC_RECRUITING"
		"&action=ICE_JOB_DETAILS_RESP"
		"&objKey=pRequisitionNo={job_id};pCalledFrom=FUSESHELL"
	)

	def __init__(self, output_file="json_files/falck_jobs.json"):
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

	def _get_existing_job_ids(self):
		return {job.get("job_id") for job in self.jobs if job.get("job_id")}

	@staticmethod
	def _headers():
		return {
			"Accept": "application/json,text/plain,*/*",
			"User-Agent": (
				"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
				"AppleWebKit/537.36 (KHTML, like Gecko) "
				"Chrome/122.0.0.0 Safari/537.36"
			),
		}

	def _fetch_page(self, offset, limit):
		params = {
			"finder": f"findReqs;siteNumber={self.SITE_NUMBER},limit={limit},offset={offset}",
			"expand": "requisitionList",
			"onlyData": "true",
		}

		for attempt in range(4):
			if attempt > 0:
				wait = 2 ** attempt
				print(f"  Retry {attempt}/3 for offset={offset} - waiting {wait}s...")
				time.sleep(wait)
			try:
				resp = requests.get(
					self.LIST_API_URL,
					params=params,
					headers=self._headers(),
					timeout=45,
				)
				if resp.status_code == 200:
					return resp.json()
				print(f"  [warn] HTTP {resp.status_code}: {resp.text[:200]}")
			except Exception as e:
				print(f"  [error] List API failed at offset={offset}: {e}")

		return None

	def _fetch_preview_page_html(self, url):
		for attempt in range(3):
			if attempt > 0:
				wait = 2 ** attempt
				print(f"    Retry {attempt}/2 for preview page - waiting {wait}s...")
				time.sleep(wait)
			try:
				resp = requests.get(url, headers=self._headers(), timeout=45)
				if resp.status_code == 200:
					return resp.text
				print(f"    [warn] Preview page HTTP {resp.status_code}: {url}")
			except Exception as e:
				print(f"    [error] Preview page fetch failed: {e}")

		return ""

	@staticmethod
	def _split_primary_location(location):
		if not location:
			return "", ""

		parts = [p.strip() for p in location.split(",") if p.strip()]
		if not parts:
			return "", ""

		city = parts[0]
		country = parts[-1]
		return city, country

	@staticmethod
	def _build_description(req):
		sections = []

		short_desc = (req.get("ShortDescriptionStr") or "").strip()
		responsibilities = (req.get("ExternalResponsibilitiesStr") or "").strip()
		qualifications = (req.get("ExternalQualificationsStr") or "").strip()

		if short_desc:
			sections.append(short_desc)
		if responsibilities:
			sections.append("Responsibilities:\n" + responsibilities)
		if qualifications:
			sections.append("Qualifications:\n" + qualifications)

		return "\n\n".join(sections).strip()

	@staticmethod
	def _extract_description_from_preview_html(html):
		if not html:
			return ""

		soup = BeautifulSoup(html, "html.parser")

		# Oracle CE preview pages expose at least a clean og:description on initial HTML.
		og = soup.find("meta", property="og:description")
		if og and og.get("content"):
			return unescape(og.get("content", "")).strip()

		meta_desc = soup.find("meta", attrs={"name": "description"})
		if meta_desc and meta_desc.get("content"):
			return unescape(meta_desc.get("content", "")).strip()

		return ""

	def parse_job_listings(self, page_size=25):
		print("\nFetching Falck jobs from Oracle Candidate Experience API...")
		print(f"Using siteNumber={self.SITE_NUMBER}")

		existing_index = {
			job.get("job_id"): idx
			for idx, job in enumerate(self.jobs)
			if job.get("job_id")
		}
		existing_ids = set(existing_index.keys())
		total_new = 0
		total_updated = 0
		total_skipped_existing = 0
		total_seen = 0
		offset = 0

		while True:
			data = self._fetch_page(offset=offset, limit=page_size)
			if not data:
				print("No data returned from API; stopping.")
				break

			items = data.get("items", [])
			if not items:
				print("No search payload returned; stopping.")
				break

			search_payload = items[0]
			requisitions = search_payload.get("requisitionList", []) or []
			total_count = search_payload.get("TotalJobsCount", 0)

			print(
				f"  Page offset={offset}: got {len(requisitions)} jobs "
				f"(reported total={total_count})"
			)

			if not requisitions:
				break

			for req in requisitions:
				total_seen += 1

				job_id = str(req.get("Id", "")).strip()
				if not job_id:
					continue

				if job_id in existing_ids:
					total_skipped_existing += 1
					continue

				title = (req.get("Title") or "").strip()
				location = (req.get("PrimaryLocation") or "").strip()
				city, country = self._split_primary_location(location)

				posted_date = (req.get("PostedDate") or "").strip()
				category = (
					req.get("JobFunction")
					or req.get("JobFamily")
					or req.get("Organization")
					or ""
				)
				department = (req.get("Department") or "").strip()
				workplace_type = (req.get("WorkplaceType") or "").strip()
				job_type = (
					req.get("JobType")
					or req.get("WorkerType")
					or req.get("ContractType")
					or ""
				)

				detail_link = self.DETAIL_PAGE_URL.format(job_id=job_id)
				apply_link = self.APPLY_DEEPLINK_URL.format(job_id=job_id)

				job = {
					"title": title,
					"job_id": job_id,
					"job_seq_no": job_id,
					"link": detail_link,
					"apply_link": apply_link,
					"location": location,
					"city": city,
					"country": country,
					"job_type": str(job_type).strip(),
					"workplace_type": workplace_type,
					"posted_date": posted_date,
					"company": self.COMPANY,
					"category": str(category).strip(),
					"department": department,
					"description": self._build_description(req),
					"skills": [],
					"status": "active",
					"source": "falck",
					"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
				}

				self.jobs.append(job)
				existing_ids.add(job_id)
				existing_index[job_id] = len(self.jobs) - 1
				total_new += 1
				print(f"  + {title[:70]} | {location[:45]} | {job_id}")

			offset += page_size
			if offset >= total_count:
				break

			time.sleep(0.3)

		self._save_jobs()

		print("\n" + "=" * 60)
		print(f"Jobs seen         : {total_seen}")
		print(f"New jobs found    : {total_new}")
		print(f"Jobs updated      : {total_updated}")
		print(f"Existing skipped  : {total_skipped_existing}")
		print(f"Total jobs stored : {len(self.jobs)}")
		print("=" * 60)

	def fetch_job_descriptions(self, delay=0.3):
		jobs_to_update = [
			j for j in self.jobs
			if str(j.get("source") or "").strip().lower() == "falck"
			and len(str(j.get("description") or "").strip()) < 120
		]
		if not jobs_to_update:
			print("\nNo Falck jobs available for description refresh.")
			return

		print(f"\nRefreshing descriptions from preview pages for {len(jobs_to_update)} job(s)...")
		success_count = 0
		failed_count = 0

		for i, job in enumerate(jobs_to_update, start=1):
			link = (job.get("link") or "").strip()
			if not link:
				failed_count += 1
				continue

			print(f"  [{i}/{len(jobs_to_update)}] {job.get('job_id', '')} - {job.get('title', '')[:50]}")

			html = self._fetch_preview_page_html(link)
			page_desc = self._extract_description_from_preview_html(html)
			current_desc = (job.get("description") or "").strip()

			if page_desc:
				# Keep the richer of API-derived and page-derived text.
				job["description"] = page_desc if len(page_desc) >= len(current_desc) else current_desc
				success_count += 1
			else:
				failed_count += 1

			if i % 5 == 0:
				self._save_jobs()
				print(f"    Progress saved ({i}/{len(jobs_to_update)})")

			time.sleep(delay)

		self._save_jobs()
		print("\n" + "=" * 60)
		print("Description refresh complete")
		print(f"Successful : {success_count}")
		print(f"Failed     : {failed_count}")
		print("=" * 60)

	def run(self):
		self.parse_job_listings(page_size=25)
		self.fetch_job_descriptions()


if __name__ == "__main__":
	print("Starting Falck job scraper...")
	print("=" * 60)

	scraper = FalckScrapper()
	scraper.run()

	print("\n" + "=" * 60)
	print(f"Done! Total jobs: {len(scraper.jobs)}")
	print("=" * 60)
