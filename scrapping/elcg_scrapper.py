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


class ElcgScrapper:
	def __init__(
		self,
		output_file="json_files/elcg_jobs.json",
		source_url="https://careers.europeanlifecaregroup.com/jobs",
		apikey=os.getenv("ZENROWS"),
	):
		self.output_file = output_file
		self.source_url = source_url
		self.base_url = "https://careers.europeanlifecaregroup.com"
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

	@staticmethod
	def _normalize_multiline_text(text):
		if not text:
			return ""
		text = text.replace("\r", "")
		text = re.sub(r"\n{3,}", "\n\n", text)
		return text.strip()

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
		cards = soup.select("ul#jobs_list_container li.z-career-job-card-image")
		jobs = []

		for card in cards:
			anchor = card.select_one("a[href*='/jobs/']")
			if not anchor:
				continue

			href = anchor.get("href", "")
			link = urljoin(page_url, href) if href else ""
			job_id = ""
			m = re.search(r"/jobs/(\d+)", href)
			if m:
				job_id = m.group(1)
			else:
				job_id = href.strip("/").split("/")[-1]

			title_node = card.select_one("span[title]")
			title = self._clean_text(title_node.get("title") if title_node else "")

			meta_spans = card.select("div.mt-1.text-md span")
			meta_values = []
			for span in meta_spans:
				text = self._clean_text(span.get_text(" ", strip=True))
				if not text:
					continue
				if text in {"·", "&middot;"}:
					continue
				meta_values.append(text)

			remote = None
			if any("hybrid" in x.lower() or "remote" in x.lower() for x in meta_values):
				remote = "Hybrid/Remote"

			filtered_meta = [x for x in meta_values if "hybrid" not in x.lower() and "remote" not in x.lower()]

			company = None
			location = None
			if len(filtered_meta) >= 2:
				company = filtered_meta[0]
				location = filtered_meta[-1]
			elif len(filtered_meta) == 1:
				location = filtered_meta[0]

			if not title or not job_id or not link:
				continue

			jobs.append(
				{
					"title": title,
					"link": link,
					"job_id": str(job_id),
					"location": location or None,
					"company": company or "European LifeCare Group",
					"job_type": None,
					"remote": remote,
					"description": None,
					"snippet": " | ".join(filtered_meta) if filtered_meta else None,
					"status": "active",
					"source": "ELCG",
					"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
				}
			)

		return jobs

	def parse_job_listings(self):
		print(f"Fetching ELCG jobs from: {self.source_url}")

		existing_index = {
			str(job.get("job_id")): idx
			for idx, job in enumerate(self.jobs)
			if job.get("job_id")
		}
		existing_ids = set(existing_index.keys())

		html = self.fetch_html(self.source_url)
		listing_jobs = self._extract_listing_cards(html, self.source_url)

		new_count = 0
		updated_count = 0

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

		self._save_jobs()
		print(f"Found {new_count} new jobs, updated {updated_count}. Total: {len(self.jobs)} jobs.")

	def _extract_description_from_detail_html(self, html):
		soup = BeautifulSoup(html, "html.parser")

		description_node = soup.select_one("section.pt-20.pb-12 div.max-w-750.prose")
		if not description_node:
			description_node = soup.select_one("main div.prose")

		if description_node:
			for sel in ["script", "style", "noscript", "button", "nav", "footer", "form"]:
				for tag in description_node.select(sel):
					tag.decompose()

			text = self._normalize_multiline_text(description_node.get_text("\n", strip=True))
			if len(text) >= 120:
				return text

		main = soup.select_one("main")
		if main:
			text = self._normalize_multiline_text(main.get_text("\n", strip=True))
			return text

		return ""

	def fetch_job_descriptions(self, delay=0.2):
		jobs_to_update = [
			job
			for job in self.jobs
			if str(job.get("source") or "").strip().lower() == "elcg"
			and job.get("link")
			and len(str(job.get("description") or "").strip()) < 120
		]

		if not jobs_to_update:
			print("No ELCG jobs need description updates.")
			return

		print(f"Fetching descriptions for {len(jobs_to_update)} ELCG job(s)...")
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
	scrapper = ElcgScrapper()
	scrapper.run()
