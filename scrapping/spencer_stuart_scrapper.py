import os
import json
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
import re  # add at top of file if not already there
from dotenv import load_dotenv

load_dotenv()


class SpencerStuartScraper:
    """
    Scrapes Spencer Stuart job listings via the Workday JSON API (listings)
    and ZenRows (job detail pages, which require JS rendering).
    """

    WORKDAY_BASE = 'https://spencerstuart.wd5.myworkdayjobs.com'
    TENANT = 'spencerstuart'
    SITE_ID = 'Spencer_Stuart_External_Careers'

    JOBS_API = f'{WORKDAY_BASE}/wday/cxs/{TENANT}/{SITE_ID}/jobs'
    ZENROWS_API = 'https://api.zenrows.com/v1/'
    ZENROWS_API_KEY = os.getenv('ZENROWS')
    LOCATION_IDS = [
        'be264fa6cb8b013095e9da6a18ba8126',
        'be264fa6cb8b012b878b88f44fba222c',
        'be264fa6cb8b0189b4f58df44fba2a2c',
        'be264fa6cb8b01a404b7eff44fbaba2c',
    ]

    def __init__(self, output_file='json_files/spencer_stuart_jobs.json'):
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
    #  Workday API helpers (listings)                                      #
    # ------------------------------------------------------------------ #

    def _post_jobs(self, offset=0, limit=20):
        headers = {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
        }
        payload = {
            'appliedFacets': {'locations': self.LOCATION_IDS},
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

    # ------------------------------------------------------------------ #
    #  ZenRows helper (job detail pages)                                   #
    # ------------------------------------------------------------------ #

    def _fetch_detail_via_zenrows(self, url):
        """
        Fetch a job detail page via ZenRows and parse structured data.
        Returns a dict with description, city, country, datePosted,
        employmentType, jobReqId — or None on failure.
        """
        try:
            resp = requests.get(
                self.ZENROWS_API,
                params={'url': url, 
                        'apikey': self.ZENROWS_API_KEY,
                        'js_render': 'true',
                        'premium_proxy': 'true',},
                timeout=60,
            )
            if resp.status_code != 200:
                print(f"  [warn] ZenRows returned {resp.status_code}: {resp.text[:200]}")
                return None
        except Exception as e:
            print(f"  [error] ZenRows request: {e}")
            return None

        soup = BeautifulSoup(resp.text, 'html.parser')

        # --- Primary source: JSON-LD structured data ---
        ld_tag = soup.find('script', type='application/ld+json')
        if ld_tag:
            try:
                data = json.loads(ld_tag.string)
                description = data.get('description', '') or ''

                address = (data.get('jobLocation') or {}).get('address') or {}
                city = address.get('addressLocality', '')
                country = address.get('addressCountry', '')

                identifier = data.get('identifier') or {}
                job_req_id = str(identifier.get('value', ''))

                date_posted = data.get('datePosted', '')
                employment_type = data.get('employmentType', '')

                if len(description) >= 50:
                    return {
                        'description': description,
                        'city': city,
                        'country': country,
                        'job_req_id': job_req_id,
                        'date_posted': date_posted,
                        'employment_type': employment_type,
                    }
            except (json.JSONDecodeError, AttributeError) as e:
                print(f"  [warn] JSON-LD parse error: {e}")

        # --- Fallback: og:description meta tag ---
        og_desc = soup.find('meta', property='og:description')
        if og_desc:
            description = og_desc.get('content', '').strip()
            if len(description) >= 50:
                print(f"  [info] Used og:description fallback")
                return {
                    'description': description,
                    'city': '',
                    'country': '',
                    'job_req_id': '',
                    'date_posted': '',
                    'employment_type': '',
                }

        print(f"  [warn] No usable description found in page")
        return None

    # ------------------------------------------------------------------ #
    #  Scraping                                                            #
    # ------------------------------------------------------------------ #

    def parse_job_listings(self):
        """Page through the Workday API and collect all job listings."""
        print("Fetching Spencer Stuart job listings...")

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

            for posting in postings:
                try:

                    external_path = posting.get('externalPath', '')
                    req_match = re.search(r'_(R\d+)', external_path)
                    job_id = req_match.group(1) if req_match else external_path.lstrip('/')

                    if not job_id:
                        continue

                    if job_id in existing_ids:
                        total_skipped += 1
                        continue

                    title = posting.get('title', '').strip()
                    location = posting.get('locationsText', '').strip()
                    posted_date = posting.get('postedOn', '').strip()
                    bullet_fields = posting.get('bulletFields', [])
                    job_type = ', '.join(str(b) for b in bullet_fields) if bullet_fields else ''
                    full_link = f'{self.WORKDAY_BASE}/en-US/{self.SITE_ID}{external_path}'

                    job = {
                        'title': title,
                        'job_id': job_id,
                        'job_seq_no': job_id,
                        'link': full_link,
                        'location': location,
                        'city': location,
                        'country': '',
                        'job_type': job_type,
                        'posted_date': posted_date,
                        'salary': '',
                        'company': 'Spencer Stuart',
                        'category': '',
                        'department': '',
                        'description': '',
                        'description_fetched': False,
                        'skills': [],
                        'status': 'active',
                        'source': 'spencer-stuart',
                        'date': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                    }

                    self.jobs.append(job)
                    existing_ids.add(job_id)
                    total_new += 1
                    print(f"  + {title} | {location}")

                except Exception as e:
                    print(f"  Error parsing posting: {e}")
                    continue

            self._save_jobs()
            print(f"  New: {total_new}, Skipped (duplicates): {total_skipped}")

            offset += limit
            if offset >= total_count:
                break

            time.sleep(1)

        print(f"\n{'='*60}")
        print(f"  New jobs found    : {total_new}")
        print(f"  Duplicates skipped: {total_skipped}")
        print(f"  Total jobs stored : {len(self.jobs)}")
        print(f"{'='*60}")

    def fetch_job_descriptions(self, delay=2):
        """Fetch full description for jobs that don't have one yet via ZenRows."""
        jobs_to_update = [j for j in self.jobs if not j.get('description_fetched', False)]

        if not jobs_to_update:
            print("\nAll jobs already have descriptions.")
            return

        print(f"\nFetching descriptions for {len(jobs_to_update)} jobs via ZenRows...")

        success_count = 0
        failed_count = 0

        for i, job in enumerate(jobs_to_update):
            url = job.get('link', '')
            if not url:
                failed_count += 1
                continue

            print(f"\n[{i+1}/{len(jobs_to_update)}] {job['title']}")

            detail = self._fetch_detail_via_zenrows(url)
            if not detail:
                print(f"  Failed to fetch detail.")
                failed_count += 1
                time.sleep(delay)
                continue

            job['description'] = detail['description']
            job['description_fetched'] = True

            if detail['city']:
                job['city'] = detail['city']
            if detail['country']:
                job['country'] = detail['country']
            if detail['job_req_id']:
                job['department'] = detail['job_req_id']
            if detail['date_posted']:
                job['posted_date'] = detail['date_posted']
            if detail['employment_type']:
                job['job_type'] = detail['employment_type']

            success_count += 1
            print(f"  Description: {len(detail['description'].split())} words | {detail['city']}, {detail['country']}")

            if (i + 1) % 5 == 0:
                self._save_jobs()
                print(f"  Progress saved ({i+1}/{len(jobs_to_update)})")

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
    print("Starting Spencer Stuart scraper...")
    print("=" * 60)

    scraper = SpencerStuartScraper()
    scraper.run(fetch_descriptions=True)

    print("\n" + "=" * 60)
    print(f"Done! Total jobs: {len(scraper.jobs)}")
    print("=" * 60)