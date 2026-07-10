import json
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup


class NovoNordiskScraper:
	"""
	Scrapes Novo Nordisk job listings.

	Source page : https://www.novonordisk.com/careers/find-a-job/career-search-results.html
	API         : GET /bin/nncorp/careersearch
	Details     : job-ad pages rendered server-side

	Notes:
	  - Job list endpoint discovered from the careers page client JS bundle.
	  - Descriptions and apply links are extracted from each job-ad HTML page.
	"""

	COMPANY = "Novo Nordisk"
	BASE_URL = "https://www.novonordisk.com"
	LIST_API = "https://www.novonordisk.com/bin/nncorp/careersearch"
	JOB_AD_PATH = "/content/nncorp/global/en/careers/find-a-job/job-ad.{job_id}.html"
	SOURCE_URL = (
		"https://www.novonordisk.com/careers/find-a-job/career-search-results.html"
		"?searchText=&countries=Denmark%3BUnited+States%3BChina+Mainland%3BAustralia%3BAustria%3B"
		"Belgium%3BBrazil%3BCanada%3BChile%3BCzech+Republic%3BFrance%3BGermany%3BGreece%3B"
		"Hong+Kong%3BHungary%3BIndia%3BIsrael%3BJapan%3BMalaysia%3BMexico%3BNetherlands%3B"
		"Pakistan%3BPoland%3BSaudi+Arabia%3BSingapore%3BSouth+Africa%3BSouth+Korea%3B"
		"Switzerland%3BTaiwan%3BTurkey%3BUnited+Kingdom%3BVietnam"
		"&categories=Business+Support+%26+Administration%3BClinical+Development%3B"
		"Commercial+Marketing%3BData+%26+AI%3BDigital+%26+IT%3BEducation%3BEngineering+%26+"
		"Technical%3BFinance%3BHuman+Resource+Management%3BLegal%2C+Compliance+%26+Audit%3B"
		"Manufacturing%3BProject+Management+%26+Agile%3BQuality%3BReg+Affairs+%26+Safety+"
		"Pharmacovigilance%3BResearch%3BBusiness+Development+%26+Strategy%3BMarket+Access%3B"
		"Medical+Affairs%3BProcurement%3BSales%3BSupply+Chain%3BCorporate+Affairs"
		"&locations=Budapest%3BEdinburgh%3BEindhoven%3BLondon%3BParis%3BPetersburg%3BPrague%3B"
		"Zurich%3BAthens%3BBrussels%3BBrest"
	)

	def __init__(self, output_file="json_files/novonordisk_jobs.json"):
		self.output_file = output_file
		self.jobs = []
		self.source_filters = self._parse_source_filters(self.SOURCE_URL)
		self.default_search_text = self.source_filters["search_text"]
		self.default_countries = self.source_filters["countries_raw"]
		self.default_categories = self.source_filters["categories_raw"]
		self.default_locations = set(self.source_filters["locations_list"])
		self.default_countries_set = set(self.source_filters["countries_list"])
		self.default_categories_set = set(self.source_filters["categories_list"])
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
	# HTTP helpers
	# ------------------------------------------------------------------ #

	@staticmethod
	def _parse_source_filters(source_url):
		parsed = urlparse(source_url)
		qs = parse_qs(parsed.query)

		search_text = (qs.get("searchText", [""]) or [""])[0].strip()
		countries_raw = (qs.get("countries", [""]) or [""])[0].strip()
		categories_raw = (qs.get("categories", [""]) or [""])[0].strip()
		locations_raw = (qs.get("locations", [""]) or [""])[0].strip()

		countries_list = [x.strip() for x in countries_raw.split(";") if x.strip()]
		categories_list = [x.strip() for x in categories_raw.split(";") if x.strip()]
		locations_list = [x.strip() for x in locations_raw.split(";") if x.strip()]

		return {
			"search_text": search_text,
			"countries_raw": countries_raw,
			"categories_raw": categories_raw,
			"locations_raw": locations_raw,
			"countries_list": countries_list,
			"categories_list": categories_list,
			"locations_list": locations_list,
		}

	@staticmethod
	def _headers():
		return {
			"Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
			"User-Agent": (
				"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
				"AppleWebKit/537.36 (KHTML, like Gecko) "
				"Chrome/122.0.0.0 Safari/537.36"
			),
		}

	def _fetch_jobs(self, keyword="", country="", category="", locale="en"):
		params = {
			"keyword": keyword,
			"country": country,
			"category": category,
			"locale": locale,
		}

		for attempt in range(4):
			if attempt > 0:
				wait = 2 ** attempt
				print(f"Retry {attempt}/3 for list API - waiting {wait}s...")
				time.sleep(wait)
			try:
				resp = requests.get(
					self.LIST_API,
					params=params,
					headers=self._headers(),
					timeout=45,
				)
				if resp.status_code == 200:
					return resp.json()
				print(f"[warn] List API HTTP {resp.status_code}: {resp.text[:200]}")
			except Exception as e:
				print(f"[error] List API failed: {e}")

		return None

	def _fetch_job_page(self, job_id):
		url = f"{self.BASE_URL}{self.JOB_AD_PATH.format(job_id=job_id)}"

		for attempt in range(4):
			if attempt > 0:
				wait = 2 ** attempt
				print(f"  Retry {attempt}/3 for {job_id} - waiting {wait}s...")
				time.sleep(wait)
			try:
				resp = requests.get(url, headers=self._headers(), timeout=60)
				if resp.status_code == 200:
					return resp.text
				print(f"  [warn] Job page HTTP {resp.status_code} for {job_id}")
			except Exception as e:
				print(f"  [error] Job page fetch failed ({job_id}): {e}")

		return ""

	# ------------------------------------------------------------------ #
	# Parsing helpers
	# ------------------------------------------------------------------ #

	@staticmethod
	def _split_location(location_label):
		if not location_label:
			return "", ""

		parts = [p.strip() for p in location_label.split(",") if p.strip()]
		if len(parts) >= 2:
			city = ", ".join(parts[:-1])
			country = parts[-1]
			return city, country
		return location_label.strip(), ""

	@staticmethod
	def _extract_description_from_job_page(html):
		soup = BeautifulSoup(html, "html.parser")

		# Primary description block on the job ad page.
		details = soup.select_one(".job-display-details")
		if details:
			text = details.get_text("\n", strip=True)
			lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
			lines = [ln for ln in lines if ln]
			return "\n".join(lines).strip()

		# Fallback: OpenGraph description if detail block is missing.
		og = soup.find("meta", property="og:description")
		if og and og.get("content"):
			return og.get("content", "").strip()

		return ""

	@staticmethod
	def _extract_apply_link_from_job_page(html):
		soup = BeautifulSoup(html, "html.parser")
		a_tag = soup.select_one('a[href*="career2.successfactors.eu/career"]')
		return a_tag.get("href", "").strip() if a_tag else ""

	# ------------------------------------------------------------------ #
	# Main scraping steps
	# ------------------------------------------------------------------ #

	def parse_job_listings(self, locale="en"):
		print("\nFetching Novo Nordisk jobs from API...")
		print("Using source URL filters:")
		print(f"  countries : {len(self.default_countries_set)}")
		print(f"  categories: {len(self.default_categories_set)}")
		print(f"  locations : {len(self.default_locations)}")

		data = self._fetch_jobs(
			keyword=self.default_search_text,
			country=self.default_countries,
			category=self.default_categories,
			locale=locale,
		)
		if not data:
			print("Failed to fetch jobs from list API.")
			return

		status = data.get("status")
		payload = data.get("data", {}) if isinstance(data, dict) else {}
		postings = payload.get("jobs", [])

		if status != 200:
			print(f"Unexpected list API status in payload: {status}")

		print(f"Total jobs returned by API: {len(postings)}")

		existing_ids = self._get_existing_job_ids()
		total_new = 0

		for posting in postings:
			country_label = (posting.get("jobCountry") or {}).get("label", "").strip()
			category_label = (posting.get("jobCategory") or {}).get("label", "").strip()
			city_label = (posting.get("jobCity") or {}).get("label", "").strip()

			# Defensive filtering so we strictly match the source URL filters.
			if self.default_countries_set and country_label not in self.default_countries_set:
				continue
			if self.default_categories_set and category_label not in self.default_categories_set:
				continue
			if self.default_locations and city_label and city_label not in self.default_locations:
				continue

			job_id = posting.get("jobId", "").strip()
			if not job_id or job_id in existing_ids:
				continue

			title = posting.get("jobTitle", "").strip()
			location = posting.get("jobLocationLabel", "").strip()
			city = (posting.get("jobCity") or {}).get("label", "").strip()
			country = (posting.get("jobCountry") or {}).get("label", "").strip()
			category = (posting.get("jobCategory") or {}).get("label", "").strip()
			subcategory = (posting.get("jobSubCategory") or {}).get("label", "").strip()
			state = (posting.get("jobState") or {}).get("label", "").strip()

			if not city or not country:
				parsed_city, parsed_country = self._split_location(location)
				city = city or parsed_city
				country = country or parsed_country

			job_url = f"{self.BASE_URL}{self.JOB_AD_PATH.format(job_id=job_id)}"

			job = {
				"title": title,
				"job_id": job_id,
				"link": job_url,
				"location": location,
				"city": city,
				"country": country,
				"state": state,
				"job_type": "",
				"remote": "Yes" if re.search(r"\bremote\b", f"{title} {location}", re.I) else "",
				"posted_date": "",
				"salary": "",
				"company": self.COMPANY,
				"category": category,
				"department": subcategory or category,
				"description": "",
				"description_fetched": False,
				"apply_link": "",
				"skills": [],
				"status": "active",
				"source": "Novo Nordisk",
				"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
			}

			self.jobs.append(job)
			existing_ids.add(job_id)
			total_new += 1
			print(f"  + {title[:70]} | {location[:50]}")

		self._save_jobs()
		print("\n" + "=" * 60)
		print(f"New jobs found    : {total_new}")
		print(f"Total jobs stored : {len(self.jobs)}")
		print("=" * 60)

	def fetch_job_descriptions(self, delay=0.6):
		jobs_to_update = [j for j in self.jobs if not j.get("description_fetched", False)]
		if not jobs_to_update:
			print("\nAll jobs already have descriptions.")
			return

		print(f"\nFetching descriptions for {len(jobs_to_update)} jobs...")
		success_count = 0
		failed_count = 0

		for i, job in enumerate(jobs_to_update):
			job_id = job.get("job_id", "")
			title = job.get("title", "")
			print(f"\n  [{i + 1}/{len(jobs_to_update)}] {title[:65]} ({job_id})")

			html = self._fetch_job_page(job_id) if job_id else ""
			description = self._extract_description_from_job_page(html) if html else ""
			apply_link = self._extract_apply_link_from_job_page(html) if html else ""

			job["description"] = description
			job["apply_link"] = apply_link
			job["description_fetched"] = True

			if description:
				success_count += 1
				print(f"    Description: {len(description.split())} words")
			else:
				failed_count += 1
				print("    [warn] No description found")

			self._save_jobs()
			time.sleep(delay)

		print("\n" + "=" * 60)
		print(f"Description Success : {success_count}")
		print(f"Description Failed  : {failed_count}")
		print("=" * 60)

	def run(self, fetch_descriptions=True, locale="en"):
		self.parse_job_listings(locale=locale)
		if fetch_descriptions:
			self.fetch_job_descriptions()


if __name__ == "__main__":
	scraper = NovoNordiskScraper(output_file="json_files/novonordisk_jobs.json")
	scraper.run(fetch_descriptions=True, locale="en")
