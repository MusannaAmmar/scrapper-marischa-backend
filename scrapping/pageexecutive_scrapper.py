import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


class PageExecutiveScrapper:
	"""
	Scrapes Page Executive vacancies and stores normalized job entries.

	Default source URL:
	  https://www.pageexecutive.com/jobs/cfo-financial-management/europe
	"""

	SOURCE_URL = "https://www.pageexecutive.com/jobs/cfo-financial-management/europe"
	COMPANY = "Page Executive"
	SOURCE = "PAGE_EXECUTIVE"
	EUROPE_COUNTRIES = {
		"albania", "andorra", "austria", "belarus", "belgium", "bosnia and herzegovina",
		"bulgaria", "croatia", "cyprus", "czech republic", "czechia", "denmark", "estonia",
		"finland", "france", "germany", "greece", "hungary", "iceland", "ireland", "italy",
		"kosovo", "latvia", "liechtenstein", "lithuania", "luxembourg", "malta", "moldova",
		"monaco", "montenegro", "netherlands", "north macedonia", "norway", "poland",
		"portugal", "romania", "san marino", "serbia", "slovakia", "slovenia", "spain",
		"sweden", "switzerland", "ukraine", "united kingdom", "vatican city", "england",
		"scotland", "wales",
	}

	def __init__(self, output_file="json_files/pageexecutive_jobs.json"):
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
				"Chrome/122.0.0.0 Safari/537.36"
			),
		}

	def _fetch_html(self, url):
		resp = requests.get(url, headers=self._headers(), timeout=60)
		resp.raise_for_status()
		return resp.text

	@staticmethod
	def _clean_text(value):
		return " ".join(str(value or "").replace("\xa0", " ").split()).strip()

	@staticmethod
	def _normalize_multiline(text):
		if not text:
			return ""
		text = text.replace("\r", "")
		text = text.replace("\xa0", " ")
		text = re.sub(r"\n{3,}", "\n\n", text)
		return text.strip()

	@staticmethod
	def _slug_from_url(url):
		path = (urlparse(url).path or "").strip("/")
		return path.split("/")[-1] if path else ""

	def _is_europe_location(self, location_text):
		if not location_text:
			return False

		normalized = self._clean_text(location_text).lower()
		parts = [self._clean_text(p).lower() for p in normalized.split(",") if self._clean_text(p)]
		country_candidate = parts[-1] if parts else normalized

		if country_candidate in self.EUROPE_COUNTRIES:
			return True

		# Defensive fallback for values like "London, UK" or "Remote - Europe"
		if country_candidate == "uk" or country_candidate.endswith(" uk"):
			return True
		if "europe" in normalized:
			return True

		return False

	def _extract_pagination_links(self, html, base_url):
		soup = BeautifulSoup(html, "html.parser")
		links = []
		for a in soup.select("a[rel='next'][href], ul.pager a[href], li.pager__item a[href]"):
			href = self._clean_text(a.get("href"))
			if not href:
				continue
			full = urljoin(base_url, href)
			if "pageexecutive.com" not in (urlparse(full).netloc or ""):
				continue
			if "/jobs" not in (urlparse(full).path or ""):
				continue
			links.append(full)
		return links

	def _collect_listing_pages(self, first_page_html, max_pages=30):
		queue = [self.SOURCE_URL]
		visited = set()
		pages = []
		html_cache = {self.SOURCE_URL: first_page_html}

		while queue and len(pages) < max_pages:
			page_url = queue.pop(0)
			if page_url in visited:
				continue
			visited.add(page_url)

			html = html_cache.get(page_url)
			if html is None:
				try:
					html = self._fetch_html(page_url)
				except Exception as exc:
					print(f"  ! failed listing page fetch during discovery: {page_url} ({exc})")
					continue
				html_cache[page_url] = html

			pages.append(page_url)
			for next_link in self._extract_pagination_links(html, page_url):
				if next_link not in visited and next_link not in queue:
					queue.append(next_link)

		return pages, html_cache

	@staticmethod
	def _extract_listing_cards(html):
		soup = BeautifulSoup(html, "html.parser")
		return soup.select("div.job-tile.search-job-tile")

	def _extract_job_id(self, card, link):
		view_job = card.select_one("a.view-job[id^='jid-']")
		if view_job and view_job.get("id"):
			return self._clean_text(view_job.get("id")).replace("jid-", "", 1)

		title_wrapper = card.select_one("div.job-title[id]")
		if title_wrapper and title_wrapper.get("id"):
			return self._clean_text(title_wrapper.get("id"))

		match = re.search(r"/ref/([^/?#]+)", link or "", flags=re.IGNORECASE)
		if match:
			return self._clean_text(match.group(1)).upper()

		return self._slug_from_url(link)

	def _extract_company_from_detail(self, soup):
		for script in soup.select("script[type='application/ld+json']"):
			raw = (script.string or script.get_text() or "").strip()
			if not raw:
				continue
			try:
				data = json.loads(raw)
			except Exception:
				continue

			if isinstance(data, dict) and data.get("@type") == "JobPosting":
				hiring = data.get("hiringOrganization")
				if isinstance(hiring, dict):
					name = self._clean_text(hiring.get("name"))
					if name:
						return name

		return self.COMPANY

	def _extract_posted_date(self, soup):
		for script in soup.select("script[type='application/ld+json']"):
			raw = (script.string or script.get_text() or "").strip()
			if not raw:
				continue
			try:
				data = json.loads(raw)
			except Exception:
				data = None

			if isinstance(data, dict) and data.get("@type") == "JobPosting":
				date_value = self._clean_text(data.get("datePosted"))
				match = re.match(r"(\d{4}-\d{2}-\d{2})", date_value)
				if match:
					return match.group(1)

			match = re.search(r'"datePosted"\s*:\s*"(\d{4}-\d{2}-\d{2})', raw)
			if match:
				return match.group(1)

		return ""

	def _extract_apply_link(self, detail_soup, detail_url):
		apply_anchor = detail_soup.select_one("a.apply-job[href]")
		if apply_anchor and apply_anchor.get("href"):
			return urljoin(detail_url, apply_anchor.get("href"))
		return ""

	def _extract_detail_description(self, detail_soup, fallback_text=""):
		chunks = []

		for section in detail_soup.select("div#job-description"):
			text = self._normalize_multiline(section.get_text("\n", strip=True))
			if text:
				chunks.append(text)

		if not chunks:
			for cls in [
				"div.job_advert__job-desc-company",
				"div.job_advert__job-desc-role",
				"div.job_advert__job-desc-candidate",
				"div.job_advert__job-desc-deal",
				"div.job-bullet-points",
			]:
				for node in detail_soup.select(cls):
					text = self._normalize_multiline(node.get_text("\n", strip=True))
					if text:
						chunks.append(text)

		if chunks:
			return self._normalize_multiline("\n\n".join(chunks))

		for script in detail_soup.select("script[type='application/ld+json']"):
			raw = (script.string or script.get_text() or "").strip()
			if not raw:
				continue
			try:
				data = json.loads(raw)
			except Exception:
				continue
			if isinstance(data, dict) and data.get("@type") == "JobPosting":
				desc_html = data.get("description")
				if desc_html:
					desc_text = BeautifulSoup(str(desc_html), "html.parser").get_text("\n", strip=True)
					desc_text = self._normalize_multiline(desc_text)
					if desc_text:
						return desc_text

		return self._normalize_multiline(fallback_text)

	def parse_job_listings(self, max_pages=30, max_jobs=None):
		print(f"Fetching Page Executive vacancies from {self.SOURCE_URL}")
		first_html = self._fetch_html(self.SOURCE_URL)
		listing_pages, listing_html_cache = self._collect_listing_pages(first_html, max_pages=max_pages)
		print(f"Listing pages found: {len(listing_pages)}")

		existing_index = {
			(
				str(job.get("source") or "").strip().lower(),
				str(job.get("job_id") or "").strip(),
			): idx
			for idx, job in enumerate(self.jobs)
			if job.get("job_id")
		}

		detail_cache = {}
		seen_ids = set()
		new_count = 0
		updated_count = 0
		parsed_count = 0
		skipped_non_europe = 0
		skipped_existing = 0

		for page_url in listing_pages:
			if max_jobs is not None and len(seen_ids) >= max_jobs:
				break

			html = listing_html_cache.get(page_url)
			if not html:
				try:
					html = self._fetch_html(page_url)
				except Exception as exc:
					print(f"  ! failed listing page fetch: {page_url} ({exc})")
					continue

			cards = self._extract_listing_cards(html)
			print(f"  - cards on page: {len(cards)} | {page_url}")

			for card in cards:
				if max_jobs is not None and len(seen_ids) >= max_jobs:
					break

				title_tag = card.select_one("div.job-title h3 a[href]")
				if not title_tag:
					continue

				title = self._clean_text(title_tag.get_text(" ", strip=True))
				link = urljoin(page_url, self._clean_text(title_tag.get("href")))
				if not link:
					continue

				job_id = self._extract_job_id(card, link)
				if not job_id or job_id in seen_ids:
					continue

				seen_ids.add(job_id)
				key = (self.SOURCE.lower(), job_id)
				if key in existing_index:
					skipped_existing += 1
					continue

				location = self._clean_text(
					(card.select_one("div.job-location") or {}).get_text(" ", strip=True)
					if card.select_one("div.job-location")
					else ""
				)
				if not self._is_europe_location(location):
					skipped_non_europe += 1
					continue
				job_type = self._clean_text(
					(card.select_one("div.job-contract-type") or {}).get_text(" ", strip=True)
					if card.select_one("div.job-contract-type")
					else ""
				)
				salary = self._clean_text(
					(card.select_one("div.job-salary") or {}).get_text(" ", strip=True)
					if card.select_one("div.job-salary")
					else ""
				)

				summary_node = card.select_one("div.job-summary")
				fallback_summary = self._normalize_multiline(
					summary_node.get_text("\n", strip=True) if summary_node else ""
				)

				detail_html = detail_cache.get(link)
				if detail_html is None:
					try:
						detail_html = self._fetch_html(link)
					except Exception as exc:
						print(f"  ! failed detail fetch: {link} ({exc})")
						detail_html = ""
					detail_cache[link] = detail_html

				detail_soup = BeautifulSoup(detail_html, "html.parser") if detail_html else BeautifulSoup("", "html.parser")

				description = self._extract_detail_description(detail_soup, fallback_text=fallback_summary)
				posted_date = self._extract_posted_date(detail_soup)
				apply_link = self._extract_apply_link(detail_soup, link)
				company = self._extract_company_from_detail(detail_soup)

				city = ""
				country = ""
				if location:
					parts = [self._clean_text(p) for p in location.split(",") if self._clean_text(p)]
					if parts:
						city = parts[0]
						country = parts[-1]

				job = {
					"title": title,
					"job_id": job_id,
					"job_seq_no": job_id,
					"link": link,
					"apply_link": apply_link,
					"location": location,
					"city": city,
					"country": country,
					"job_type": job_type,
					"workplace_type": "",
					"posted_date": posted_date,
					"company": company,
					"description": description,
					"status": "active",
					"source": self.SOURCE,
					"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
					"salary": salary,
					"slug": self._slug_from_url(link),
				}

				parsed_count += 1

				self.jobs.append(job)
				existing_index[key] = len(self.jobs) - 1
				new_count += 1

				print(f"  + parsed: {title[:80]}")

		# Keep non-PageExecutive jobs and currently visible PageExecutive jobs.
		self.jobs = [
			j
			for j in self.jobs
			if str(j.get("source") or "").strip().lower() != self.SOURCE.lower()
			or str(j.get("job_id") or "").strip() in seen_ids
		]

		self._save_jobs()
		print("\n" + "=" * 60)
		print(f"Cards parsed        : {parsed_count}")
		print(f"Unique vacancies    : {len(seen_ids)}")
		print(f"New jobs stored     : {new_count}")
		print(f"Jobs updated        : {updated_count}")
		print(f"Existing skipped    : {skipped_existing}")
		print(f"Skipped non-Europe  : {skipped_non_europe}")
		print(f"Total jobs in file  : {len(self.jobs)}")
		print("=" * 60)

	def run(self):
		self.parse_job_listings()
		print(f"Done. Output written to {self.output_file}")


if __name__ == "__main__":
	scrapper = PageExecutiveScrapper()
	scrapper.run()
