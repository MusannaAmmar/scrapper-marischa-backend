import json
import os
import re
import time
from datetime import datetime, timezone
from html import unescape

import requests
from bs4 import BeautifulSoup


class SandayScrapper:
	"""
	Scrapes Sanday jobs from Recruitee.

	Primary source:
	  - https://sanday.recruitee.com/api/offers/

	Fallback source:
	  - Parse embedded offers from local sanday_jobs.html snapshot.
	"""

	COMPANY = "Sanday"
	CAREERS_URL = "https://werkenbij.sanday.com"
	API_BASES = [
		"https://sanday.recruitee.com",
		"https://werkenbij.sanday.com",
	]

	def __init__(self, output_file="json_files/sanday_jobs.json", html_snapshot="sanday_jobs.html"):
		self.output_file = output_file
		self.html_snapshot = html_snapshot
		self.jobs = []
		self.new_job_ids = set()
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
		soup = BeautifulSoup(str(html), "html.parser")
		text = soup.get_text(separator="\n", strip=True)
		return re.sub(r"\n{3,}", "\n\n", text).strip()

	def _request_json(self, url, timeout=45, attempts=3):
		for attempt in range(attempts):
			if attempt > 0:
				wait = 2 ** attempt
				print(f"  Retry {attempt}/{attempts - 1} - waiting {wait}s")
				time.sleep(wait)
			try:
				resp = requests.get(url, headers=self._headers(), timeout=timeout)
				if resp.status_code == 200:
					return resp.json()
				print(f"  [warn] HTTP {resp.status_code}: {url}")
			except Exception as exc:
				print(f"  [error] request failed for {url}: {exc}")
		return None

	def _fetch_offers_api(self):
		for base in self.API_BASES:
			url = f"{base}/api/offers/"
			print(f"Trying listings API: {url}")
			data = self._request_json(url)
			if isinstance(data, dict) and isinstance(data.get("offers"), list):
				return base, data.get("offers", [])
		return None, []

	def _fetch_offer_detail(self, api_base, offer_id):
		if not api_base:
			return {}
		url = f"{api_base}/api/offers/{offer_id}"
		data = self._request_json(url, attempts=2)
		if isinstance(data, dict):
			return data.get("offer", data)
		return {}

	@staticmethod
	def _extract_embedded_props(html_text):
		soup = BeautifulSoup(html_text, "html.parser")
		app_div = soup.find("div", attrs={"data-component": "PublicApp"})
		if not app_div:
			return {}

		raw = app_div.get("data-props", "")
		if not raw:
			return {}

		try:
			return json.loads(unescape(raw))
		except Exception:
			return {}

	@staticmethod
	def _find_offers_in_obj(obj):
		"""Recursively find the first plausible offers list in nested data."""
		if isinstance(obj, dict):
			offers = obj.get("offers")
			if isinstance(offers, list):
				valid = [
					item for item in offers
					if isinstance(item, dict) and item.get("id") and item.get("title")
				]
				if valid:
					return valid
			for value in obj.values():
				found = SandayScrapper._find_offers_in_obj(value)
				if found:
					return found

		if isinstance(obj, list):
			for value in obj:
				found = SandayScrapper._find_offers_in_obj(value)
				if found:
					return found
		return []

	def _fetch_offers_from_snapshot(self):
		if not os.path.exists(self.html_snapshot):
			return []

		with open(self.html_snapshot, "r", encoding="utf-8") as f:
			html_text = f.read()

		props = self._extract_embedded_props(html_text)
		offers = self._find_offers_in_obj(props)
		return offers or []

	def _extract_description(self, detail):
		if not detail:
			return ""

		primary_lang = detail.get("primary_lang_code") or detail.get("primaryLangCode")
		translations = detail.get("translations") or {}

		candidates = []
		if isinstance(translations, dict) and primary_lang in translations:
			candidates.append(translations.get(primary_lang, {}))
		if isinstance(translations, dict):
			candidates.extend(translations.values())

		for obj in candidates:
			if not isinstance(obj, dict):
				continue
			desc_html = obj.get("descriptionHtml") or obj.get("description")
			req_html = obj.get("requirementsHtml") or obj.get("requirements")
			parts = []
			if desc_html:
				parts.append(self._to_text(desc_html))
			if req_html:
				parts.append("Requirements:\n" + self._to_text(req_html))
			text = "\n\n".join([p for p in parts if p]).strip()
			if text:
				return text

		generic_desc = detail.get("description") or detail.get("descriptionHtml")
		return self._to_text(generic_desc)

	def parse_job_listings(self):
		existing_index = {
			str(job.get("job_id")): idx
			for idx, job in enumerate(self.jobs)
			if job.get("job_id")
		}

		api_base, offers = self._fetch_offers_api()
		source_mode = "api"
		if not offers:
			print("Listings API unavailable; falling back to embedded HTML offers")
			offers = self._fetch_offers_from_snapshot()
			source_mode = "html"

		if not offers:
			print("No Sanday offers found from API or snapshot")
			return None

		print(f"Processing {len(offers)} offers from {source_mode}")
		self.new_job_ids = set()
		new_count = 0
		updated_count = 0
		skipped_existing = 0

		for offer in offers:
			status = str(offer.get("status") or "").strip().lower()
			if status and status != "published":
				continue

			job_id = str(offer.get("id") or "").strip()
			if not job_id:
				continue

			title = str(offer.get("title") or "").strip()
			slug = str(offer.get("slug") or "").strip()
			city = str(offer.get("city") or "").strip()
			country = str(offer.get("country") or offer.get("country_code") or "").strip()
			location = str(offer.get("location") or "").strip()
			if not location:
				location = ", ".join([part for part in [city, country] if part])

			department = str(offer.get("department") or "").strip()
			category = str(offer.get("category") or "").strip()
			employment_type = str(offer.get("employment_type_code") or offer.get("employmentType") or "").strip()
			posted_date = str(offer.get("published_at") or offer.get("created_at") or "").strip()

			remote = bool(offer.get("remote", False))
			hybrid = bool(offer.get("hybrid", False))
			workplace_type = "remote" if remote else "hybrid" if hybrid else "on-site"

			link = str(offer.get("careers_url") or "").strip()
			if not link and slug:
				link = f"{self.CAREERS_URL}/o/{slug}"

			job = {
				"title": title,
				"job_id": job_id,
				"job_seq_no": str(offer.get("guid") or job_id),
				"link": link,
				"apply_link": link,
				"location": location,
				"city": city,
				"country": country,
				"job_type": employment_type,
				"workplace_type": workplace_type,
				"posted_date": posted_date,
				"company": self.COMPANY,
				"category": category,
				"department": department,
				"description": "",
				# "skills": [],
				"status": "active",
				"source": "Sanday",
				"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
			}

			if job_id in existing_index:
				skipped_existing += 1
				continue

			self.jobs.append(job)
			existing_index[job_id] = len(self.jobs) - 1
			self.new_job_ids.add(job_id)
			new_count += 1

		self._save_jobs()
		print(
			f"New jobs: {new_count} | Updated jobs: {updated_count} | "
			f"Skipped existing: {skipped_existing} | Total stored: {len(self.jobs)}"
		)
		return api_base

	def fetch_job_descriptions(self, api_base, delay=0.2):
		if not api_base:
			print("Skipping description refresh because API base is unavailable")
			return

		targets = [
			job for job in self.jobs
			if str(job.get("source") or "").strip().lower() == "sanday"
			and str(job.get("job_id") or "") in self.new_job_ids
			and len(str(job.get("description") or "").strip()) < 120
		]
		if not targets:
			print("No Sanday jobs found for description refresh")
			return

		ok = 0
		failed = 0
		for index, job in enumerate(targets, start=1):
			offer_id = str(job.get("job_id") or "").strip()
			if not offer_id:
				failed += 1
				continue

			print(f"[{index}/{len(targets)}] Fetching details for {offer_id}")
			detail = self._fetch_offer_detail(api_base, offer_id)
			description = self._extract_description(detail)

			if description:
				previous = str(job.get("description") or "")
				if not previous or len(description) >= len(previous):
					job["description"] = description
				ok += 1
			else:
				failed += 1

			if index % 15 == 0:
				self._save_jobs()

			time.sleep(delay)

		self._save_jobs()
		print(f"Descriptions refreshed: {ok} success, {failed} without description")

	def run(self):
		api_base = self.parse_job_listings()
		self.fetch_job_descriptions(api_base=api_base, delay=0.2)
		print(f"Done. Output written to {self.output_file}")


if __name__ == "__main__":
	scrapper = SandayScrapper()
	scrapper.run()
