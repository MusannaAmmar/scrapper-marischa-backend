import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


class KvScrapper:
	"""
	Scrapes active vacancies from K+V vacancies overview pages.

	Start page:
	  https://kv.nl/vacatures-en-opdrachten/
	"""

	SOURCE_URL = "https://kv.nl/vacatures-en-opdrachten/"
	COMPANY = "K+V"
	SOURCE = "K+V"

	def __init__(self, output_file="json_files/kv_jobs.json"):
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
	def _next_page_url(soup, current_url):
		next_link = soup.select_one(".sd-pagination a.next.page-numbers")
		if not next_link:
			return ""
		return urljoin(current_url, next_link.get("href") or "")

	@staticmethod
	def _extract_job_id(link):
		path = urlparse(link).path.strip("/")
		if not path:
			return ""

		last = path.split("/")[-1]
		match = re.search(r"-(\d+)/?$", path)
		if match:
			return match.group(1)
		return last

	@staticmethod
	def _tile_text(node, selector):
		el = node.select_one(selector)
		if not el:
			return ""
		return el.get_text(" ", strip=True)

	@staticmethod
	def _extract_tile_tags(tile):
		return [
			t.get_text(" ", strip=True)
			for t in tile.select(".sd-vacancy-tags-container .sd-vacancy-tag")
			if t.get_text(" ", strip=True)
		]

	def _extract_description(self, link):
		try:
			html = self._fetch_html(link)
		except Exception:
			return ""

		soup = BeautifulSoup(html, "html.parser")
		candidates = [
			soup.select_one("main"),
			soup.select_one("article"),
			soup.select_one(".entry-content"),
			soup.select_one(".site-main"),
		]

		for node in candidates:
			if not node:
				continue
			text = node.get_text("\n", strip=True)
			text = re.sub(r"\n{3,}", "\n\n", text).strip()
			if len(text) >= 120:
				return text

		return ""

	def _parse_page_tiles(self, html, page_url):
		soup = BeautifulSoup(html, "html.parser")
		tiles = soup.select(".sd-block-vacancy-overview .sd-vacancy-grid a.sd-vacancy-tile")
		results = []

		for tile in tiles:
			link = urljoin(page_url, tile.get("href") or "")
			title = (tile.get("title") or "").strip() or self._tile_text(tile, "h3")
			if not link or not title:
				continue

			tags = self._extract_tile_tags(tile)
			job_type = tags[0] if tags else ""
			location = tags[1] if len(tags) > 1 else ""
			salary = ""
			for tag in tags:
				if "€" in tag or "eur" in tag.lower():
					salary = tag
					break

			results.append(
				{
					"title": title,
					"link": link,
					"job_id": self._extract_job_id(link),
					"job_seq_no": self._extract_job_id(link),
					"location": location,
					"city": location,
					"country": "Netherlands",
					"job_type": job_type,
					"salary": salary,
					"posted_date": "",
					"company": self.COMPANY,
					"category": "",
					"department": "",
					"description": "",
					"skills": [],
					"status": "active",
					"source": self.SOURCE,
					"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
				}
			)

		return results, soup

	def parse_job_listings(self):
		print(f"Fetching K+V jobs from {self.SOURCE_URL}")

		existing_index = {
			str(job.get("job_id")): idx
			for idx, job in enumerate(self.jobs)
			if job.get("job_id")
		}

		visited_pages = set()
		seen_job_ids = set()
		page_url = self.SOURCE_URL
		page_count = 0
		new_count = 0
		updated_count = 0
		duplicate_tiles = 0
		skipped_existing = 0

		while page_url and page_url not in visited_pages:
			visited_pages.add(page_url)
			page_count += 1
			print(f"Parsing listing page {page_count}: {page_url}")

			html = self._fetch_html(page_url)
			parsed_jobs, soup = self._parse_page_tiles(html, page_url)
			print(f"  Tiles found: {len(parsed_jobs)}")

			for job in parsed_jobs:
				job_id = str(job.get("job_id") or "").strip()
				if not job_id:
					continue

				if job_id in seen_job_ids:
					duplicate_tiles += 1
					continue
				seen_job_ids.add(job_id)

				if job_id in existing_index:
					skipped_existing += 1
					continue

				job["description"] = self._extract_description(job.get("link") or "")
				self.jobs.append(job)
				existing_index[job_id] = len(self.jobs) - 1
				new_count += 1

				print(f"  + {job['title'][:90]} | {job.get('location', '')}")

			next_page = self._next_page_url(soup, page_url)
			page_url = next_page if next_page and next_page not in visited_pages else ""

		# Keep only active records for this source from the current crawl.
		self.jobs = [
			j
			for j in self.jobs
			if str(j.get("source") or "").strip().lower() != self.SOURCE.lower()
			or str(j.get("job_id") or "") in seen_job_ids
		]

		self._save_jobs()
		print("\n" + "=" * 60)
		print(f"Pages parsed          : {page_count}")
		print(f"Unique jobs seen      : {len(seen_job_ids)}")
		print(f"Duplicate tiles skipped: {duplicate_tiles}")
		print(f"New jobs stored       : {new_count}")
		print(f"Jobs updated          : {updated_count}")
		print(f"Existing skipped      : {skipped_existing}")
		print(f"Total jobs in file    : {len(self.jobs)}")
		print("=" * 60)

	def run(self):
		self.parse_job_listings()
		print(f"Done. Output written to {self.output_file}")


if __name__ == "__main__":
	scrapper = KvScrapper()
	scrapper.run()
