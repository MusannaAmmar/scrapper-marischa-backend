import html
import json
import os
import re
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup


class LogexScrapper:
	"""
	Scrapes LOGEX jobs from Recruitee.

	Primary source:
	  https://logex.recruitee.com/api/offers/

	Fallback source:
	  logex.html -> PublicApp data-props payload (contains offers list)
	"""

	BASE_URL = "https://logex.recruitee.com"
	LIST_API_URL = f"{BASE_URL}/api/offers/"
	DETAIL_API_URL = f"{BASE_URL}/api/offers/{{offer_id}}"
	COMPANY = "LOGEX"

	def __init__(self, output_file="json_files/logex_jobs.json", html_file="logex.html"):
		self.output_file = output_file
		self.html_file = html_file
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
				"Chrome/124.0.0.0 Safari/537.36"
			),
		}

	@staticmethod
	def _clean_text(value):
		if value is None:
			return ""
		return re.sub(r"\s+", " ", str(value)).strip()

	@staticmethod
	def _html_to_text(raw_html):
		if not raw_html:
			return ""
		soup = BeautifulSoup(raw_html, "html.parser")
		text = soup.get_text(separator="\n", strip=True)
		return re.sub(r"\n{3,}", "\n\n", text).strip()

	@staticmethod
	def _normalize_tags(tags):
		if not tags:
			return []
		normalized = []
		for item in tags:
			if isinstance(item, str):
				value = item.strip()
				if value:
					normalized.append(value)
			elif isinstance(item, dict):
				value = (
					item.get("name")
					or item.get("title")
					or item.get("slug")
					or ""
				)
				value = str(value).strip()
				if value:
					normalized.append(value)
		return normalized

	def _fetch_json(self, url):
		for attempt in range(3):
			if attempt > 0:
				time.sleep(2 ** attempt)
			try:
				resp = requests.get(url, headers=self._headers(), timeout=45)
				if resp.status_code == 200:
					return resp.json()
				print(f"  [warn] HTTP {resp.status_code} for {url}")
			except Exception as e:
				print(f"  [warn] Request failed for {url}: {e}")
		return None

	def _extract_offers_from_html_payload(self):
		if not os.path.exists(self.html_file):
			return []

		with open(self.html_file, "r", encoding="utf-8") as f:
			html_content = f.read()

		soup = BeautifulSoup(html_content, "html.parser")
		app_node = soup.find("div", attrs={"data-component": "PublicApp"})
		if not app_node:
			return []

		props_attr = app_node.get("data-props")
		if not props_attr:
			return []

		try:
			props_json = json.loads(html.unescape(props_attr))
		except Exception as e:
			print(f"  [warn] Could not parse data-props JSON: {e}")
			return []

		return (props_json.get("appConfig") or {}).get("offers") or []

	def _build_job_from_offer(self, offer):
		offer_id = str(offer.get("id") or "").strip()
		if not offer_id:
			return None

		slug = str(offer.get("slug") or "").strip()
		link = f"{self.BASE_URL}/o/{slug}" if slug else f"{self.BASE_URL}/o/{offer_id}"

		country_code = self._clean_text(offer.get("country_code") or offer.get("countryCode"))
		city = self._clean_text(offer.get("city"))
		location = self._clean_text(offer.get("location"))
		if not location:
			location = ", ".join([p for p in [city, country_code] if p])

		department = self._clean_text(offer.get("department"))
		category = self._clean_text(offer.get("category"))
		employment_type = self._clean_text(
			offer.get("employment_type_code") or offer.get("employmentType")
		)

		tags = self._normalize_tags(offer.get("tags", []))
		remote = bool(offer.get("remote", False))
		hybrid = bool(offer.get("hybrid", False))
		workplace_type = "remote" if remote else "hybrid" if hybrid else "on-site"

		posted_date = self._clean_text(offer.get("published_at") or offer.get("created_at"))

		return {
			"title": self._clean_text(offer.get("title")),
			"job_id": offer_id,
			"job_seq_no": offer_id,
			"link": link,
			"apply_link": link,
			"location": location,
			"city": city,
			"country": country_code,
			"job_type": employment_type,
			"workplace_type": workplace_type,
			"posted_date": posted_date,
			"salary": "",
			"company": self.COMPANY,
			"category": category,
			"department": department,
			"tags": tags,
			"description": "",
			"description_fetched": False,
			"skills": [],
			"status": "active",
			"source": "LOGEX",
			"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
		}

	def parse_job_listings(self):
		print("Fetching LOGEX job listings...")

		existing_index = {
			str(job.get("job_id")): idx
			for idx, job in enumerate(self.jobs)
			if job.get("job_id")
		}
		existing_ids = set(existing_index.keys())

		data = self._fetch_json(self.LIST_API_URL)
		offers = []
		if data and isinstance(data, dict) and isinstance(data.get("offers"), list):
			offers = data.get("offers", [])
			print(f"  API offers: {len(offers)}")
		else:
			print("  Recruitee API unavailable, using HTML payload fallback...")
			offers = self._extract_offers_from_html_payload()
			print(f"  HTML payload offers: {len(offers)}")

		if not offers:
			print("  No offers found.")
			return

		new_count = 0
		updated_count = 0
		seen_ids = set()

		for offer in offers:
			job = self._build_job_from_offer(offer)
			if not job:
				continue

			job_id = str(job.get("job_id"))
			seen_ids.add(job_id)

			if job_id in existing_ids:
				idx = existing_index[job_id]
				existing_job = self.jobs[idx]
				if str(existing_job.get("description") or "").strip():
					job["description"] = existing_job.get("description")
				if existing_job.get("description_fetched"):
					job["description_fetched"] = True
				self.jobs[idx] = {**existing_job, **job}
				updated_count += 1
			else:
				self.jobs.append(job)
				existing_ids.add(job_id)
				existing_index[job_id] = len(self.jobs) - 1
				new_count += 1

		# Keep historical entries but mark no-longer-listed LOGEX jobs as expired.
		expired_count = 0
		for job in self.jobs:
			if str(job.get("source") or "").strip().lower() != "logex":
				continue
			jid = str(job.get("job_id") or "")
			if jid and jid not in seen_ids:
				job["status"] = "expired"
				expired_count += 1

		self._save_jobs()
		print(
			f"Found {new_count} new jobs, updated {updated_count}, marked {expired_count} expired. "
			f"Total: {len(self.jobs)} jobs."
		)

	def _fetch_offer_detail(self, offer_id):
		url = self.DETAIL_API_URL.format(offer_id=offer_id)
		data = self._fetch_json(url)
		if isinstance(data, dict):
			return data.get("offer", data)
		return {}

	def _extract_description_from_detail(self, detail):
		if not detail:
			return ""

		translations = detail.get("translations") or {}
		primary_lang = detail.get("primary_lang_code") or detail.get("primaryLangCode")

		lang_candidates = []
		if primary_lang and primary_lang in translations:
			lang_candidates.append(translations[primary_lang])
		if isinstance(translations, dict):
			lang_candidates.extend(list(translations.values()))

		for lang_obj in lang_candidates:
			if not isinstance(lang_obj, dict):
				continue
			description = self._html_to_text(lang_obj.get("descriptionHtml") or lang_obj.get("description"))
			requirements = self._html_to_text(lang_obj.get("requirementsHtml") or lang_obj.get("requirements"))
			parts = []
			if description:
				parts.append(description)
			if requirements:
				parts.append("Requirements:\n" + requirements)
			if parts:
				return "\n\n".join(parts).strip()

		fallback = self._html_to_text(
			detail.get("description") or detail.get("descriptionHtml") or ""
		)
		return fallback

	def fetch_job_descriptions(self, delay=0.2):
		jobs_to_update = [
			job
			for job in self.jobs
			if str(job.get("source") or "").strip().lower() == "logex"
			and str(job.get("status") or "").strip().lower() == "active"
			and not job.get("description_fetched", False)
		]

		if not jobs_to_update:
			print("All active LOGEX jobs already have descriptions.")
			return

		print(f"Fetching descriptions for {len(jobs_to_update)} LOGEX job(s)...")
		success_count = 0
		failed_count = 0

		for i, job in enumerate(jobs_to_update, start=1):
			offer_id = str(job.get("job_id") or "")
			print(f"[{i}/{len(jobs_to_update)}] {job.get('title', '')[:80]}")

			detail = self._fetch_offer_detail(offer_id)
			description = self._extract_description_from_detail(detail)

			if description and len(description) >= 60:
				job["description"] = description
				success_count += 1
				print(f"  + Description fetched ({len(description.split())} words)")
			else:
				failed_count += 1
				print("  [warn] Description missing or too short")

			job["description_fetched"] = True
			self._save_jobs()
			time.sleep(delay)

		print(f"Description refresh done. Success: {success_count}, Failed: {failed_count}")

	def run(self):
		self.parse_job_listings()
		self.fetch_job_descriptions()


if __name__ == "__main__":
	scrapper = LogexScrapper()
	scrapper.run()
