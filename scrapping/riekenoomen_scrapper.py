import json
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv


load_dotenv()


class RiekenOomenScrapper:
	def __init__(
		self,
		output_file="json_files/riekenoomen_jobs.json",
		source_url="https://www.riekenoomen.nl/vacatures",
		apikey=os.getenv("ZENROWS"),
	):
		self.output_file = output_file
		self.source_url = source_url
		self.base_url = "https://www.riekenoomen.nl"
		self.apikey = apikey
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
		return re.sub(r"\s+", " ", value).strip()

	def fetch_html(self, url):
		headers = {
			"Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
			"User-Agent": (
				"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
				"AppleWebKit/537.36 (KHTML, like Gecko) "
				"Chrome/122.0.0.0 Safari/537.36"
			),
		}

		try:
			resp = requests.get(url, headers=headers, timeout=60)
			resp.raise_for_status()
			return resp.text
		except Exception as direct_error:
			if self.apikey:
				params = {
					"url": url,
					"apikey": self.apikey,
					"mode": "auto",
				}
				try:
					resp = requests.get("https://api.zenrows.com/v1/", params=params, timeout=90)
					if resp.status_code == 200:
						return resp.text
					print(f"[warn] ZenRows HTTP {resp.status_code} for {url}")
				except Exception as e:
					print(f"[warn] ZenRows fetch failed for {url}: {e}")

			raise direct_error

	def _extract_listing_cards(self, html, page_url):
		soup = BeautifulSoup(html, "html.parser")
		cards = soup.select("article.node--type-vacature-extern")
		jobs = []

		for card in cards:
			anchor = card.select_one("a.vacature-extern-link-block")
			if not anchor:
				continue

			href = anchor.get("href", "")
			link = urljoin(page_url, href) if href else ""
			job_id = href.strip("/").split("/")[-1] if href else ""

			title = self._clean_text(card.select_one("h2 .field--name-title").get_text()) if card.select_one("h2 .field--name-title") else ""
			status_text = self._clean_text(card.select_one(".value-field_vac_ex_status").get_text()) if card.select_one(".value-field_vac_ex_status") else ""
			location = self._clean_text(card.select_one(".value-field_vac_ex_standplaats").get_text()) if card.select_one(".value-field_vac_ex_standplaats") else ""
			company = self._clean_text(card.select_one(".value-field_vac_ex_organisatie_naam").get_text()) if card.select_one(".value-field_vac_ex_organisatie_naam") else ""

			if not job_id or not title:
				continue

			status = "active"
			low = status_text.lower()
			if "verstreken" in low or "binnenkort beschikbaar" in low:
				status = "expired"

			jobs.append(
				{
					"title": title,
					"link": link,
					"job_id": job_id,
					"location": location or None,
					"company": company or None,
					"description": None,
					"status": status,
					"status_text": status_text or None,
					"source": "Rieken & Oomen",
					"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
				}
			)

		next_page = soup.select_one("ul.pagination a.page-link[rel='next']")
		next_url = urljoin(page_url, next_page.get("href")) if next_page and next_page.get("href") else None

		return jobs, next_url

	def parse_job_listings(self):
		print(f"Fetching Rieken & Oomen listings from: {self.source_url}")

		existing_index = {
			str(job.get("job_id")): idx
			for idx, job in enumerate(self.jobs)
			if job.get("job_id")
		}
		existing_ids = set(existing_index.keys())

		seen_page_urls = set()
		page_url = self.source_url
		pages = 0
		new_count = 0
		updated_count = 0

		while page_url and page_url not in seen_page_urls:
			seen_page_urls.add(page_url)
			pages += 1
			print(f"  Parsing page {pages}: {page_url}")

			html = self.fetch_html(page_url)
			listing_jobs, next_url = self._extract_listing_cards(html, page_url)

			for job in listing_jobs:
				job_id = str(job.get("job_id"))

				if job_id in existing_ids:
					idx = existing_index[job_id]
					existing_job = self.jobs[idx]
					if str(existing_job.get("description") or "").strip():
						job["description"] = existing_job.get("description")
					self.jobs[idx] = {**existing_job, **job}
					updated_count += 1
				else:
					self.jobs.append(job)
					existing_ids.add(job_id)
					existing_index[job_id] = len(self.jobs) - 1
					new_count += 1

			page_url = next_url

		self._save_jobs()
		print(
			f"Found {new_count} new jobs, updated {updated_count}. "
			f"Total: {len(self.jobs)} jobs across {pages} page(s)."
		)

	@staticmethod
	def _normalize_multiline_text(text):
		if not text:
			return ""
		text = text.replace("\r", "")
		text = re.sub(r"\n{3,}", "\n\n", text)
		return text.strip()

	def _extract_description_from_detail_html(self, html):
		soup = BeautifulSoup(html, "html.parser")

		node = soup.select_one("article.node--type-vacature-extern.node--view-mode-full .node__content")
		if not node:
			node = soup.select_one(".node--vacature-extern-content")

		if node:
			for sel in ["header", "nav", "footer", ".breadcrumb", ".vacature-extern-cta-block", ".field--name-field-vac-ex-solliciteer-link"]:
				for tag in node.select(sel):
					tag.decompose()

			text = self._normalize_multiline_text(node.get_text("\n", strip=True))
			if len(text) >= 120:
				return text

		main = soup.select_one(".main-content")
		if main:
			text = self._normalize_multiline_text(main.get_text("\n", strip=True))
			return text

		return ""

	def fetch_job_descriptions(self, delay=0.25):
		jobs_to_update = [
			job
			for job in self.jobs
			if str(job.get("source") or "").strip().lower() == "rieken & oomen"
			and job.get("link")
			and len(str(job.get("description") or "").strip()) < 120
		]

		if not jobs_to_update:
			print("No Rieken & Oomen jobs available for description refresh.")
			return

		print(f"Fetching descriptions for {len(jobs_to_update)} Rieken & Oomen job(s)...")
		success_count = 0
		failed_count = 0

		for i, job in enumerate(jobs_to_update, start=1):
			print(f"[{i}/{len(jobs_to_update)}] Fetching description: {job.get('title')}")
			try:
				html = self.fetch_html(job["link"])
				description = self._extract_description_from_detail_html(html)

				if description and len(description) >= 120:
					job["description"] = description
					success_count += 1
					print(f"  + Description fetched ({len(description)} chars)")
				else:
					failed_count += 1
					print("  ! Description empty or too short")
			except Exception as e:
				failed_count += 1
				print(f"  ! Failed to fetch description: {e}")

			self._save_jobs()
			time.sleep(delay)

		print(f"Description refresh done. Success: {success_count}, Failed: {failed_count}")

	def run(self):
		self.parse_job_listings()
		self.fetch_job_descriptions()


if __name__ == "__main__":
	scrapper = RiekenOomenScrapper()
	scrapper.run()
