import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv


load_dotenv()


class QLMScrapper:
    ZENROWS_API = "https://api.zenrows.com/v1/"
    ZENROWS_API_KEY = os.getenv("ZENROWS")

    def __init__(self, output_file="json_files/qlm_jobs.json"):
        self.output_file = output_file
        self.url = "https://www.qlmsearch.com/job-search"
        self.jobs = self._load_existing_jobs()

    def _load_existing_jobs(self):
        if os.path.exists(self.output_file):
            with open(self.output_file, "r", encoding="utf-8") as f:
                jobs = json.load(f)
            print(f"Loaded {len(jobs)} existing jobs from {self.output_file}")
            return jobs
        return []

    def _save_jobs(self):
        with open(self.output_file, "w", encoding="utf-8") as f:
            json.dump(self.jobs, f, indent=2, ensure_ascii=False)

    @staticmethod
    def _clean_text(value):
        if not value:
            return ""
        return re.sub(r"\s+", " ", str(value)).strip()

    @staticmethod
    def _extract_job_id(link):
        if not link:
            return None
        match = re.search(r"/job-search/([^/?#]+)", link, flags=re.IGNORECASE)
        return match.group(1).strip().lower() if match else None

    def fetch_via_zenrows(self, url):
        if not self.ZENROWS_API_KEY:
            print("Missing ZENROWS API key in environment.")
            return None

        params = {
            "apikey": self.ZENROWS_API_KEY,
            "url": url,
            "js_render": "true",
            "wait": "4000",
        }

        try:
            response = requests.get(self.ZENROWS_API, params=params, timeout=90)
            if response.status_code == 200:
                return response.text
            print(f"ZenRows API error: {response.status_code} - {response.text[:300]}")
            return None
        except Exception as exc:
            print(f"ZenRows request failed: {exc}")
            return None

    def _extract_listing_jobs(self, html):
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select("div.job-search_active-search-item.w-dyn-item")
        parsed_jobs = []

        for card in cards:
            title_nodes = card.select("div.job-search_role div.job-search_title-text")

            title = self._clean_text(title_nodes[0].get_text(" ", strip=True)) if len(title_nodes) > 0 else ""
            location = self._clean_text(title_nodes[2].get_text(" ", strip=True)) if len(title_nodes) > 2 else ""

            link_tag = card.select_one("div.job-search_description-right a.button.is-apply[href]")
            link = urljoin(self.url, link_tag.get("href", "").strip()) if link_tag else ""
            job_id = self._extract_job_id(link)

            desc_node = card.select_one("div.job-search_description-left div.text-rich-text.w-richtext")
            description = self._clean_text(desc_node.get_text(" ", strip=True)) if desc_node else ""

            if not (title and link and job_id):
                continue

            parsed_jobs.append(
                {
                    "title": title,
                    "job_id": job_id,
                    "link": link,
                    "location": location,
                    "description": description,
                    "status": "active",
                    "source": "QLM Search",
                    "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                }
            )

        return parsed_jobs

    def parse_job_listings(self):
        print("\nFetching QLM Search listings...")
        html = self.fetch_via_zenrows(self.url)
        if not html:
            print("No HTML returned from QLM Search page.")
            return self.jobs

        listing_jobs = self._extract_listing_jobs(html)
        if not listing_jobs:
            print("No jobs parsed from QLM Search HTML.")
            return self.jobs

        existing_index = {
            str(job.get("job_id")): idx
            for idx, job in enumerate(self.jobs)
            if job.get("job_id")
        }

        seen_ids = set()
        new_count = 0
        updated_count = 0

        for job in listing_jobs:
            job_id = str(job.get("job_id"))
            seen_ids.add(job_id)

            if job_id in existing_index:
                idx = existing_index[job_id]
                existing_job = self.jobs[idx]
                self.jobs[idx] = {**existing_job, **job}
                updated_count += 1
            else:
                self.jobs.append(job)
                existing_index[job_id] = len(self.jobs) - 1
                new_count += 1

        expired_count = 0
        for job in self.jobs:
            if str(job.get("source", "")).lower() == "qlm search":
                jid = str(job.get("job_id") or "")
                if jid and jid not in seen_ids:
                    job["status"] = "expired"
                    expired_count += 1

        self._save_jobs()
        print("\n" + "=" * 60)
        print("QLM Search scraping done")
        print(f"New jobs       : {new_count}")
        print(f"Updated jobs   : {updated_count}")
        print(f"Expired marked : {expired_count}")
        print(f"Total stored   : {len(self.jobs)}")
        print("=" * 60)
        return self.jobs

    def run(self):
        return self.parse_job_listings()


if __name__ == "__main__":
    scraper = QLMScrapper()
    data = scraper.run()
    print(f"Saved {len(data)} jobs to {scraper.output_file}")