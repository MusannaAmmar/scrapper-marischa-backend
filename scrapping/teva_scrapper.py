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


class TevaScrapper:
    ZENROWS_API = "https://api.zenrows.com/v1/"

    def __init__(self, apikey=os.getenv("ZENROWS"), output_file="json_files/teva_jobs.json"):
        self.apikey = apikey
        self.output_file = output_file
        self.jobs = []
        self.new_job_ids = set()
        self._load_existing_jobs()

        self.base_url = "https://careers.teva"
        self.search_url = (
            f"{self.base_url}/search/"
            "?q=&q2=&alertId=&locationsearch=&geolocation="
            "&searchby=location&d=10&lat=&lon=&title=&shifttype="
            "&facility=&location=&department=netherlands#searchresults"
        )

    def _load_existing_jobs(self):
        if os.path.exists(self.output_file):
            with open(self.output_file, "r", encoding="utf-8") as f:
                self.jobs = json.load(f)
            print(f"Loaded {len(self.jobs)} existing jobs from {self.output_file}")
        else:
            self.jobs = []

    def _get_existing_job_ids(self):
        return {job["job_id"] for job in self.jobs if job.get("job_id")}

    def _fetch_page(self, url, wait="5000", debug_file=None):
        if not self.apikey:
            print("Missing ZENROWS API key in environment.")
            return None

        params = {
            "url": url,
            "apikey": self.apikey,
            "js_render": "true",
            "wait": wait,
            "premium_proxy": "true",
        }

        try:
            response = requests.get(self.ZENROWS_API, params=params, timeout=90)
            if response.status_code == 200:
                if debug_file:
                    with open(debug_file, "w", encoding="utf-8") as f:
                        f.write(response.text)
                    print(f"  Debug HTML saved to {debug_file}")
                return response.text

            print(f"  ZenRows returned status {response.status_code}: {response.text[:200]}")
            return None
        except Exception as e:
            print(f"  Error fetching {url}: {e}")
            return None

    @staticmethod
    def _clean_text(value):
        return re.sub(r"\s+", " ", value or "").strip()

    def _parse_listing_rows(self, html):
        soup = BeautifulSoup(html, "html.parser")
        rows = soup.select("table#searchresults tr.data-row")
        parsed_jobs = []

        for row in rows:
            title_link = (
                row.select_one("span.jobTitle.hidden-phone a.jobTitle-link")
                or row.select_one("a.jobTitle-link")
            )
            if not title_link:
                continue

            job_id_elem = row.select_one("span.jobShifttype")
            category_elem = row.select_one("span.jobFacility")
            location_elem = row.select_one("td.colLocation span.jobLocation")
            country_elem = row.select_one("span.jobDepartment")

            href = title_link.get("href", "")
            link = urljoin(self.base_url, href)
            title = self._clean_text(title_link.get_text(" ", strip=True))
            job_seq_no = self._clean_text(job_id_elem.get_text(" ", strip=True)) if job_id_elem else ""
            category = self._clean_text(category_elem.get_text(" ", strip=True)) if category_elem else ""
            location = self._clean_text(location_elem.get_text(" ", strip=True)) if location_elem else ""
            country = self._clean_text(country_elem.get_text(" ", strip=True)) if country_elem else "Netherlands"

            url_id = link.rstrip("/").split("/")[-1] if link else ""
            job_id = job_seq_no or url_id
            city = location.split(",")[0].strip() if location else ""

            parsed_jobs.append(
                {
                    "title": title,
                    "job_id": job_id,
                    "job_seq_no": job_seq_no or job_id,
                    "link": link,
                    "location": location,
                    "city": city,
                    "country": country or "Netherlands",
                    "job_type": "",
                    "posted_date": "",
                    "company": "Teva Pharmaceuticals",
                    "category": category,
                    "department": country or "Netherlands",
                    "description": "",
                    "skills": [],
                    "status": "active",
                    "source": "teva pharmaceuticals",
                    "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                }
            )

        return parsed_jobs

    def parse_job_listings(self, debug=False):
        print("Fetching Teva job listings...")
        html = self._fetch_page(
            self.search_url,
            wait="5000",
            debug_file="teva_debug.html" if debug else None,
        )
        if not html:
            print("Failed to fetch search page.")
            return

        jobs = self._parse_listing_rows(html)
        print(f"Found {len(jobs)} job rows")

        existing_ids = self._get_existing_job_ids()
        self.new_job_ids = set()
        new_count = 0
        skipped_count = 0

        for job in jobs:
            job_id = job.get("job_id")
            if not job_id:
                continue

            if job_id in existing_ids:
                skipped_count += 1
                continue

            self.jobs.append(job)
            existing_ids.add(job_id)
            self.new_job_ids.add(job_id)
            new_count += 1
            print(f"  + {job['title']} | {job['location']} | {job_id}")

        self._save_jobs()
        print(f"\n{'=' * 60}")
        print(f"  New jobs found    : {new_count}")
        print(f"  Duplicates skipped: {skipped_count}")
        print(f"  Total jobs stored : {len(self.jobs)}")
        print(f"{'=' * 60}")

    @staticmethod
    def _extract_label_value(soup, label_text):
        blocks = soup.find_all("div", class_="col-xs-12 fontalign-left")
        for block in blocks:
            label = block.find("span", class_="joblayouttoken-label")
            if label and label_text.lower() in label.get_text(" ", strip=True).lower():
                value = block.select_one("span.rtltextaligneligible span") or block.select_one(
                    "span.rtltextaligneligible"
                )
                return TevaScrapper._clean_text(value.get_text(" ", strip=True)) if value else ""
        return ""

    def fetch_job_descriptions(self, delay=8):
        jobs_to_update = [
            job
            for job in self.jobs
            if str(job.get("job_id") or "") in self.new_job_ids
            and (not job.get("description") or len(job.get("description", "")) < 300)
        ]

        if not jobs_to_update:
            print("\nAll jobs already have descriptions.")
            return

        print(f"\nFetching descriptions for {len(jobs_to_update)} jobs...")

        success_count = 0
        failed_count = 0
        total_words_saved = 0

        for i, job in enumerate(jobs_to_update):
            link = job.get("link")
            if not link:
                failed_count += 1
                continue

            print(f"\n[{i + 1}/{len(jobs_to_update)}] {job['title']}")

            for attempt in range(2):
                try:
                    wait_time = "8000" if attempt == 0 else "12000"
                    html = self._fetch_page(link, wait=wait_time)
                    if not html:
                        print(f"  Attempt {attempt + 1}: no HTML returned")
                        time.sleep(3)
                        continue

                    soup = BeautifulSoup(html, "html.parser")

                    title_elem = soup.select_one('h1 span[itemprop="title"]') or soup.select_one("h1")
                    location_elem = soup.find("span", class_="jobGeoLocation")
                    company = self._extract_label_value(soup, "Company")

                    if title_elem:
                        job["title"] = self._clean_text(title_elem.get_text(" ", strip=True))
                    if location_elem:
                        job["location"] = self._clean_text(location_elem.get_text(" ", strip=True))
                        job["city"] = job["location"].split(",")[0].strip()
                    if company:
                        job["company"] = company

                    desc_elem = (
                        soup.select_one('[itemprop="description"]')
                        or soup.find("div", class_=re.compile(r"job.?description|jobDisplay", re.I))
                        or soup.find("article")
                        or soup.find("main")
                    )

                    if not desc_elem:
                        print(f"  Attempt {attempt + 1}: description element not found")
                        time.sleep(3)
                        continue

                    for unwanted in desc_elem(["script", "style", "noscript", "form", "button"]):
                        unwanted.decompose()

                    raw_text = desc_elem.get_text(separator="\n", strip=True)

                    if raw_text and len(raw_text) >= 120:
                        previous = str(job.get("description") or "")
                        if not previous or len(raw_text) >= len(previous):
                            job["description"] = raw_text
                        success_count += 1
                        total_words_saved += len(raw_text.split())
                        break

                    print(f"  Attempt {attempt + 1}: description too short")
                    time.sleep(3)

                except Exception as e:
                    print(f"  Attempt {attempt + 1} error: {e}")
                    time.sleep(3)
            else:
                failed_count += 1

            if (i + 1) % 3 == 0:
                self._save_jobs()
                print(f"  Saved progress ({success_count}/{i + 1} successful)")

            time.sleep(delay)

        self._save_jobs()

        print(f"\n{'=' * 60}")
        print("Description Fetching Complete!")
        print(f"  Successful : {success_count}")
        print(f"  Failed     : {failed_count}")
        if total_words_saved > 0:
            print(f"  Words saved: ~{total_words_saved:,} ({int(total_words_saved * 0.00075)} tokens)")
        print(f"{'=' * 60}")

    def _save_jobs(self):
        output_dir = os.path.dirname(self.output_file)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        with open(self.output_file, "w", encoding="utf-8") as f:
            json.dump(self.jobs, f, indent=2, ensure_ascii=False)

    def run(self, fetch_descriptions=True, debug=False):
        self.parse_job_listings(debug=debug)
        if fetch_descriptions:
            self.fetch_job_descriptions()


if __name__ == "__main__":
    print("Starting Teva Pharmaceuticals job scraper...")
    print("=" * 60)

    scraper = TevaScrapper()
    scraper.run(fetch_descriptions=True, debug=False)

    print("\n" + "=" * 60)
    print(f"Done! Total jobs: {len(scraper.jobs)}")
    print("=" * 60)
