import os
import json
import time
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()
import re

class HeidrickScraper:
    """
    Scrapes Heidrick & Struggles job listings via the Workday JSON API.
    No ZenRows / JS rendering needed — Workday exposes a REST endpoint.
    """

    WORKDAY_BASE = 'https://heidrick.wd1.myworkdayjobs.com'
    TENANT = 'heidrickandstruggles'
    COMPANY = 'heidrick'

    # Workday facet ID for Netherlands
    COUNTRY_ID = 'dcc5b7608d8644b3a93716604e78e995'

    JOBS_API = f'{WORKDAY_BASE}/wday/cxs/{COMPANY}/{TENANT}/jobs'

    def __init__(self, output_file='json_files/heidrick_jobs.json'):
        self.output_file = output_file
        self.jobs = []
        self._load_existing_jobs()

    # ------------------------------------------------------------------ #
    #  Persistence helpers                                                 #
    # ------------------------------------------------------------------ #

    def _load_existing_jobs(self):
        if os.path.exists(self.output_file):
            with open(self.output_file, 'r', encoding='utf-8') as f:
                self.jobs = json.load(f)
            print(f"Loaded {len(self.jobs)} existing jobs from {self.output_file}")
        else:
            self.jobs = []

    def _get_existing_job_ids(self):
        return {job['job_id'] for job in self.jobs if job.get('job_id')}

    def _save_jobs(self):
        with open(self.output_file, 'w', encoding='utf-8') as f:
            json.dump(self.jobs, f, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------ #
    #  Workday API helpers                                                 #
    # ------------------------------------------------------------------ #

    def _post_jobs(self, offset=0, limit=20):
        """POST to Workday jobs API and return parsed JSON (or None on error)."""
        headers = {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
        }
        payload = {
            'appliedFacets': {'Country': [self.COUNTRY_ID]},
            'limit': limit,
            'offset': offset,
            'searchText': '',
        }
        try:
            resp = requests.post(self.JOBS_API, json=payload, headers=headers, timeout=30)
            if resp.status_code == 200:
                return resp.json()
            print(f"  [warn] jobs API returned {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            print(f"  [error] jobs API: {e}")
        return None

    def _get_job_detail(self, url):
        """GET job detail from Workday and return parsed JSON (or None on error)."""
        # url = f'{self.WORKDAY_BASE}/wday/cxs/{self.COMPANY}/{self.TENANT}{external_path}'
        headers = {'Accept': 'application/json'}
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code == 200:
                return resp.json()
            print(f"  [warn] detail API returned {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            print(f"  [error] detail API for {url}: {e}")
        return None

    # ------------------------------------------------------------------ #
    #  Scraping                                                            #
    # ------------------------------------------------------------------ #

    def parse_job_listings(self):
        """Page through the Workday API and collect all job listings."""
        print("Fetching Heidrick & Struggles job listings...")

        existing_ids = self._get_existing_job_ids()
        total_new = 0
        total_skipped = 0
        limit = 20
        offset = 0

        while True:
            print(f"\n  Fetching offset={offset} ...")
            data = self._post_jobs(offset=offset, limit=limit)
            if not data:
                print("  Failed to fetch page — stopping.")
                break

            postings = data.get('jobPostings', [])
            total_count = data.get('total', 0)
            print(f"  Total available: {total_count} | This page: {len(postings)}")

            if not postings:
                break
            

            new_count = 0
            skipped_count = 0

            for posting in postings:
                try:
                    external_path = posting.get('externalPath', '')
                    # Use externalPath stripped of leading slash as stable ID
                    # Extract just the R-number from the slug, e.g. "job/Bremen/Engagement-Leader_R2516847-1" → "R2516847-1"
                    match = re.search(r'_(R\d+(?:-\d+)?)', external_path)
                    job_id = match.group(1) if match else external_path.lstrip('/')

                    if not job_id:
                        continue

                    if job_id in existing_ids:
                        skipped_count += 1
                        total_skipped += 1
                        continue

                    title = posting.get('title', '').strip()
                    location = posting.get('locationsText', '').strip()
                    posted_date = posting.get('postedOn', '').strip()
                    # bulletFields may contain employment type / seniority
                    bullet_fields = posting.get('bulletFields', [])
                    job_type = " "

                    full_link = f'{self.WORKDAY_BASE}/en-US/{self.TENANT}{external_path}'

                    job = {
                        'title': title,
                        'job_id': job_id,
                        'job_seq_no': job_id,
                        'link': full_link,
                        'location': location,
                        'city': location,
                        'country': '',  # filled in from detail API
                        'job_type': job_type,
                        'posted_date': posted_date,
                        'salary': '',
                        'company': 'Heidrick & Struggles',
                        'category': '',
                        'department': '',
                        'description': '',
                        'description_fetched': False,
                        'skills': [],
                        'status': 'active',
                        'source': 'heidrick',
                        'date': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                    }

                    self.jobs.append(job)
                    existing_ids.add(job_id)
                    new_count += 1
                    total_new += 1
                    print(f"  + {title} | {location}")

                except Exception as e:
                    print(f"  Error parsing posting: {e}")
                    continue

            skipped_count = limit - new_count - skipped_count  # recalc
            self._save_jobs()
            print(f"  New: {new_count}, Skipped (duplicates): {total_skipped}")

            offset += limit
            if offset >= total_count:
                break

            time.sleep(1)  # be polite between pages

        print(f"\n{'='*60}")
        print(f"  New jobs found    : {total_new}")
        print(f"  Duplicates skipped: {total_skipped}")
        print(f"  Total jobs stored : {len(self.jobs)}")
        print(f"{'='*60}")

    def fetch_job_descriptions(self, delay=2):
        """Fetch full description for jobs that don't have one yet."""
        jobs_to_update = [j for j in self.jobs if not j.get('description_fetched', False)]

        if not jobs_to_update:
            print("\nAll jobs already have descriptions.")
            return

        print(f"\nFetching descriptions for {len(jobs_to_update)} jobs...")

        success_count = 0
        failed_count = 0

        for i, job in enumerate(jobs_to_update):
        #     job_id = job.get('job_id', '')
        #     if not job_id:
        #         failed_count += 1
        #         continue

        #     # Rebuild the externalPath from job_id
        #     external_path = '/' + job_id
            link = job.get('link', '')
            # Extract the /job/... path from the browser link and build the CXS API URL
            path_match = re.search(r'(/job/.+)', link)
            if not path_match:
                failed_count += 1
                continue
            cxs_url = f'{self.WORKDAY_BASE}/wday/cxs/{self.COMPANY}/{self.TENANT}{path_match.group(1)}'
            detail = self._get_job_detail(cxs_url)
            print(f"\n[{i+1}/{len(jobs_to_update)}] {job['title']}")

            detail = self._get_job_detail(cxs_url)
            if not detail:
                print(f"  Failed to fetch detail.")
                failed_count += 1
                time.sleep(delay)
                continue

            try:
                info = detail.get('jobPostingInfo', {})
                description = info.get('jobDescription', '') or ''

                # Strip any residual HTML tags
                if '<' in description:
                    try:
                        from bs4 import BeautifulSoup
                        description = BeautifulSoup(description, 'html.parser').get_text(
                            separator='\n', strip=True
                        )
                    except ImportError:
                        pass

                if len(description) < 50:
                    print(f"  Description too short ({len(description)} chars), skipping.")
                    failed_count += 1
                else:
                    job['description'] = description
                    job['description_fetched'] = True
                    # Enrich additional fields if available
                    job['department'] = info.get('jobReqId', '') or job['department']
                    # Extract country from structured location data
                    offices = detail.get('jobPostingInfo', {}).get('offices', [])
                    if offices:
                        country = offices[0].get('country', {}).get('descriptor', '') or job['country']
                        job['country'] = country
                    success_count += 1
                    print(f"  Description: {len(description.split())} words")

            except Exception as e:
                print(f"  Error parsing detail: {e}")
                failed_count += 1

            if (i + 1) % 5 == 0:
                self._save_jobs()
                print(f"  Saved progress ({success_count}/{i+1} successful)")

            time.sleep(delay)

        self._save_jobs()

        print(f"\n{'='*60}")
        print(f"Description Fetching Complete!")
        print(f"  Successful : {success_count}")
        print(f"  Failed     : {failed_count}")
        print(f"{'='*60}")

    def run(self, fetch_descriptions=True):
        self.parse_job_listings()
        if fetch_descriptions:
            self.fetch_job_descriptions()


if __name__ == '__main__':
    print("Starting Heidrick & Struggles scraper...")
    print("=" * 60)

    scraper = HeidrickScraper()
    scraper.run(fetch_descriptions=True)

    print("\n" + "=" * 60)
    print(f"Done! Total jobs: {len(scraper.jobs)}")
    print("=" * 60)