import json
import os
import re
import time
from datetime import datetime, timezone
from html import unescape
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup


class InternationalSOSScrapper:
	SOURCE_URL = (
		"https://opportunities.internationalsos.com/careers/search"
		"?location=Amsterdam%2CNL-UT%2CNetherlands"
		"&pid=563980767965271"
		"&domain=internationalsos.com"
		"&sort_by=relevance"
		"&location_radius_type=mi"
		"&location_distance_km=160"
		"&triggerGoButton=false"
	)

	COMPANY = "International SOS"

	# Geographically in Europe for filter fallback/use.
	EUROPE_COUNTRIES = {
		"albania", "andorra", "austria", "belarus", "belgium", "bosnia and herzegovina",
		"bulgaria", "croatia", "cyprus", "czech republic", "czechia", "denmark", "estonia",
		"finland", "france", "germany", "greece", "hungary", "iceland", "ireland", "italy",
		"kosovo", "latvia", "liechtenstein", "lithuania", "luxembourg", "malta", "moldova",
		"monaco", "montenegro", "netherlands", "north macedonia", "norway", "poland",
		"portugal", "romania", "san marino", "serbia", "slovakia", "slovenia", "spain",
		"sweden", "switzerland", "ukraine", "united kingdom", "vatican city",
	}

	def __init__(self, output_file="json_files/international_sos_jobs.json", source_url=None):
		self.output_file = output_file
		self.source_url = source_url or self.SOURCE_URL
		self.jobs = []
		self.filters = self._parse_source_filters(self.source_url)
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
			"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
			"User-Agent": (
				"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
				"AppleWebKit/537.36 (KHTML, like Gecko) "
				"Chrome/124.0.0.0 Safari/537.36"
			),
		}

	@staticmethod
	def _parse_source_filters(source_url):
		parsed = urlparse(source_url)
		qs = parse_qs(parsed.query)
		location_raw = (qs.get("location", [""]) or [""])[0].strip()
		parts = [x.strip() for x in location_raw.split(",") if x.strip()]

		city = parts[0] if parts else ""
		country = parts[-1] if len(parts) >= 2 else ""

		return {
			"location_raw": location_raw,
			"city": city,
			"country": country,
		}

	def _fetch_source_html(self):
		for attempt in range(4):
			if attempt > 0:
				wait = 2 ** attempt
				print(f"Retry {attempt}/3 for source page - waiting {wait}s...")
				time.sleep(wait)
			try:
				resp = requests.get(self.source_url, headers=self._headers(), timeout=45)
				if resp.status_code == 200:
					return resp.text
				print(f"[warn] Source page HTTP {resp.status_code}: {resp.text[:200]}")
			except Exception as e:
				print(f"[error] Source page fetch failed: {e}")

		return ""

	@staticmethod
	def _extract_smart_apply_data(html):
		if not html:
			return {}

		soup = BeautifulSoup(html, "html.parser")
		code = soup.find("code", id="smartApplyData")
		if not code:
			return {}

		raw = code.get_text(strip=True)
		if not raw:
			return {}

		try:
			return json.loads(unescape(raw))
		except Exception:
			return {}

	@staticmethod
	def _extract_country_from_location(location_text):
		if not location_text:
			return ""
		parts = [p.strip() for p in location_text.split(",") if p.strip()]
		return parts[-1] if parts else ""

	def _is_europe_country(self, country):
		return (country or "").strip().lower() in self.EUROPE_COUNTRIES

	def _passes_filters(self, position):
		location = (position.get("location") or "").strip()
		locations = [str(x).strip() for x in (position.get("locations") or []) if str(x).strip()]
		all_locations = [location] + locations

		source_city = (self.filters.get("city") or "").strip().lower()
		source_country = (self.filters.get("country") or "").strip().lower()

		in_source_city = False
		if source_city:
			in_source_city = any(source_city in loc.lower() for loc in all_locations)

		countries = [self._extract_country_from_location(loc) for loc in all_locations]
		countries = [c for c in countries if c]
		in_source_country = any(c.lower() == source_country for c in countries) if source_country else False
		in_europe = any(self._is_europe_country(c) for c in countries)

		# Primary goal: Amsterdam filter (source city). Also ensure Europe-only.
		if source_city and in_source_city and in_europe:
			return True

		# Fallback: source country match in Europe.
		if source_country and in_source_country and in_europe:
			return True

		# Final fallback requested by user: countries within Europe only.
		return in_europe

	@staticmethod
	def _parse_city_country(location_text):
		if not location_text:
			return "", ""
		parts = [p.strip() for p in location_text.split(",") if p.strip()]
		if not parts:
			return "", ""
		city = parts[0]
		country = parts[-1]
		return city, country

	@staticmethod
	def _extract_description_from_job_html(html):
		if not html:
			return ""

		soup = BeautifulSoup(html, "html.parser")

		for tag in soup.find_all("script", type="application/ld+json"):
			text = (tag.string or tag.get_text() or "").strip()
			if not text:
				continue
			try:
				data = json.loads(text)
				candidates = data if isinstance(data, list) else [data]
				for item in candidates:
					if isinstance(item, dict) and item.get("@type") == "JobPosting":
						desc = (item.get("description") or "").strip()
						if desc:
							return re.sub(r"\s+", " ", unescape(desc)).strip()
			except Exception:
				continue

		og = soup.find("meta", property="og:description")
		if og and og.get("content"):
			return re.sub(r"\s+", " ", unescape(og.get("content", ""))).strip()

		return ""

	def _fetch_job_description(self, url):
		if not url:
			return ""

		for attempt in range(3):
			if attempt > 0:
				time.sleep(2 ** attempt)
			try:
				resp = requests.get(url, headers=self._headers(), timeout=45)
				if resp.status_code == 200:
					return self._extract_description_from_job_html(resp.text)
			except Exception:
				pass

		return ""

	def parse_job_listings(self):
		print("\nFetching International SOS jobs...")
		print(f"Source URL city filter   : {self.filters.get('city')}")
		print(f"Source URL country filter: {self.filters.get('country')}")

		html = self._fetch_source_html()
		data = self._extract_smart_apply_data(html)
		positions = data.get("positions", []) if isinstance(data, dict) else []

		if not positions:
			print("No positions found in smartApplyData.")
			return

		print(f"Total positions found in page payload: {len(positions)}")

		existing_ids = self._get_existing_job_ids()
		total_new = 0
		total_skipped = 0
		total_filtered_out = 0

		for p in positions:
			if not self._passes_filters(p):
				total_filtered_out += 1
				continue

			job_id = str(p.get("id", "")).strip()
			if not job_id or job_id in existing_ids:
				total_skipped += 1
				continue

			title = (p.get("name") or p.get("posting_name") or "").strip()
			location = (p.get("location") or "").strip()
			city, country = self._parse_city_country(location)
			category = (p.get("department") or "").strip()
			business_unit = (p.get("business_unit") or "").strip()
			workplace_type = (p.get("work_location_option") or "").strip()

			job_url = (p.get("canonicalPositionUrl") or "").strip()
			if job_url and job_url.startswith("/"):
				job_url = "https://opportunities.internationalsos.com" + job_url

			description = self._fetch_job_description(job_url)

			created_epoch = p.get("t_create")
			posted_date = ""
			if isinstance(created_epoch, int):
				posted_date = datetime.fromtimestamp(created_epoch, tz=timezone.utc).strftime("%Y-%m-%d")

			job = {
				"title": title,
				"job_id": job_id,
				"job_seq_no": job_id,
				"link": job_url,
				"location": location,
				"city": city,
				"country": country,
				"job_type": "",
				"workplace_type": workplace_type,
				"posted_date": posted_date,
				"company": self.COMPANY,
				"category": category,
				"department": business_unit or category,
				"description": description,
				"skills": [],
				"status": "active",
				"source": "internationalsos",
				"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
			}

			self.jobs.append(job)
			existing_ids.add(job_id)
			total_new += 1
			print(f"  + {title[:70]} | {location[:50]} | {job_id}")

		self._save_jobs()

		print("\n" + "=" * 60)
		print(f"Filtered out      : {total_filtered_out}")
		print(f"Duplicates skipped: {total_skipped}")
		print(f"New jobs found    : {total_new}")
		print(f"Total jobs stored : {len(self.jobs)}")
		print("=" * 60)

	def run(self):
		self.parse_job_listings()


if __name__ == "__main__":
	print("Starting International SOS scraper...")
	print("=" * 60)

	scraper = InternationalSOSScrapper()
	scraper.run()

	print("\n" + "=" * 60)
	print(f"Done! Total jobs: {len(scraper.jobs)}")
	print("=" * 60)
