import os
import json
import re
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

class AbottScraper:
    """
    Scrapes Exact Sciences job listings from the Workday JSON API with
    country filters applied.

    Source URL:
        https://exactsciences.wd1.myworkdayjobs.com/Exact_Sciences/jobs
        ?locationCountry=6a800a4736884df5826858d435650f45
        &locationCountry=187134fccb084a0ea9b4b95f23890dbe
        &locationCountry=8cd04a563fd94da7b06857a79faaf815
        &locationCountry=29247e57dbaf46fb855b224e03170bc7
        &locationCountry=dcc5b7608d8644b3a93716604e78e995

    Workday sites expose a REST JSON API at:
        POST /wday/cxs/{tenant}/{siteId}/jobs
    Individual job detail pages are JS-rendered (React SPA); descriptions
    are fetched via ZenRows and parsed from data-automation-id="job-posting-details".
    """

    COMPANY  = 'Exact Sciences'
    TENANT   = 'exactsciences'
    SITE_ID  = 'Exact_Sciences'
    BASE_URL = 'https://exactsciences.wd1.myworkdayjobs.com'

    API_URL  = 'https://exactsciences.wd1.myworkdayjobs.com/wday/cxs/exactsciences/Exact_Sciences/jobs'

    # Country filter IDs extracted from the source URL
    COUNTRY_FILTERS = [
        '6a800a4736884df5826858d435650f45',
        '187134fccb084a0ea9b4b95f23890dbe',
        '8cd04a563fd94da7b06857a79faaf815',
        '29247e57dbaf46fb855b224e03170bc7',
        'dcc5b7608d8644b3a93716604e78e995',
    ]

    PAGE_SIZE = 20

    ZENROWS_API     = 'https://api.zenrows.com/v1/'
    ZENROWS_API_KEY = os.getenv('ZENROWS')

    def __init__(self, output_file='json_files/abott_jobs.json'):
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
    #  API helpers                                                         #
    # ------------------------------------------------------------------ #

    def _api_headers(self):
        return {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 Chrome/120.0 Safari/537.36'
            ),
        }

    def _fetch_jobs_page(self, offset=0):
        """POST to Workday jobs API and return parsed JSON, or None on failure."""
        payload = {
            'appliedFacets': {
                'locationCountry': self.COUNTRY_FILTERS,
            },
            'limit': self.PAGE_SIZE,
            'offset': offset,
            'searchText': '',
        }

        for attempt in range(4):
            wait = 2 ** attempt
            if attempt > 0:
                print(f"  Retry {attempt}/3 – waiting {wait}s...")
                time.sleep(wait)
            try:
                resp = requests.post(
                    self.API_URL,
                    headers=self._api_headers(),
                    json=payload,
                    timeout=30,
                )
                if resp.status_code == 200:
                    return resp.json()
                print(f"  [warn] HTTP {resp.status_code}: {resp.text[:200]}")
            except Exception as e:
                print(f"  [error] {e}")
        return None

    def _fetch_rendered_html(self, url):
        """Fetch a JS-rendered page via ZenRows (required for Workday SPA)."""
        params = {
            'url': url,
            'apikey': self.ZENROWS_API_KEY,
            'js_render': 'true',
            'premium_proxy': 'true',
        }
        for attempt in range(4):
            wait = 2 ** attempt
            if attempt > 0:
                print(f"  Retry {attempt}/3 (ZenRows) – waiting {wait}s...")
                time.sleep(wait)
            try:
                resp = requests.get(self.ZENROWS_API, params=params, timeout=90)
                if resp.status_code == 200:
                    return resp.text
                print(f"  [warn] ZenRows {resp.status_code}: {resp.text[:200]}")
            except Exception as e:
                print(f"  [error] ZenRows: {e}")
        return None

    def _extract_description_from_html(self, html):
        """Parse the Workday job detail page and extract the description div."""
        soup = BeautifulSoup(html, 'html.parser')

        # Primary target: data-automation-id="job-posting-details"
        container = soup.find(attrs={'data-automation-id': 'job-posting-details'})
        if container:
            return container.get_text(separator='\n', strip=True)

        # Fallback: rich-text-container holds the description body
        container = soup.find(attrs={'data-automation-id': 'richTextContainer'})
        if container:
            return container.get_text(separator='\n', strip=True)

        # Last-resort: og:description meta tag
        og = soup.find('meta', property='og:description')
        if og:
            return og.get('content', '').strip()

        return ''

    # ------------------------------------------------------------------ #
    #  Listings parser                                                     #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _extract_job_id(external_path):
        """
        Derive a stable unique ID from the Workday externalPath.
        Workday paths end with a requisition code like '_R26-12872'.
        Pattern: letter(s) + digits + optional (hyphen + alphanumeric)+ groups.
        Falls back to the full normalised path.
        """
        m = re.search(r'_([A-Za-z]+\d+(?:-[A-Za-z0-9]+)*)$', external_path.rstrip('/'))
        if m:
            return m.group(1)
        return external_path.strip('/').replace('/', '_')

    def parse_job_listings(self):
        existing_ids = self._get_existing_job_ids()
        total_new = 0
        offset = 0

        print('\nFetching Exact Sciences jobs (country-filtered)...')

        while True:
            print(f'\n  Requesting page (offset={offset})...')
            data = self._fetch_jobs_page(offset=offset)

            if not data:
                print('  Failed to fetch page — stopping.')
                break

            job_postings = data.get('jobPostings', [])
            total        = data.get('total', 0)

            if offset == 0:
                print(f'  Total jobs available: {total}')

            if not job_postings:
                break

            for posting in job_postings:
                external_path = posting.get('externalPath', '')
                if not external_path:
                    continue

                job_id = self._extract_job_id(external_path)

                if job_id in existing_ids:
                    continue

                title     = posting.get('title', '')
                locations = posting.get('locationsText', '')
                if isinstance(locations, list):
                    locations = ', '.join(locations)

                posted_on = posting.get('postedOn', '')

                # Build the human-readable job URL
                full_url = f'{self.BASE_URL}/en-US/{self.SITE_ID}{external_path}'

                job = {
                    'title':               title,
                    'job_id':              job_id,
                    'link':                full_url,
                    'external_path':       external_path,
                    'location':            locations,
                    'city':                locations,
                    'country':             '',
                    'job_type':            '',
                    'remote':              'Yes' if re.search(r'\bremote\b', locations, re.I) else '',
                    'posted_date':         posted_on,
                    'salary':              '',
                    'company':             self.COMPANY,
                    'category':            '',
                    'department':          '',
                    'description':         '',
                    'description_fetched': False,
                    'skills':              [],
                    'status':              'active',
                    'source':              'Exact Sciences',
                    'date':                datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                }

                self.jobs.append(job)
                existing_ids.add(job_id)
                total_new += 1
                print(f'  + {title[:70]} | {locations[:50]}')

            offset += len(job_postings)
            if offset >= total:
                break

            time.sleep(1)

        self._save_jobs()
        print(f"\n{'='*60}")
        print(f'  New jobs found    : {total_new}')
        print(f'  Total jobs stored : {len(self.jobs)}')
        print(f"{'='*60}")

    # ------------------------------------------------------------------ #
    #  Description fetching                                                #
    # ------------------------------------------------------------------ #

    def fetch_job_descriptions(self, delay=2):
        jobs_to_update = [j for j in self.jobs if not j.get('description_fetched', False)]
        if not jobs_to_update:
            print('\nAll jobs already have descriptions.')
            return

        print(f'\nFetching descriptions for {len(jobs_to_update)} jobs...')
        success_count = 0
        failed_count  = 0

        for i, job in enumerate(jobs_to_update):
            print(f"\n  [{i + 1}/{len(jobs_to_update)}] {job.get('title', '')[:60]}")

            description = ''

            # Workday is a React SPA — fetch the rendered HTML page via ZenRows
            job_url = job.get('link', '')
            if job_url:
                html = self._fetch_rendered_html(job_url)
                if html:
                    description = self._extract_description_from_html(html)
                    description = re.sub(r'\n{3,}', '\n\n', description).strip()

            job['description']         = description
            job['description_fetched'] = True

            if description:
                print(f'    Description: {len(description.split())} words')
                success_count += 1
            else:
                print('    [warn] No description found')
                failed_count += 1

            self._save_jobs()
            time.sleep(delay)

        print(f"\n{'='*60}")
        print(f'  Description Success : {success_count}')
        print(f'  Description Failed  : {failed_count}')
        print(f"{'='*60}")

    # ------------------------------------------------------------------ #
    #  Entry point                                                         #
    # ------------------------------------------------------------------ #

    def run(self, fetch_descriptions=True):
        self.parse_job_listings()
        if fetch_descriptions:
            self.fetch_job_descriptions()


if __name__ == '__main__':
    scraper = AbottScraper(output_file='json_files/abott_jobs.json')
    scraper.run()
