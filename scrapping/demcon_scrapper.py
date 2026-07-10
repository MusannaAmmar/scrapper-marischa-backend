import os
import re
import json
import requests
from urllib.parse import urljoin
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from datetime import datetime, timezone


load_dotenv()


class DemonScrapper:
    ZENROWS_API_KEY = os.getenv("ZENROWS")
    ZENROWS_API = "https://api.zenrows.com/v1/"

    def __init__(self, output_file="json_files/demcon_jobs.json"):
        self.url = "https://careersatdemcon.com/vacancies"
        self.base_url = "https://careersatdemcon.com"
        self.output_file = output_file
        self.source_name = "Demcon Enschede"
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

    def fetch_via_zenrows(self, url, render=False):
        params = {
            "url": url,
            "apikey": self.ZENROWS_API_KEY,
        }
        if render:
            params["js_render"] = "true"
            params["wait"] = "2000" # Give Nuxt time to hydrate

        try:
            response = requests.get(self.ZENROWS_API, params=params, timeout=90)
            if response.status_code == 200:
                return response.text
            else:
                print(f"ZenRows returned status {response.status_code} for {url}")
        except requests.RequestException as e:
            print(f"Error fetching URL via ZenRows: {e}")
        return None

    @staticmethod
    def _extract_job_id_from_url(url):
        match = re.search(r"/vacancy/(\d+)/", url)
        return match.group(1) if match else None

    def _extract_vacancy_hrefs_from_html(self, html):
        """Fallback: uses BeautifulSoup to extract links directly from the rendered HTML."""
        hrefs = set()
        soup = BeautifulSoup(html, 'html.parser')
        
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if "/vacancy/" in href:
                full_url = urljoin(self.base_url, href)
                # Filter out 'Apply' and 'Open Position' utility links
                if not any(x in full_url.lower() for x in ["/apply", "open-vacancy"]):
                    hrefs.add(full_url)

        return hrefs

    def extract_hrefs(self):
        # 1. Fetch main page with Javascript rendering to ensure links are present
        html = self.fetch_via_zenrows(self.url, render=True)
        if not html:
            return []

        hrefs = set()
        hrefs.update(self._extract_vacancy_hrefs_from_html(html))

        return sorted(list(hrefs))

    def extract_title(self, url):
        try:
            job_id = self._extract_job_id_from_url(url)
            html = self.fetch_via_zenrows(url, render=True)
            if not html:
                raise ValueError("No HTML returned for detail page")

            soup = BeautifulSoup(html, 'html.parser')
            h1 = soup.find('h1', class_='title') or soup.find('h1')
            title = h1.get_text(strip=True) if h1 else ""

            location_node = soup.find('div', class_='tags__details location')
            location = location_node.get_text(" ", strip=True) if location_node else ""

            description_parts = []
            for p in soup.find_all('p'):
                text = p.get_text(" ", strip=True)
                if text:
                    description_parts.append(text)
            description = "\n".join(description_parts).strip()

            return {
                "job_id": job_id,
                "title": title,
                "link": url,
                "apply_link": url,
                "location": location,
                "description": description,
                "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "status": "active",
                "source": self.source_name,
            }
        except Exception as e:
            print(f"Error extracting title from {url}: {e}")
            return None

    def parse_job_listings(self):
        print("\nFetching Demcon job listings...")
        print(f"Source URL: {self.url}")

        hrefs = self.extract_hrefs()
        if not hrefs:
            print("No vacancy links found.")
            return self.jobs

        print(f"Found {len(hrefs)} unique detail links")

        existing_index = {
            str(job.get("job_id")): idx
            for idx, job in enumerate(self.jobs)
            if job.get("job_id")
        }
        seen_ids = set()

        new_count = 0
        updated_count = 0
        skipped_existing_count = 0
        failed_count = 0

        for i, link in enumerate(hrefs, start=1):
            job_id = self._extract_job_id_from_url(link)
            if not job_id:
                failed_count += 1
                print(f"  [{i}/{len(hrefs)}] Skipped: no job_id in link {link}")
                continue

            seen_ids.add(job_id)

            if job_id in existing_index:
                skipped_existing_count += 1
                print(f"  [{i}/{len(hrefs)}] Skipped existing job_id={job_id}")
                continue

            print(f"  [{i}/{len(hrefs)}] Processing job_id={job_id}")
            job = self.extract_title(link)
            if not job:
                failed_count += 1
                continue

            self.jobs.append(job)
            existing_index[job_id] = len(self.jobs) - 1
            new_count += 1
            print(f"    + New: {job.get('title', '')[:80]}")

        expired_count = 0
        for job in self.jobs:
            if str(job.get("source", "")).lower() == self.source_name.lower():
                jid = str(job.get("job_id") or "")
                if jid and jid not in seen_ids:
                    job["status"] = "expired"
                    expired_count += 1

        self._save_jobs()
        print("\n" + "=" * 60)
        print("Demcon scraping done")
        print(f"New jobs        : {new_count}")
        print(f"Updated jobs    : {updated_count}")
        print(f"Skipped existing: {skipped_existing_count}")
        print(f"Expired marked  : {expired_count}")
        print(f"Failed/skipped  : {failed_count}")
        print(f"Total stored    : {len(self.jobs)}")
        print("=" * 60)
        return self.jobs

    def run(self):
        return self.parse_job_listings()


if __name__ == "__main__":
    scraper = DemonScrapper()
    data = scraper.run()
    print(f"Saved {len(data)} jobs to {scraper.output_file}")