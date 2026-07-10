import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup


class EnddeblauwScrapper:
	"""
	Scrapes vacancies from &deBlauw and stores normalized job entries.

	Source URL:
	  https://endeblauw.com/en/vacancies-career-opportunites-deblauw/

	Vacancy cards are rendered as:
	  <article class="vacancy block-link" id="vacancy-..."> ...
	"""

	SOURCE_URL = "https://endeblauw.com/en/vacancies-career-opportunites-deblauw/"
	COMPANY = "&deBlauw"
	SOURCE = "Enddeblauw"

	def __init__(self, output_file="json_files/enddeblauw_jobs.json"):
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

	def _fetch_html(self, url=None):
		target_url = url or self.SOURCE_URL
		resp = requests.get(target_url, headers=self._headers(), timeout=60)
		resp.raise_for_status()
		return resp.text

	@staticmethod
	def _slug_from_url(url):
		if not url:
			return ""
		path = urlparse(url).path.strip("/")
		return path.split("/")[-1] if path else ""

	@staticmethod
	def _clean_text(value):
		return " ".join(str(value or "").split()).strip()

	@staticmethod
	def _normalize_multiline(text):
		if not text:
			return ""
		text = text.replace("\r", "")
		text = re.sub(r"\n{3,}", "\n\n", text)
		return text.strip()

	def _parse_cards(self, html):
		soup = BeautifulSoup(html, "html.parser")
		return soup.select("article.vacancy.block-link")

	def _extract_description(self, html):
		soup = BeautifulSoup(html, "html.parser")

		sections = []
		for block in soup.select("main.vacancy-detail div.flex-grid__main div.copy"):
			text = block.get_text(separator="\n", strip=True)
			text = self._normalize_multiline(text)
			if text and len(text) > 80:
				sections.append(text)

		if sections:
			return self._normalize_multiline("\n\n".join(sections))

		meta_desc = soup.select_one("meta[name='description']")
		if meta_desc:
			return self._clean_text(meta_desc.get("content") or "")

		return ""

	@staticmethod
	def _extract_tags(article):
		tags = []
		for span in article.select("ul.vacancy__tags span.tag"):
			value = " ".join(span.get_text(" ", strip=True).split())
			if value:
				tags.append(value)
		return tags

	@staticmethod
	def _extract_meta_labels(article):
		labels = []
		for span in article.select("ul.vacancy__meta span.meta-tag__label"):
			value = " ".join(span.get_text(" ", strip=True).split())
			if value:
				labels.append(value)
		return labels

	def parse_job_listings(self):
		print(f"Fetching Enddeblauw jobs from {self.SOURCE_URL}")
		html = self._fetch_html()
		cards = self._parse_cards(html)

		if not cards:
			print("No vacancy cards found")
			return

		print(f"Vacancy cards found: {len(cards)}")

		existing_index = {
			str(job.get("job_id")): idx
			for idx, job in enumerate(self.jobs)
			if job.get("job_id")
		}

		seen_ids = set()
		new_count = 0
		updated_count = 0
		skipped_existing = 0
		description_cache = {}

		for article in cards:
			article_id = str(article.get("id") or "").strip()
			job_id = article_id.replace("vacancy-", "").strip()
			if not job_id:
				continue

			seen_ids.add(job_id)
			if job_id in existing_index:
				skipped_existing += 1
				continue

			title_el = article.select_one("h2.vacancy__title")
			title = self._clean_text(title_el.get_text(" ", strip=True) if title_el else "")
			anchor = article.select_one("a.block-link__anchor")
			link = self._clean_text(anchor.get("href")) if anchor else ""
			slug = self._slug_from_url(link)

			description = ""
			if link:
				if link in description_cache:
					description = description_cache[link]
				else:
					try:
						detail_html = self._fetch_html(link)
						description = self._extract_description(detail_html)
					except Exception as exc:
						print(f"  ! failed detail fetch for {link}: {exc}")
					description_cache[link] = description

			tags = self._extract_tags(article)
			category = tags[0] if len(tags) >= 1 else ""
			department = tags[1] if len(tags) >= 2 else ""

			meta_labels = self._extract_meta_labels(article)
			client_company = meta_labels[0] if len(meta_labels) >= 1 else ""
			location = meta_labels[1] if len(meta_labels) >= 2 else ""
			city = location.split(",")[0].strip() if location else ""

			job = {
				"title": title,
				"job_id": job_id,
				"job_seq_no": job_id,
				"link": link,
				"apply_link": link,
				"location": location,
				"city": city,
				"country": "",
				"job_type": "",
				"workplace_type": "",
				"posted_date": "",
				"company": self.COMPANY,
				"category": category,
				"department": department,
				"description": description,
				# "skills": [],
				"status": "active",
				"source": self.SOURCE,
				"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
				"slug": slug,
				"client_company": client_company,
			}

			self.jobs.append(job)
			existing_index[job_id] = len(self.jobs) - 1
			new_count += 1

			print(f"  + parsed: {title[:80]} | {location}")

		# Keep non-Enddeblauw jobs and only currently visible Enddeblauw jobs.
		self.jobs = [
			j for j in self.jobs
			if str(j.get("source") or "").strip().lower() != self.SOURCE.lower()
			or str(j.get("job_id") or "").strip() in seen_ids
		]

		self._save_jobs()
		print("\n" + "=" * 60)
		print(f"Vacancies parsed    : {len(seen_ids)}")
		print(f"New jobs stored     : {new_count}")
		print(f"Jobs updated        : {updated_count}")
		print(f"Existing skipped    : {skipped_existing}")
		print(f"Total jobs in file  : {len(self.jobs)}")
		print("=" * 60)

	def run(self):
		self.parse_job_listings()
		print(f"Done. Output written to {self.output_file}")


if __name__ == "__main__":
	scrapper = EnddeblauwScrapper()
	scrapper.run()
