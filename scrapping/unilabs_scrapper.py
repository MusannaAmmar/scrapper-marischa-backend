import os
import json
import re
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

class UnilabsScraper:
    """
    Scrapes Unilabs job listings via the Workable public JSON API.

    Source page : https://apply.workable.com/unilabs/
    Platform    : Workable (React SPA — API used directly, no HTML parsing needed)

    Endpoints:
      List   POST https://apply.workable.com/api/v3/accounts/unilabs/jobs
                  body: {"query":"","location":[],"department":[],"worktype":[],"remote":[]}
      Detail Workable does not expose a public JSON detail endpoint.
             Job pages are fully client-side rendered React. Descriptions are
             fetched by JS-rendering https://apply.workable.com/unilabs/j/{shortcode}/
             via ZenRows and parsing JSON-LD structured data or visible DOM.

    Job page URL: https://apply.workable.com/unilabs/j/{shortcode}/
    """

    COMPANY    = 'Unilabs'
    SUBDOMAIN  = 'unilabs'
    BASE_URL   = 'https://apply.workable.com'

    LIST_API   = 'https://apply.workable.com/api/v3/accounts/unilabs/jobs'
    DETAIL_API = 'https://apply.workable.com/api/v3/accounts/unilabs/jobs/{shortcode}'

    ZENROWS_API     = 'https://api.zenrows.com/v1/'
    ZENROWS_API_KEY = os.getenv('ZENROWS')

    # Removed: DETAIL_API — no public JSON detail endpoint exists on Workable

    def __init__(self, output_file='json_files/unilabs_jobs.json'):
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
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 Chrome/120.0 Safari/537.36'
            ),
        }

    def _fetch_list_page(self, token=None):
        """
        POST to Workable public jobs API.
        Workable's careers board uses POST with a JSON body, not GET with query params.
        """
        body = {
            'query': '',
            'location': [],
            'department': [],
            'worktype': [],
            'remote': [],
        }
        if token:
            body['token'] = token

        for attempt in range(4):
            wait = 2 ** attempt
            if attempt > 0:
                print(f"  Retry {attempt}/3 – waiting {wait}s...")
                time.sleep(wait)
            try:
                resp = requests.post(
                    self.LIST_API,
                    headers=self._api_headers(),
                    json=body,
                    timeout=30,
                )
                if resp.status_code == 200:
                    return resp.json()
                print(f"  [warn] HTTP {resp.status_code}: {resp.text[:200]}")
            except Exception as e:
                print(f"  [error] {e}")
        return None

    def _fetch_rendered_job_page(self, url):
        """
        Fetch the JS-rendered Workable job detail page via ZenRows.
        Waits until the job description section appears in the DOM.
        """
        params = {
            'url': url,
            'apikey': self.ZENROWS_API_KEY,
            'js_render': 'true',
            'premium_proxy': 'true',
            'wait': '6000',
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

    @staticmethod
    def _extract_description_from_html(html):
        """
        Parse a JS-rendered Workable job page.

        Strategy (in order):
        1. JSON-LD <script type="application/ld+json"> — Workable embeds JobPosting
           structured data with 'description' when smartSEODescription is on.
        2. Elements with data-ui attributes (Workable React data-ui="job-description").
        3. <article> or <section> containing a Description/Requirements heading.
        4. og:description meta tag.
        """
        soup = BeautifulSoup(html, 'html.parser')

        # 1 — JSON-LD structured data
        for script in soup.find_all('script', type='application/ld+json'):
            try:
                data = json.loads(script.string or '')
                if isinstance(data, list):
                    data = data[0]
                if data.get('@type') == 'JobPosting':
                    desc = data.get('description', '')
                    if desc:
                        # Already plain text or HTML — strip tags
                        desc = re.sub(r'<[^>]+>', ' ', desc)
                        desc = re.sub(r'\s+', ' ', desc).strip()
                        return desc
            except Exception:
                pass

        # 2 — data-ui attributes Workable uses in its React components
        for attr_val in ['job-description', 'jobDescription', 'description', 'content']:
            el = soup.find(attrs={'data-ui': attr_val})
            if el:
                return el.get_text(separator='\n', strip=True)

        # 3 — article / section containing visible heading
        for container in soup.find_all(['article', 'section', 'div']):
            heading = container.find(['h1', 'h2', 'h3'])
            if heading and re.search(r'description|requirement|about|role|position', heading.get_text(), re.I):
                return container.get_text(separator='\n', strip=True)

        # 4 — og:description meta
        og = soup.find('meta', property='og:description')
        if og:
            return og.get('content', '').strip()

        return ''

    # ------------------------------------------------------------------ #
    #  Listings parser                                                     #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse_location(job):
        """Build a readable location string from Workable location fields."""
        loc = job.get('location', {}) or {}
        parts = [
            loc.get('city', ''),
            loc.get('region', ''),
            loc.get('country', ''),
        ]
        return ', '.join(p for p in parts if p)

    def parse_job_listings(self):
        existing_ids = self._get_existing_job_ids()
        total_new = 0
        token = None

        print('\nFetching Unilabs jobs via Workable API...')

        while True:
            page_label = f'token={token[:12]}...' if token else 'first page'
            print(f'\n  Requesting {page_label}...')

            data = self._fetch_list_page(token=token)
            if not data:
                print('  Failed to fetch page — stopping.')
                break

            postings = data.get('results', [])
            if not postings:
                print('  No results returned — done.')
                break

            print(f'  Got {len(postings)} jobs in this page')

            for posting in postings:
                shortcode = posting.get('shortcode', '')
                if not shortcode:
                    continue

                job_id = shortcode  # Workable shortcodes are already unique IDs

                if job_id in existing_ids:
                    continue

                title      = posting.get('title', '')
                location   = self._parse_location(posting)
                country    = (posting.get('location') or {}).get('country', '')
                city       = (posting.get('location') or {}).get('city', '')
                department = posting.get('department', '')
                remote     = posting.get('remote', False)
                created_at = posting.get('created_at', '')
                employment = posting.get('employment_type', '')

                job_url = f'{self.BASE_URL}/{self.SUBDOMAIN}/j/{shortcode}/'

                job = {
                    'title':               title,
                    'job_id':              job_id,
                    'link':                job_url,
                    'shortcode':           shortcode,
                    'location':            location,
                    'city':                city,
                    'country':             country,
                    'job_type':            employment,
                    'remote':              'Yes' if remote else (
                        'Yes' if re.search(r'\bremote\b', location + ' ' + title, re.I) else ''
                    ),
                    'posted_date':         created_at,
                    'salary':              '',
                    'company':             self.COMPANY,
                    'category':            department,
                    'department':          department,
                    'description':         '',
                    'description_fetched': False,
                    'skills':              [],
                    'status':              'active',
                    'source':              'Unilabs',
                    'date':                datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                }

                self.jobs.append(job)
                existing_ids.add(job_id)
                total_new += 1
                print(f'  + {title[:70]} | {location[:50]}')

            # Cursor-based pagination — Workable returns 'nextPage' token when more pages exist
            token = data.get('nextPage') or data.get('next_page_token')
            if not token:
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

    def fetch_job_descriptions(self, delay=1):
        jobs_to_update = [j for j in self.jobs if not j.get('description_fetched', False)]
        if not jobs_to_update:
            print('\nAll jobs already have descriptions.')
            return

        print(f'\nFetching descriptions for {len(jobs_to_update)} jobs...')
        success_count = 0
        failed_count  = 0

        for i, job in enumerate(jobs_to_update):
            shortcode = job.get('shortcode', '')
            print(f"\n  [{i + 1}/{len(jobs_to_update)}] {job.get('title', '')[:60]}")

            description = ''

            if shortcode:
                job_url = job.get('link', '') or f'{self.BASE_URL}/{self.SUBDOMAIN}/j/{shortcode}/'
                html = self._fetch_rendered_job_page(job_url)
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
    scraper = UnilabsScraper(output_file='json_files/unilabs_jobs.json')
    scraper.run()
