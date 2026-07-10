import json
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv


load_dotenv()


class TalentmarkScrapper:
	def __init__(
		self,
		output_file="json_files/talentmark_jobs.json",
		source_url="https://www.talentmark.com/en/jobs/?job_expertise=board-leadership&job_country=netherlands",
		html_file="talentmark_jobs.html",
		apikey=os.getenv("ZENROWS"),
	):
		self.output_file = output_file
		self.source_url = source_url
		self.html_file = html_file
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

	def _fetch_with_zenrows(self, url):
		if not self.apikey:
			return None

		params = {
			"url": url,
			"apikey": self.apikey,
			"mode": "auto",
			"wait": "3000",
		}
		response = requests.get("https://api.zenrows.com/v1/", params=params, timeout=90)
		if response.status_code != 200:
			print(f"[warn] ZenRows HTTP {response.status_code} for {url}")
			return None
		return response.text

	def fetch_html(self, url):
		headers = {
			"Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
			"User-Agent": (
				"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
				"AppleWebKit/537.36 (KHTML, like Gecko) "
				"Chrome/124.0.0.0 Safari/537.36"
			),
		}

		try:
			response = requests.get(url, headers=headers, timeout=60)
			response.raise_for_status()
			return response.text
		except Exception as direct_error:
			zenrows_html = self._fetch_with_zenrows(url)
			if zenrows_html:
				return zenrows_html
			raise direct_error

	def _get_filter_targets(self, url):
		parsed = urlparse(url)
		params = parse_qs(parsed.query)
		expected_expertise = "board-leadership"
		expected_country = "netherlands"

		if params.get("job_expertise"):
			expected_expertise = str(params["job_expertise"][0]).lower().strip()
		if params.get("job_country"):
			expected_country = str(params["job_country"][0]).lower().strip()

		return expected_expertise, expected_country

	@staticmethod
	def _extract_job_id(link):
		if not link:
			return None

		match = re.search(r"/jobs/([^/?#]+)/?", link, flags=re.IGNORECASE)
		if match:
			return match.group(1).strip().lower()

		return None

	def _matches_requested_filters(self, expertise_text, location_text, expected_expertise, expected_country):
		expertise = str(expertise_text or "").lower()
		location = str(location_text or "").lower()

		normalized_country = expected_country.replace("-", " ")

		if normalized_country and normalized_country not in location:
			return False

		# Listing cards do not always expose all selected expertise values.
		# When scraping from a filtered source URL, trust URL filtering and do not
		# hard-fail on expertise text mismatches from the card snippet itself.
		if expected_expertise:
			normalized_expertise = expected_expertise.replace("-", " ")
			if expertise and normalized_expertise in expertise:
				return True
		return True

	def _extract_listing_jobs(self, html, page_url, expected_expertise, expected_country):
		soup = BeautifulSoup(html, "html.parser")
		cards = soup.select("article.job-summary")
		jobs = []

		for card in cards:
			link_tag = card.select_one("h3 a[href]")
			if not link_tag:
				continue

			link = urljoin(page_url, link_tag.get("href", "").strip())
			title = self._clean_text(link_tag.get_text(" ", strip=True))
			if not link or not title:
				continue

			top_props = card.select_one("ul.loop-job-properties:not(.bottom-loop)")
			bottom_props = card.select_one("ul.loop-job-properties.bottom-loop")

			location_text = ""
			if top_props and top_props.select_one("li.location .text"):
				location_text = self._clean_text(top_props.select_one("li.location .text").get_text(" ", strip=True))

			expertise_values = []
			for node in (top_props.select("li.expertise .text") if top_props else []):
				value = self._clean_text(node.get_text(" ", strip=True))
				if value:
					expertise_values.append(value)

			expertise_text = " | ".join(expertise_values)

			if not self._matches_requested_filters(expertise_text, location_text, expected_expertise, expected_country):
				continue

			excerpt_node = card.select_one("div.excerpt")
			excerpt = self._clean_text(excerpt_node.get_text(" ", strip=True)) if excerpt_node else ""

			posted_date = ""
			date_node = top_props.select_one("li.date .text, li.date") if top_props else None
			if date_node:
				posted_date = self._clean_text(date_node.get_text(" ", strip=True))

			employment_type = ""
			education = ""
			bottom_values = []
			if bottom_props:
				for node in bottom_props.select("li"):
					value = self._clean_text(node.get_text(" ", strip=True))
					if value:
						bottom_values.append(value)
			if bottom_values:
				employment_type = bottom_values[0]
			if len(bottom_values) > 1:
				education = bottom_values[1]

			job_id = self._extract_job_id(link) or link.lower()

			jobs.append(
				{
					"title": title,
					"link": link,
					"job_id": job_id,
					"location": location_text or None,
					"company": "Talentmark",
					"description": excerpt or None,
					"status": "active",
					"source": "Talentmark",
					"expertise": expertise_text or None,
					"employment_type": employment_type or None,
					"education": education or None,
					"posted_date": posted_date or None,
					"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
				}
			)

		next_url = None
		next_link = soup.select_one("nav.pagination a.next, nav.pagination a.next.page-numbers")
		if next_link and next_link.get("href"):
			next_url = urljoin(page_url, next_link.get("href"))

		if not next_url:
			rel_next = soup.select_one("link[rel='next']")
			if rel_next and rel_next.get("href"):
				next_url = urljoin(page_url, rel_next.get("href"))

		return jobs, next_url

	def parse_job_listings(self, search_url=None):
		start_url = search_url or self.source_url
		expected_expertise, expected_country = self._get_filter_targets(start_url)
		print(f"Fetching Talentmark listings from: {start_url}")

		existing_index = {
			str(job.get("job_id")): idx
			for idx, job in enumerate(self.jobs)
			if job.get("job_id")
		}
		existing_ids = set(existing_index.keys())

		seen_page_urls = set()
		page_url = start_url
		pages = 0
		new_count = 0
		updated_count = 0
		seen_job_ids = set()

		while page_url and page_url not in seen_page_urls:
			seen_page_urls.add(page_url)
			pages += 1
			print(f"  Parsing page {pages}: {page_url}")

			html = self.fetch_html(page_url)
			listing_jobs, next_url = self._extract_listing_jobs(
				html,
				page_url,
				expected_expertise,
				expected_country,
			)

			if not listing_jobs and pages == 1 and os.path.exists(self.html_file):
				print(f"  Live page yielded no jobs. Falling back to local file: {self.html_file}")
				with open(self.html_file, "r", encoding="utf-8") as f:
					local_html = f.read()
				listing_jobs, _ = self._extract_listing_jobs(
					local_html,
					start_url,
					expected_expertise,
					expected_country,
				)

			for listing_job in listing_jobs:
				job_id = str(listing_job.get("job_id"))
				seen_job_ids.add(job_id)

				if job_id in existing_ids:
					idx = existing_index[job_id]
					existing_job = self.jobs[idx]
					if str(existing_job.get("description") or "").strip():
						listing_job["description"] = existing_job.get("description")
					self.jobs[idx] = {**existing_job, **listing_job}
					updated_count += 1
				else:
					self.jobs.append(listing_job)
					existing_ids.add(job_id)
					existing_index[job_id] = len(self.jobs) - 1
					new_count += 1

			page_url = next_url

		# Keep old records from this source but mark as expired if no longer listed.
		expired_count = 0
		for job in self.jobs:
			if str(job.get("source") or "").strip().lower() != "talentmark":
				continue
			job_id = str(job.get("job_id") or "")
			if job_id and job_id not in seen_job_ids:
				job["status"] = "expired"
				expired_count += 1

		self._save_jobs()
		print(
			f"Found {new_count} new jobs, updated {updated_count}, marked {expired_count} expired. "
			f"Total: {len(self.jobs)} jobs across {pages} page(s)."
		)

	def _extract_description_from_detail_html(self, html):
		soup = BeautifulSoup(html, "html.parser")

		candidate_selectors = [
			"section#single-job-content",
			"div.single-job-content",
			"section.single-job",
			"main#main",
		]

		best_text = ""
		for selector in candidate_selectors:
			node = soup.select_one(selector)
			if not node:
				continue

			cloned = BeautifulSoup(str(node), "html.parser")
			for bad in cloned.select(
				"script, style, nav, form, footer, aside, .related, .related-vacancies, .alert-wrap"
			):
				bad.decompose()

			text = self._normalize_multiline_text(cloned.get_text("\n", strip=True))
			if len(text) > len(best_text):
				best_text = text

		if len(best_text) < 160:
			for tag in soup(["script", "style", "nav", "footer", "form"]):
				tag.decompose()
			fallback_text = self._normalize_multiline_text(soup.get_text("\n", strip=True))
			if len(fallback_text) > len(best_text):
				best_text = fallback_text

		# Trim global footer text if it leaks into the extracted body.
		best_text = re.split(r"\nTalentmark offices\n|\nPrivacy Statement\n", best_text, maxsplit=1)[0].strip()
		best_text = re.split(
			r"(?i)apply for this job now!|related vacancies|back to overview",
			best_text,
			maxsplit=1,
		)[0].strip()
		return best_text

	def fetch_job_descriptions(self, delay=0.25):
		def _needs_refresh(job):
			description = str(job.get("description") or "").strip()
			if len(description) < 160:
				return True
			normalized = description.lower()
			if "related vacancies" in normalized or "apply for this job now!" in normalized:
				return True
			return False

		jobs_to_update = [
			job
			for job in self.jobs
			if str(job.get("source") or "").strip().lower() == "talentmark"
			and str(job.get("status") or "").strip().lower() == "active"
			and job.get("link")
			and _needs_refresh(job)
		]

		if not jobs_to_update:
			print("No Talentmark jobs need description updates.")
			return

		print(f"Fetching descriptions for {len(jobs_to_update)} Talentmark job(s)...")
		success_count = 0
		failed_count = 0

		for i, job in enumerate(jobs_to_update, start=1):
			print(f"[{i}/{len(jobs_to_update)}] Fetching description: {job.get('title')}")
			try:
				html = self.fetch_html(job["link"])
				description = self._extract_description_from_detail_html(html)

				if description and len(description) >= 160:
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

	def run(self, search_url=None):
		self.parse_job_listings(search_url=search_url)
		self.fetch_job_descriptions()


if __name__ == "__main__":
	scraper = TalentmarkScrapper()
	scraper.run()
