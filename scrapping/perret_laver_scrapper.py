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


class PerretLaverScrapper:
	def __init__(
		self,
		output_file="json_files/perret_laver_jobs.json",
		html_file="perret_laver_data.html",
		base_url="https://plusportal.perrettlaver.com",
		source_url="https://plusportal.perrettlaver.com/PracticeGroup/9f11755f-6033-31bc-ec9d-3a19d5ffcead",
		apikey=os.getenv("ZENROWS"),
	):
		self.output_file = output_file
		self.html_file = html_file
		self.base_url = base_url.rstrip("/")
		self.source_url = source_url
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

	def _get_existing_job_ids(self):
		return {str(job.get("job_id")) for job in self.jobs if job.get("job_id")}

	@staticmethod
	def _clean_text(value):
		if not value:
			return None
		text = re.sub(r"\s+", " ", value).strip()
		return text or None

	def _read_local_html(self):
		with open(self.html_file, "r", encoding="utf-8") as f:
			return f.read()

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
			response = requests.get(url, headers=headers, timeout=60)
			response.raise_for_status()
			return response.text
		except Exception as direct_error:
			if self.apikey:
				params = {
					"url": url,
					"apikey": self.apikey,
					"mode": "auto",
				}
				try:
					response = requests.get("https://api.zenrows.com/v1/", params=params, timeout=90)
					if response.status_code == 200:
						return response.text
					print(f"[warn] ZenRows HTTP {response.status_code} for {url}")
				except Exception as e:
					print(f"[warn] ZenRows fetch failed for {url}: {e}")

			raise direct_error

	def parse_job_listings(self, search_url=None):
		source = search_url or self.source_url
		source_label = source if source else self.html_file
		print(f"Fetching Perrett Laver listings from: {source_label}")

		if source:
			html = self.fetch_html(source)
			base_url = source
		else:
			html = self._read_local_html()
			base_url = self.base_url

		soup = BeautifulSoup(html, "html.parser")
		job_cards = soup.find_all("div", class_="vacancy-card")

		existing_index = {
			str(job.get("job_id")): idx
			for idx, job in enumerate(self.jobs)
			if job.get("job_id")
		}
		existing_ids = set(existing_index.keys())
		new_count = 0
		updated_count = 0
		skipped_count = 0

		for card in job_cards:
			title_tag = card.find("span", class_="title")
			subtitle_tags = card.find_all("span", class_="subtitle")
			pill_tag = card.find("span", class_="vacancy__pill")

			detail_link_tag = card.find("a", href=re.compile(r"/VacancyDetail/", re.IGNORECASE))
			link = None
			if detail_link_tag and detail_link_tag.get("href"):
				link = urljoin(base_url, detail_link_tag["href"])

			job_id = None
			if link:
				match = re.search(r"/VacancyDetail/([^/?#]+)", link, flags=re.IGNORECASE)
				if match:
					job_id = match.group(1)

			# Fallback ID from vacancy number when detail UUID is missing.
			if not job_id and pill_tag:
				job_id = self._clean_text(pill_tag.get_text())

			if not job_id:
				continue

			title = self._clean_text(title_tag.get_text()) if title_tag else None
			company = None
			location = None
			listing_description = None

			if subtitle_tags:
				company = self._clean_text(subtitle_tags[0].get_text())
				if len(subtitle_tags) > 1:
					possible_location = self._clean_text(subtitle_tags[1].get_text())
					if possible_location and (len(possible_location) > 90 or "." in possible_location):
						listing_description = possible_location
					else:
						location = possible_location

			vacancy_number = self._clean_text(pill_tag.get_text()) if pill_tag else None
			job_payload = {
				"title": title,
				"link": link,
				"job_id": job_id,
				"job_seq_no": vacancy_number,
				"location": location,
				"company": company,
				"description": listing_description,
				"status": "active",
				"source": "Perrett Laver",
				"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
			}

			if job_id in existing_ids:
				idx = existing_index[job_id]
				existing_job = self.jobs[idx]
				if str(existing_job.get("description") or "").strip():
					job_payload["description"] = existing_job.get("description")
				self.jobs[idx] = {**existing_job, **job_payload}
				updated_count += 1
				skipped_count += 1
			else:
				self.jobs.append(job_payload)
				existing_ids.add(job_id)
				existing_index[job_id] = len(self.jobs) - 1
				new_count += 1

		self._save_jobs()
		print(
			f"Found {new_count} new jobs, updated {updated_count}, skipped {skipped_count} duplicates. "
			f"Total: {len(self.jobs)} jobs."
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

		# Primary selector from Perrett Laver detail pages.
		rich_text_node = soup.select_one("div.col-12.rich-text")
		if rich_text_node:
			parts = []
			for node in rich_text_node.select("p, li"):
				text = self._normalize_multiline_text(node.get_text(" ", strip=True))
				if text:
					parts.append(text)

			if parts:
				rich_text = "\n\n".join(parts).strip()
			else:
				rich_text = self._normalize_multiline_text(rich_text_node.get_text("\n", strip=True))
			if rich_text and len(rich_text) >= 40:
				return rich_text

		candidate_selectors = [
			"div.col-12.rich-text",
			"main",
			"article",
			"section",
			"div[class*='vacancy']",
			"div[class*='detail']",
			"div[class*='description']",
		]

		best_text = ""
		for selector in candidate_selectors:
			for node in soup.select(selector):
				text = self._normalize_multiline_text(node.get_text("\n", strip=True))
				if len(text) > len(best_text):
					best_text = text

		if len(best_text) >= 120:
			# Remove common global footer text if it leaks into the extracted body.
			best_text = re.split(r"\nUseful Links\n|\n©\s*\d{4}", best_text, maxsplit=1)[0].strip()
			return best_text

		for tag in soup(["script", "style", "header", "footer", "nav"]):
			tag.decompose()

		body_text = self._normalize_multiline_text(soup.get_text("\n", strip=True))
		body_text = re.split(r"\nUseful Links\n|\n©\s*\d{4}", body_text, maxsplit=1)[0].strip()
		return body_text

	@staticmethod
	def _is_generic_listing_page_text(text):
		if not text:
			return True
		normalized = text.lower()
		generic_markers = [
			"explore our current vacancies by sector or keyword",
			"browse by",
			"register with us",
			"work for perrett laver",
		]
		return all(marker in normalized for marker in generic_markers)

	@staticmethod
	def _build_fallback_description(job):
		title = str(job.get("title") or "").strip()
		company = str(job.get("company") or "").strip()
		location = str(job.get("location") or "").strip()

		parts = []
		if title:
			parts.append(f"Role: {title}")
		if company:
			parts.append(f"Organization: {company}")
		if location:
			parts.append(f"Location: {location}")

		return " | ".join(parts) if parts else "Perrett Laver vacancy listing"

	def fetch_job_descriptions(self, delay=0.5):
		jobs_to_update = [
			job
			for job in self.jobs
			if str(job.get("source") or "").strip().lower() == "perrett laver"
			and job.get("link")
			and len(str(job.get("description") or "").strip()) < 120
		]

		if not jobs_to_update:
			print("No Perrett Laver jobs need description updates.")
			return

		print(f"Fetching descriptions for {len(jobs_to_update)} Perrett Laver job(s)...")
		success_count = 0
		failed_count = 0

		for i, job in enumerate(jobs_to_update, start=1):
			link = job.get("link")
			print(f"[{i}/{len(jobs_to_update)}] Fetching description: {job.get('title')}")

			try:
				html = self.fetch_html(link)
				description = self._extract_description_from_detail_html(html)

				if (
					description
					and len(description) >= 80
					and not self._is_generic_listing_page_text(description)
				):
					job["description"] = description
					success_count += 1
					print(f"  + Description fetched ({len(description)} chars)")
				else:
					if (
						not str(job.get("description") or "").strip()
						or self._is_generic_listing_page_text(str(job.get("description") or ""))
					):
						job["description"] = self._build_fallback_description(job)
					failed_count += 1
					print("  ! Detail page did not contain vacancy description, used fallback")
			except Exception as e:
				if (
					not str(job.get("description") or "").strip()
					or self._is_generic_listing_page_text(str(job.get("description") or ""))
				):
					job["description"] = self._build_fallback_description(job)
				failed_count += 1
				print(f"  ! Failed to fetch description: {e}")

			self._save_jobs()
			time.sleep(delay)

		print(
			f"Description refresh done. Success: {success_count}, "
			f"Failed: {failed_count}"
		)

	def run(self, search_url=None):
		self.parse_job_listings(search_url=search_url)
		self.fetch_job_descriptions()


if __name__ == "__main__":
	scraper = PerretLaverScrapper()
	scraper.run(search_url=scraper.source_url)
