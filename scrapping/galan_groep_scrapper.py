import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup


class GalanGroepScrapper:
	"""
	Parses Galan Groep vacancies from the public WordPress API.
	"""

	SOURCE_URL = "https://vacatures.galangroep.nl/"
	POSTS_API_URL = "https://vacatures.galangroep.nl/wp-json/wp/v2/posts"
	COMPANY = "De Galan Groep"
	SOURCE = "Galan Groep"

	def __init__(self, output_file="json_files/galan_groep_jobs.json"):
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
	def _clean_text(value):
		return " ".join(str(value or "").replace("\xa0", " ").split()).strip()

	@staticmethod
	def _strip_html(value):
		if not value:
			return ""
		text = BeautifulSoup(str(value), "html.parser").get_text(" ", strip=True)
		return re.sub(r"\s+", " ", text).strip()

	@staticmethod
	def _slug_from_url(url):
		path = (urlparse(url).path or "").strip("/")
		return path.split("/")[-1] if path else ""

	@staticmethod
	def _status_from_title(title):
		title_l = (title or "").lower()
		if "reactietermijn-gesloten" in title_l or "reactietermijn gesloten" in title_l:
			return "inactive"
		if "gesloten" in title_l:
			return "inactive"
		return "active"

	@staticmethod
	def _extract_class_value(classes, prefix):
		for cls in classes:
			if cls.startswith(prefix):
				return cls.replace(prefix, "").replace("-", " ").strip()
		return ""

	def _fetch_posts_page(self, page=1, per_page=100):
		headers = {
			"User-Agent": "Mozilla/5.0",
		}
		url = f"{self.POSTS_API_URL}?page={page}&per_page={per_page}&status=publish"
		resp = requests.get(url, headers=headers, timeout=60)
		resp.raise_for_status()
		posts = resp.json() if isinstance(resp.json(), list) else []
		total_pages = int(resp.headers.get("X-WP-TotalPages", "1") or "1")
		return posts, total_pages

	def parse_job_listings(self):
		print(f"Parsing Galan Groep jobs from API: {self.POSTS_API_URL}")
		try:
			all_posts = []
			page = 1
			total_pages = 1
			while page <= total_pages:
				posts, total_pages = self._fetch_posts_page(page=page)
				all_posts.extend(posts)
				page += 1
		except Exception as exc:
			print(f"Could not load source API: {exc}")
			print("Keeping existing jobs unchanged.")
			return

		if not all_posts:
			print("No vacancy posts found")
			return

		print(f"Vacancy posts found: {len(all_posts)}")

		existing_index = {
			str(job.get("job_id")): idx
			for idx, job in enumerate(self.jobs)
			if job.get("job_id")
		}

		seen_ids = set()
		new_count = 0
		updated_count = 0

		for post in all_posts:
			classes = post.get("class_list") or []
			if "category-vacatures" not in classes:
				continue

			link = self._clean_text(post.get("link"))
			title = self._clean_text(self._strip_html(((post.get("title") or {}).get("rendered"))))
			if not link or not title:
				continue

			slug = self._clean_text(post.get("slug")) or self._slug_from_url(link)
			if not slug:
				continue

			job_seq_no = str(post.get("id") or slug)
			job_id = slug

			description = self._strip_html(((post.get("excerpt") or {}).get("rendered")))
			posted_date_raw = self._clean_text(post.get("date"))
			posted_date = posted_date_raw[:10] if len(posted_date_raw) >= 10 else ""

			location = self._extract_class_value(classes, "worklocation-")
			country_tag = self._extract_class_value(classes, "country-")
			job_type = self._extract_class_value(classes, "employment-contract-")
			category = self._extract_class_value(classes, "procedure-")
			department = self._extract_class_value(classes, "business-line-")
			client_company = ""

			if country_tag.lower() == "nederland":
				country = "Netherlands"
			else:
				country = country_tag.title() if country_tag else ""

			city = location.title() if location else ""

			job = {
				"title": title,
				"job_id": job_id,
				"job_seq_no": job_seq_no,
				"link": link,
				"apply_link": link,
				"location": location,
				"city": city,
				"country": country,
				"job_type": job_type,
				"workplace_type": "",
				"posted_date": posted_date,
				"company": self.COMPANY,
				"category": category,
				"department": department,
				"description": description,
				"skills": [],
				"status": self._status_from_title(title),
				"source": self.SOURCE,
				"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
				"slug": slug,
				"client_company": client_company,
			}

			seen_ids.add(job_id)

			if job_id in existing_index:
				idx = existing_index[job_id]
				self.jobs[idx] = {**self.jobs[idx], **job}
				updated_count += 1
			else:
				self.jobs.append(job)
				existing_index[job_id] = len(self.jobs) - 1
				new_count += 1

			print(f"  + parsed: {title[:80]}")

		# Keep non-Galan jobs and currently visible Galan jobs.
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
		print(f"Total jobs in file  : {len(self.jobs)}")
		print("=" * 60)

	def run(self):
		self.parse_job_listings()
		print(f"Done. Output written to {self.output_file}")


if __name__ == "__main__":
	scrapper = GalanGroepScrapper()
	scrapper.run()
