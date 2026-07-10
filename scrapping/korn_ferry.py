import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


class KornFerryScrapper:
	"""
	Scrapes Korn Ferry jobs from the filtered "Experienced Opportunities" board.

	Only jobs from the requested countries are kept:
	- United Kingdom
	- Poland
	- Netherlands
	"""

	SOURCE_URL = (
		"https://kornferry.tal.net/vx/lang-en-GB/mobile-0/appcentre-ext/brand-4/"
		"xf-9ddf4301e915/candidate/jobboard/vacancy/3/adv/?"
		"f_Item_Opportunity_133305_lk=1822&"
		"f_Item_Opportunity_133305_lk=1768&"
		"f_Item_Opportunity_133305_lk=1747"
	)
	COMPANY = "Korn Ferry"
	SOURCE = "Korn Ferry"
	ALLOWED_COUNTRIES = {"united kingdom", "poland", "netherlands"}

	def __init__(self, output_file="json_files/korn_ferry_jobs.json"):
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
				"Chrome/124.0.0.0 Safari/537.36"
			),
		}

	def _fetch_html(self, url):
		resp = requests.get(url, headers=self._headers(), timeout=60)
		resp.raise_for_status()
		return resp.text

	@staticmethod
	def _extract_job_rows(html):
		soup = BeautifulSoup(html, "html.parser")
		rows = soup.select("table.solr_search_list tbody tr.details_row")
		return rows

	@staticmethod
	def _country_from_row(row):
		tds = row.find_all("td", class_="comm_list_tbody")
		if len(tds) < 2:
			return ""
		return tds[1].get_text(" ", strip=True)

	@staticmethod
	def _extract_city(title):
		# Best-effort city extraction from title patterns like:
		# "..., London" or "..., Warsaw (Poland)"
		clean_title = re.sub(r"\s+", " ", title or "").strip()

		match = re.search(r",\s*([A-Za-z\- ]+?)(?:\s*\(|\s*$)", clean_title)
		if match:
			return match.group(1).strip()

		match = re.search(r"\(([^()]+)\)", clean_title)
		if match:
			inside = match.group(1).strip()
			if inside.lower() not in {"poland", "netherlands", "united kingdom", "uk", "pl"}:
				return inside

		return ""

	@staticmethod
	def _extract_field_from_description(description, label):
		if not description:
			return ""

		lines = [line.strip() for line in description.splitlines() if line.strip()]
		label_l = label.strip().lower()

		for idx, line in enumerate(lines):
			if line.lower() == label_l and idx + 1 < len(lines):
				value = lines[idx + 1].strip()
				if value and value.lower() not in {"about us", "job description"}:
					return value

		return ""

	@staticmethod
	def _extract_job_id(row, link):
		job_id = str(row.get("data-oppid") or "").strip()
		if job_id:
			return job_id

		match = re.search(r"/opp/(\d+)-", link or "")
		if match:
			return match.group(1)

		return ""

	def _extract_description(self, job_link):
		if not job_link:
			return ""

		try:
			html = self._fetch_html(job_link)
		except Exception:
			return ""

		soup = BeautifulSoup(html, "html.parser")

		candidates = [
			soup.select_one("#main-content"),
			soup.select_one(".vacancy-description"),
			soup.select_one(".job-description"),
			soup.select_one("article"),
			soup.select_one("main"),
		]

		for node in candidates:
			if not node:
				continue
			text = node.get_text("\n", strip=True)
			text = re.sub(r"\n{3,}", "\n\n", text).strip()
			if len(text) >= 120:
				return text

		return ""

	def parse_job_listings(self):
		print(f"Fetching Korn Ferry jobs from {self.SOURCE_URL}")
		html = self._fetch_html(self.SOURCE_URL)
		rows = self._extract_job_rows(html)

		if not rows:
			print("No job rows found in Korn Ferry page")
			return

		print(f"Job rows found: {len(rows)}")

		existing_index = {
			str(job.get("job_id")): idx
			for idx, job in enumerate(self.jobs)
			if job.get("job_id")
		}

		seen_job_ids = set()
		new_count = 0
		updated_count = 0
		skipped_country = 0
		deduped_rows = 0

		for row in rows:
			anchor = row.select_one("a.subject")
			if not anchor:
				continue

			title = anchor.get_text(" ", strip=True)
			link = urljoin(self.SOURCE_URL, anchor.get("href") or "")
			country = self._country_from_row(row)
			if country.strip().lower() not in self.ALLOWED_COUNTRIES:
				skipped_country += 1
				continue

			job_id = self._extract_job_id(row, link)
			if not job_id:
				continue

			if job_id in seen_job_ids:
				deduped_rows += 1
				continue
			seen_job_ids.add(job_id)

			description = self._extract_description(link)
			city = self._extract_field_from_description(description, "City") or self._extract_city(title)
			workplace_type = self._extract_field_from_description(description, "Location type")

			job = {
				"title": title,
				"job_id": job_id,
				"job_seq_no": job_id,
				"link": link,
				"apply_link": link,
				"location": country,
				"city": city,
				"country": country,
				"job_type": "",
				"workplace_type": workplace_type,
				"posted_date": "",
				"company": self.COMPANY,
				"category": "",
				"department": "",
				"description": description,
				"skills": [],
				"status": "active",
				"source": self.SOURCE,
				"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
			}

			if job_id in existing_index:
				idx = existing_index[job_id]
				self.jobs[idx] = {**self.jobs[idx], **job}
				updated_count += 1
			else:
				self.jobs.append(job)
				existing_index[job_id] = len(self.jobs) - 1
				new_count += 1

			print(f"  + {title[:90]} | {country}")

		# Keep only allowed Korn Ferry records for this source.
		self.jobs = [
			j
			for j in self.jobs
			if str(j.get("source") or "").strip().lower() != self.SOURCE.lower()
			or str(j.get("country") or "").strip().lower() in self.ALLOWED_COUNTRIES
		]

		self._save_jobs()
		print("\n" + "=" * 60)
		print(f"Rows parsed           : {len(rows)}")
		print(f"Country-filter skipped: {skipped_country}")
		print(f"Duplicate rows skipped: {deduped_rows}")
		print(f"New jobs stored       : {new_count}")
		print(f"Jobs updated          : {updated_count}")
		print(f"Total jobs in file    : {len(self.jobs)}")
		print("=" * 60)

	def run(self):
		self.parse_job_listings()
		print(f"Done. Output written to {self.output_file}")


if __name__ == "__main__":
	scrapper = KornFerryScrapper()
	scrapper.run()
