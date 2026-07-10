import json
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv


load_dotenv()


class BeOneMedicineScrapper:
	def __init__(
		self,
		output_file="json_files/beonemedicine_jobs.json",
		source_url=(
			"https://beigene.wd5.myworkdayjobs.com/en-US/BeiGene?"
			"jobFamilyGroup=8ed4a38402b901f5e897e493870c8f20&"
			"jobFamilyGroup=8ed4a38402b9014d817f8f93870c6420&"
			"jobFamilyGroup=1eebd6d194321001737411489b840000&"
			"jobFamilyGroup=8ed4a38402b901ae0f22c193870c7e20&"
			"jobFamilyGroup=8ed4a38402b901770982ee93870c9520&"
			"jobFamilyGroup=8ed4a38402b901657a6aa793870c7020&"
			"locationCountry=187134fccb084a0ea9b4b95f23890dbe&"
			"locationCountry=29247e57dbaf46fb855b224e03170bc7"
		),
		html_file="beonemedicine.html",
		apikey=os.getenv("ZENROWS"),
	):
		self.output_file = output_file
		self.source_url = source_url
		self.html_file = html_file
		self.apikey = apikey

		self.base_url = "https://beigene.wd5.myworkdayjobs.com"
		self.cxs_company = "beigene"
		self.site_id = "BeiGene"

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
	def _clean_text(value):
		if not value:
			return ""
		return re.sub(r"\s+", " ", str(value)).strip()

	def _hydrate_workday_config_from_html(self):
		if not os.path.exists(self.html_file):
			return

		try:
			with open(self.html_file, "r", encoding="utf-8") as f:
				html = f.read()

			tenant_match = re.search(r"tenant\s*:\s*\"([^\"]+)\"", html)
			site_match = re.search(r"siteId\s*:\s*\"([^\"]+)\"", html)

			if tenant_match:
				self.cxs_company = tenant_match.group(1).strip()
			if site_match:
				self.site_id = site_match.group(1).strip()
		except Exception as e:
			print(f"[warn] Could not parse {self.html_file}: {e}")

	@property
	def jobs_api_url(self):
		return f"{self.base_url}/wday/cxs/{self.cxs_company}/{self.site_id}/jobs"

	def _build_filters_from_source_url(self):
		parsed = urlparse(self.source_url)
		query = parse_qs(parsed.query)
		return {
			"jobFamilyGroup": query.get("jobFamilyGroup", []),
			"locationCountry": query.get("locationCountry", []),
		}

	def _post_jobs_page(self, offset, limit, filters):
		payload = {
			"appliedFacets": {
				"jobFamilyGroup": filters.get("jobFamilyGroup", []),
				"locationCountry": filters.get("locationCountry", []),
			},
			"limit": limit,
			"offset": offset,
			"searchText": "",
		}
		headers = {
			"Accept": "application/json",
			"Content-Type": "application/json",
			"User-Agent": (
				"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
				"AppleWebKit/537.36 (KHTML, like Gecko) "
				"Chrome/124.0.0.0 Safari/537.36"
			),
		}

		for attempt in range(3):
			if attempt > 0:
				time.sleep(2 ** attempt)
			try:
				response = requests.post(self.jobs_api_url, json=payload, headers=headers, timeout=45)
				if response.status_code == 200:
					return response.json()
				print(f"  [warn] jobs API HTTP {response.status_code}: {response.text[:220]}")
			except Exception as e:
				print(f"  [warn] jobs API attempt {attempt + 1} failed: {e}")
		return None

	def fetch_html_zenrows(self, url):
		if not self.apikey:
			return None

		params = {
			"url": url,
			"apikey": self.apikey,
			"js_render": "true",
			"premium_proxy": "true",
		}

		for attempt in range(3):
			if attempt > 0:
				time.sleep(2 ** attempt)
			try:
				response = requests.get("https://api.zenrows.com/v1/", params=params, timeout=90)
				if response.status_code == 200:
					return response.text
				print(f"  [warn] ZenRows HTTP {response.status_code}: {response.text[:220]}")
			except Exception as e:
				print(f"  [warn] ZenRows attempt {attempt + 1} failed: {e}")
		return None

	@staticmethod
	def _extract_job_id(external_path):
		if not external_path:
			return ""
		match = re.search(r"_([A-Za-z]+\d+(?:-[A-Za-z0-9]+)*)$", external_path.rstrip("/"))
		if match:
			return match.group(1)
		return external_path.strip("/").replace("/", "_")

	def parse_job_listings(self):
		self._hydrate_workday_config_from_html()
		filters = self._build_filters_from_source_url()
		print(f"Fetching BeOne Medicine job listings from filtered source URL...")
		print(f"  API endpoint: {self.jobs_api_url}")

		existing_index = {
			str(job.get("job_id")): idx
			for idx, job in enumerate(self.jobs)
			if job.get("job_id")
		}
		existing_ids = set(existing_index.keys())

		offset = 0
		limit = 20
		total = None
		page_no = 0
		new_count = 0
		updated_count = 0
		seen_job_ids = set()

		while True:
			page_no += 1
			print(f"  Fetching page {page_no} (offset={offset})")
			data = self._post_jobs_page(offset=offset, limit=limit, filters=filters)
			if not data:
				print("  Could not fetch listing page. Stopping pagination.")
				break

			postings = data.get("jobPostings", [])
			if total is None:
				total = int(data.get("total", 0))
				print(f"  Total filtered jobs reported by API: {total}")

			if not postings:
				break

			for posting in postings:
				external_path = posting.get("externalPath", "")
				job_id = self._extract_job_id(external_path)
				if not job_id:
					continue

				seen_job_ids.add(job_id)

				title = self._clean_text(posting.get("title"))
				locations = posting.get("locationsText", "")
				if isinstance(locations, list):
					locations = ", ".join([self._clean_text(x) for x in locations if x])
				locations = self._clean_text(locations)

				posted_date = self._clean_text(posting.get("postedOn"))
				bullet_fields = posting.get("bulletFields", [])
				if not isinstance(bullet_fields, list):
					bullet_fields = []

				job_type = self._clean_text(bullet_fields[0]) if len(bullet_fields) > 0 else ""
				category = self._clean_text(bullet_fields[1]) if len(bullet_fields) > 1 else ""

				public_link = f"{self.base_url}/en-US/{self.site_id}{external_path}"

				payload = {
					"title": title,
					"job_id": job_id,
					"job_seq_no": job_id,
					"link": public_link,
					"external_path": external_path,
					"location": locations or None,
					"city": locations or None,
					"country": "",
					"job_type": job_type or "",
					"posted_date": posted_date or None,
					"salary": "",
					"company": "BeOne Medicines",
					"category": category or "",
					"department": "",
					"description": "",
					"description_fetched": False,
					"skills": [],
					"status": "active",
					"source": "BeOne Medicines",
					"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
				}

				if job_id in existing_ids:
					idx = existing_index[job_id]
					existing = self.jobs[idx]
					if str(existing.get("description") or "").strip():
						payload["description"] = existing.get("description")
					if existing.get("description_fetched"):
						payload["description_fetched"] = True
					self.jobs[idx] = {**existing, **payload}
					updated_count += 1
				else:
					self.jobs.append(payload)
					existing_ids.add(job_id)
					existing_index[job_id] = len(self.jobs) - 1
					new_count += 1

			offset += len(postings)
			if total is not None and offset >= total:
				break

			time.sleep(0.6)

		expired_count = 0
		for job in self.jobs:
			if str(job.get("source") or "").strip().lower() != "beone medicines":
				continue
			job_id = str(job.get("job_id") or "")
			if job_id and job_id not in seen_job_ids:
				job["status"] = "expired"
				expired_count += 1

		self._save_jobs()
		print(
			f"Found {new_count} new jobs, updated {updated_count}, marked {expired_count} expired. "
			f"Total: {len(self.jobs)} jobs."
		)

	def _extract_description_from_rendered_html(self, html):
		soup = BeautifulSoup(html, "html.parser")

		container = soup.find(attrs={"data-automation-id": "job-posting-details"})
		if not container:
			container = soup.find(attrs={"data-automation-id": "richTextContainer"})

		if container:
			text = container.get_text(separator="\n", strip=True)
			text = re.sub(r"\n{3,}", "\n\n", text).strip()
			return text

		og = soup.find("meta", attrs={"property": "og:description"})
		if og:
			return self._clean_text(og.get("content", ""))

		return ""

	def fetch_job_descriptions(self, delay=1.0):
		jobs_to_update = [
			j
			for j in self.jobs
			if str(j.get("source") or "").strip().lower() == "beone medicines"
			and str(j.get("status") or "").strip().lower() == "active"
			and not j.get("description_fetched", False)
			and j.get("link")
		]

		if not jobs_to_update:
			print("All BeOne Medicines active jobs already have descriptions.")
			return

		print(f"Fetching descriptions for {len(jobs_to_update)} BeOne Medicines job(s)...")
		success_count = 0
		failed_count = 0

		for i, job in enumerate(jobs_to_update, start=1):
			print(f"[{i}/{len(jobs_to_update)}] {job.get('title')}")
			html = self.fetch_html_zenrows(job["link"])

			if not html:
				failed_count += 1
				print("  [warn] Could not fetch rendered HTML")
				continue

			description = self._extract_description_from_rendered_html(html)
			if description and len(description) >= 60:
				job["description"] = description
				job["description_fetched"] = True
				success_count += 1
				print(f"  + Description fetched ({len(description.split())} words)")
			else:
				failed_count += 1
				job["description_fetched"] = True
				print("  [warn] Description not found in rendered HTML")

			self._save_jobs()
			time.sleep(delay)

		print(f"Description refresh done. Success: {success_count}, Failed: {failed_count}")

	def run(self):
		self.parse_job_listings()
		self.fetch_job_descriptions()


if __name__ == "__main__":
	scraper = BeOneMedicineScrapper()
	scraper.run()
