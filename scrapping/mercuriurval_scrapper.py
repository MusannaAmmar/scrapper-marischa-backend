import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import urljoin
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup


class MercuriUrvalScrapper:
	"""
	Scrapes Mercuri Urval opportunities using the public XML API behind:
	https://www.mercuriurval.com/global/our-opportunities/?q=&filter=business-17,business-16,business-4,business-14
	"""

	SOURCE_URL = (
		"https://www.mercuriurval.com/global/our-opportunities/"
		"?q=&filter=business-17,business-16,business-4,business-14"
	)
	API_URL = "https://www.mercuriurval.com/api/opportunities/search"
	BASE_URL = "https://www.mercuriurval.com"

	# Required filters from your request.
	FILTERS = ["business-17", "business-16", "business-4", "business-14"]

	COMPANY = "Mercuri Urval"
	SOURCE = "Mercuri Urval"

	def __init__(self, output_file="json_files/mercuriurval_jobs.json"):
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
			"Accept": "application/xml,text/xml;q=0.9,*/*;q=0.8",
			"User-Agent": (
				"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
				"AppleWebKit/537.36 (KHTML, like Gecko) "
				"Chrome/124.0.0.0 Safari/537.36"
			),
		}

	@staticmethod
	def _local_name(tag):
		return tag.split("}", 1)[-1] if "}" in tag else tag

	def _find_text(self, node, field_name):
		for child in list(node):
			if self._local_name(child.tag) == field_name:
				return (child.text or "").strip()
		return ""

	@staticmethod
	def _clean_text(text):
		text = re.sub(r"\r", "\n", text or "")
		text = re.sub(r"\n{3,}", "\n\n", text)
		return text.strip()

	def _fetch_api_xml(self, page_url=None):
		if page_url:
			resp = requests.get(page_url, headers=self._headers(), timeout=60)
		else:
			params = {
				"language": "en-XX",
				"q": "",
				"filter": ",".join(self.FILTERS),
			}
			resp = requests.get(self.API_URL, params=params, headers=self._headers(), timeout=60)

		resp.raise_for_status()
		return resp.text

	def _parse_api_page(self, xml_text):
		root = ET.fromstring(xml_text)
		opportunities = []
		next_page_link = ""

		for child in list(root):
			name = self._local_name(child.tag)

			if name == "Opportunities":
				for opp in list(child):
					if self._local_name(opp.tag) != "Opportunity":
						continue
					opportunities.append(opp)

			elif name == "NextPageLink":
				next_page_link = (child.text or "").strip()

		return opportunities, next_page_link

	def _fetch_detail_and_extract(self, detail_url):
		try:
			resp = requests.get(detail_url, headers=self._headers(), timeout=60)
			resp.raise_for_status()
		except Exception:
			return {
				"description": "",
				"location": "",
				"posted_date": "",
				"publish_date": "",
				"reference_number": "",
				"company": "",
			}

		soup = BeautifulSoup(resp.text, "html.parser")
		text = soup.get_text("\n", strip=True)

		description = ""
		candidates = [
			soup.select_one("main"),
			soup.select_one("article"),
			soup.select_one(".job-ad"),
			soup.select_one(".content"),
		]
		for node in candidates:
			if not node:
				continue
			node_text = self._clean_text(node.get_text("\n", strip=True))
			if len(node_text) > 200:
				description = node_text
				break
		if not description:
			description = self._clean_text(text)

		def match_value(pattern):
			m = re.search(pattern, text, flags=re.IGNORECASE)
			return m.group(1).strip() if m else ""

		location = match_value(r"Location:\s*(.+)")
		posted_date = match_value(r"Apply before:\s*([0-9]{4}-[0-9]{2}-[0-9]{2})")
		publish_date = match_value(r"Publish date:\s*([0-9]{4}-[0-9]{2}-[0-9]{2})")
		reference_number = match_value(r"Reference number:\s*([A-Za-z0-9\-_/]+)")

		company = ""
		company_h2 = soup.select_one("main h2")
		if company_h2:
			company = company_h2.get_text(" ", strip=True)

		return {
			"description": description,
			"location": location,
			"posted_date": posted_date,
			"publish_date": publish_date,
			"reference_number": reference_number,
			"company": company,
		}

	@staticmethod
	def _build_job_id(api_id, reference, detail_url):
		if reference:
			return reference
		if api_id:
			return str(api_id)
		m = re.search(r"jobadid=([0-9]+)", detail_url or "")
		if m:
			return m.group(1)
		return ""

	def parse_job_listings(self):
		print(f"Fetching Mercuri Urval jobs from filtered source:\n{self.SOURCE_URL}")

		existing_index = {
			str(job.get("job_id")): idx
			for idx, job in enumerate(self.jobs)
			if job.get("job_id")
		}
		existing_links = {
			str(job.get("link") or "").strip()
			for job in self.jobs
			if str(job.get("source") or "").strip().lower() == self.SOURCE.lower()
			and str(job.get("link") or "").strip()
		}
		existing_job_id_by_link = {
			str(job.get("link") or "").strip(): str(job.get("job_id") or "").strip()
			for job in self.jobs
			if str(job.get("source") or "").strip().lower() == self.SOURCE.lower()
			and str(job.get("link") or "").strip()
			and str(job.get("job_id") or "").strip()
		}

		seen_job_ids = set()
		new_count = 0
		updated_count = 0
		skipped_existing = 0
		page_count = 0
		page_url = None

		while True:
			page_count += 1
			xml_text = self._fetch_api_xml(page_url=page_url)
			opp_nodes, next_page_link = self._parse_api_page(xml_text)

			print(f"Page {page_count}: opportunities found = {len(opp_nodes)}")

			for opp in opp_nodes:
				title = self._find_text(opp, "Title")
				if not title:
					continue

				api_id = self._find_text(opp, "Id")
				raw_url = self._find_text(opp, "Url")
				detail_url = urljoin(self.BASE_URL, raw_url) if raw_url else ""
				if not detail_url:
					continue

				if detail_url in existing_links:
					existing_id = existing_job_id_by_link.get(detail_url, "")
					if existing_id:
						seen_job_ids.add(existing_id)
					skipped_existing += 1
					continue

				api_id_key = str(api_id or "").strip()
				if api_id_key and api_id_key in existing_index:
					seen_job_ids.add(api_id_key)
					skipped_existing += 1
					continue

				detail = self._fetch_detail_and_extract(detail_url)

				ref_number = detail.get("reference_number", "")
				job_id = self._build_job_id(api_id, ref_number, detail_url)
				if not job_id:
					continue

				if job_id in seen_job_ids:
					continue
				seen_job_ids.add(job_id)

				location = detail.get("location", "")
				company = detail.get("company") or self._find_text(opp, "CompanyName") or self.COMPANY

				job = {
					"title": title,
					"job_id": job_id,
					"job_seq_no": str(api_id or job_id),
					"link": detail_url,
					"apply_link": detail_url,
					"location": location,
					"city": location,
					"country": self._find_text(opp, "Country"),
					"job_type": self._find_text(opp, "Function"),
					"workplace_type": "",
					"posted_date": detail.get("posted_date", ""),
					"publish_date": detail.get("publish_date", ""),
					"company": company,
					"category": self._find_text(opp, "BusinessSector"),
					"department": self._find_text(opp, "Level"),
					"reference_number": ref_number,
					"description": detail.get("description", ""),
					"skills": [],
					"status": "active",
					"source": self.SOURCE,
					"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
				}

				self.jobs.append(job)
				existing_index[job_id] = len(self.jobs) - 1
				existing_links.add(detail_url)
				new_count += 1

				print(f"  + {title[:100]} | {location}")

			if not next_page_link:
				break
			page_url = urljoin(self.BASE_URL, next_page_link)

		# Keep only records from current crawl for this source.
		self.jobs = [
			j
			for j in self.jobs
			if str(j.get("source") or "").strip().lower() != self.SOURCE.lower()
			or str(j.get("job_id") or "") in seen_job_ids
		]

		self._save_jobs()

		print("\n" + "=" * 60)
		print(f"Pages parsed       : {page_count}")
		print(f"Unique jobs seen   : {len(seen_job_ids)}")
		print(f"New jobs stored    : {new_count}")
		print(f"Jobs updated       : {updated_count}")
		print(f"Existing skipped   : {skipped_existing}")
		print(f"Total jobs in file : {len(self.jobs)}")
		print("=" * 60)

	def run(self):
		self.parse_job_listings()
		print(f"Done. Output written to {self.output_file}")


if __name__ == "__main__":
	scrapper = MercuriUrvalScrapper()
	scrapper.run()
