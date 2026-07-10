import json
import os
import re
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup


class IGHScrapper:
	"""
	Scrapes IG&H jobs from their Recruitee careers site.

	Source page:
	  https://igh.recruitee.com/?source=IG&HWebsite=&jobs-b9ce0317%5Btag%5D%5B%5D=Experienced

	Listing API:
	  GET /api/offers/

	Detail API:
	  GET /api/offers/{offer_id}
	"""

	BASE_URL = "https://igh.recruitee.com"
	COMPANY = "IG&H"
	LIST_API_URL = f"{BASE_URL}/api/offers/"
	DETAIL_API_URL = f"{BASE_URL}/api/offers/{{offer_id}}"
	TAG_FILTER_KEY = "jobs-b9ce0317[tag][]"
	TAG_FILTER_VALUE = "Experienced"

	def __init__(self, output_file="json_files/igh_jobs.json"):
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
			"Accept": "application/json,text/plain,*/*",
			"User-Agent": (
				"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
				"AppleWebKit/537.36 (KHTML, like Gecko) "
				"Chrome/122.0.0.0 Safari/537.36"
			),
		}

	@staticmethod
	def _to_text(html):
		if not html:
			return ""
		soup = BeautifulSoup(html, "html.parser")
		text = soup.get_text(separator="\n", strip=True)
		return re.sub(r"\n{3,}", "\n\n", text).strip()

	@staticmethod
	def _normalize_tags(tags):
		if not tags:
			return []

		normalized = []
		for t in tags:
			if isinstance(t, str):
				normalized.append(t.strip())
			elif isinstance(t, dict):
				name = (
					t.get("name")
					or t.get("title")
					or t.get("slug")
					or ""
				)
				if name:
					normalized.append(str(name).strip())

		return [t for t in normalized if t]

	def _is_experienced_offer(self, offer):
		tags = self._normalize_tags(offer.get("tags", []))
		if any(tag.lower() == self.TAG_FILTER_VALUE.lower() for tag in tags):
			return True

		# Fallback check: some offers may not expose the tag cleanly in list payload.
		experience = str(offer.get("experience", "")).strip().lower()
		return experience == "experienced"

	def _fetch_offers(self):
		params = {
			"source": "IG",
			"HWebsite": "",
			self.TAG_FILTER_KEY: self.TAG_FILTER_VALUE,
		}

		for attempt in range(4):
			if attempt > 0:
				wait = 2 ** attempt
				print(f"Retry {attempt}/3 for listings - waiting {wait}s...")
				time.sleep(wait)

			try:
				resp = requests.get(
					self.LIST_API_URL,
					params=params,
					headers=self._headers(),
					timeout=45,
				)
				if resp.status_code == 200:
					data = resp.json()
					offers = data.get("offers", []) if isinstance(data, dict) else []
					return offers
				print(f"[warn] Listings HTTP {resp.status_code}: {resp.text[:200]}")
			except Exception as e:
				print(f"[error] Listings fetch failed: {e}")

		return []

	def _fetch_offer_detail(self, offer_id):
		url = self.DETAIL_API_URL.format(offer_id=offer_id)

		for attempt in range(3):
			if attempt > 0:
				wait = 2 ** attempt
				print(f"  Retry {attempt}/2 for offer {offer_id} - waiting {wait}s...")
				time.sleep(wait)

			try:
				resp = requests.get(url, headers=self._headers(), timeout=45)
				if resp.status_code == 200:
					data = resp.json()
					return data.get("offer", data) if isinstance(data, dict) else {}
				print(f"  [warn] Detail HTTP {resp.status_code} for offer {offer_id}")
			except Exception as e:
				print(f"  [error] Detail fetch failed for offer {offer_id}: {e}")

		return {}

	def _extract_detail_description(self, detail):
		if not detail:
			return ""

		primary_lang = detail.get("primary_lang_code") or detail.get("primaryLangCode")
		translations = detail.get("translations", {}) or {}

		candidates = []
		if primary_lang and isinstance(translations, dict):
			candidates.append(translations.get(primary_lang, {}))

		if isinstance(translations, dict):
			for lang_obj in translations.values():
				candidates.append(lang_obj)

		for obj in candidates:
			if not isinstance(obj, dict):
				continue
			desc_html = (
				obj.get("descriptionHtml")
				or obj.get("description_html")
				or obj.get("description")
			)
			req_html = (
				obj.get("requirementsHtml")
				or obj.get("requirements_html")
				or obj.get("requirements")
			)
			body = []
			if desc_html:
				body.append(self._to_text(desc_html))
			if req_html:
				body.append("Requirements:\n" + self._to_text(req_html))
			text = "\n\n".join([b for b in body if b]).strip()
			if text:
				return text

		# Generic fallback keys.
		desc_html = (
			detail.get("description")
			or detail.get("descriptionHtml")
			or detail.get("description_html")
		)
		return self._to_text(desc_html)

	def parse_job_listings(self):
		print("\nFetching IG&H jobs from Recruitee API with Experienced filter...")

		existing_index = {
			job.get("job_id"): idx
			for idx, job in enumerate(self.jobs)
			if job.get("job_id")
		}
		existing_ids = set(existing_index.keys())
		total_seen = 0
		total_new = 0
		total_updated = 0
		total_skipped_existing = 0

		offers = self._fetch_offers()
		print(f"API returned {len(offers)} offer(s) before in-code filtering")

		for offer in offers:
			total_seen += 1

			if not self._is_experienced_offer(offer):
				continue

			job_id = str(offer.get("id") or "").strip()
			if not job_id:
				continue

			if job_id in existing_ids:
				total_skipped_existing += 1
				continue

			slug = (offer.get("slug") or "").strip()
			title = (offer.get("title") or "").strip()
			city = (offer.get("city") or "").strip()
			country = (
				(offer.get("country_code") or "").strip()
				or (offer.get("country") or "").strip()
			)
			location = (offer.get("location") or "").strip()
			if not location:
				location = ", ".join([p for p in [city, country] if p])

			department = str(offer.get("department") or "").strip()
			category = str(offer.get("category") or "").strip()
			job_type = str(
				offer.get("employment_type_code")
				or offer.get("employmentType")
				or ""
			).strip()
			posted_date = (
				str(offer.get("published_at") or "").strip()
				or str(offer.get("created_at") or "").strip()
			)
			remote = bool(offer.get("remote", False))
			hybrid = bool(offer.get("hybrid", False))
			tags = self._normalize_tags(offer.get("tags", []))

			link = (offer.get("careers_url") or "").strip()
			if not link and slug:
				link = f"{self.BASE_URL}/o/{slug}"

			workplace_type = "remote" if remote else "hybrid" if hybrid else "on-site"

			job = {
				"title": title,
				"job_id": job_id,
				"job_seq_no": job_id,
				"link": link,
				"apply_link": link,
				"location": location,
				"city": city,
				"country": country,
				"job_type": job_type,
				"workplace_type": workplace_type,
				"posted_date": posted_date,
				"company": self.COMPANY,
				"category": category,
				"department": department,
				"tags": tags,
				"description": "",
				"skills": [],
				"status": "active",
				"source": "IG&H",
				"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
			}

			self.jobs.append(job)
			existing_ids.add(job_id)
			existing_index[job_id] = len(self.jobs) - 1
			total_new += 1
			print(f"  + {title[:70]} | {location[:45]} | {job_id}")

		self._save_jobs()

		print("\n" + "=" * 60)
		print(f"Offers seen        : {total_seen}")
		print(f"New jobs found     : {total_new}")
		print(f"Jobs updated       : {total_updated}")
		print(f"Existing skipped   : {total_skipped_existing}")
		print(f"Total jobs stored  : {len(self.jobs)}")
		print("=" * 60)

	def fetch_job_descriptions(self, delay=0.25):
		jobs_to_update = []
		for job in self.jobs:
			source = str(job.get("source") or "").strip().lower()
			company = str(job.get("company") or "").strip().lower()
			desc_len = len(str(job.get("description") or "").strip())
			if (source in {"igh", "ig&h"} or company == "ig&h") and desc_len < 120:
				jobs_to_update.append(job)
		if not jobs_to_update:
			print("\nNo IG&H jobs available for description refresh.")
			return

		print(f"\nRefreshing descriptions for {len(jobs_to_update)} IG&H job(s)...")
		success_count = 0
		failed_count = 0

		for i, job in enumerate(jobs_to_update, start=1):
			offer_id = str(job.get("job_id") or "").strip()
			if not offer_id:
				failed_count += 1
				continue

			print(f"  [{i}/{len(jobs_to_update)}] {offer_id} - {job.get('title', '')[:60]}")
			detail = self._fetch_offer_detail(offer_id)
			new_desc = self._extract_detail_description(detail)
			current_desc = (job.get("description") or "").strip()

			if new_desc:
				job["description"] = new_desc if len(new_desc) >= len(current_desc) else current_desc
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
		self.parse_job_listings()
		self.fetch_job_descriptions()


if __name__ == "__main__":
	print("Starting IG&H job scraper...")
	print("=" * 60)

	scraper = IGHScrapper(output_file="json_files/igh_jobs.json")
	scraper.run()

	print("\n" + "=" * 60)
	print(f"Done! Total jobs: {len(scraper.jobs)}")
	print("=" * 60)
