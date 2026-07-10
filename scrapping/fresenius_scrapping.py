import os
import json
import re
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

class FreseniusScraper:
    """
    Scrapes Fresenius Group job listings from karriere.fresenius.de.
    The site is Next.js SSR — job data lives in __NEXT_DATA__ JSON.
    Pagination is driven by incrementing `offset` in the hex-encoded
    `encodedParameters` URL query param.
    """

    BASE_URL = 'https://karriere.fresenius.de'
    LISTINGS_URL = f'{BASE_URL}/en-US/job-search'

    ZENROWS_API = 'https://api.zenrows.com/v1/'
    ZENROWS_API_KEY = os.getenv('ZENROWS')

    # Filter term UUIDs extracted from the source URL's encodedParameters.
    # These correspond to the 5 selected subsidiaries / job categories.
    FILTER_TERM_IDS = [
        'c7b0174e-d67a-5ff0-8f09-5d51c766b74e',
        '7e30c9f5-d5b4-5a01-89d8-2ec8d3e092bf',
        '8860f74d-48ee-5193-af6e-dea4af7f08ce',
        '3bde8a8c-e15a-5f6e-b176-8f7d3d5e3f0e',
        '2473d6ef-02fb-51c2-830f-088ad9aede91',
    ]

    COMPANY_SEGMENT_MAP = {
        'FRESENIUS_HEALTH_SERVICES': 'Fresenius Health Services',
        'FRESENIUS_HELIOS_HOSPITALS': 'Fresenius Helios',
        'FRESENIUS_KABI': 'Fresenius Kabi',
        'FRESENIUS_MEDICAL_CARE': 'Fresenius Medical Care',
        'FRESENIUS_VAMED': 'Fresenius Vamed',
        'FRESENIUS_SE': 'Fresenius SE',
    }

    PAGE_LIMIT = 20

    def __init__(self, output_file='json_files/fresenius_jobs.json'):
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
    #  URL / parameter helpers                                             #
    # ------------------------------------------------------------------ #

    def _build_encoded_params(self, offset=0, limit=None):
        """Return hex-encoded JSON parameters for the encodedParameters URL param."""
        params = {
            'language': 'en-US',
            'limit': limit or self.PAGE_LIMIT,
            'offset': offset,
            'include_term_counts': True,
            'term_ids': self.FILTER_TERM_IDS,
        }
        return json.dumps(params, separators=(',', ':')).encode('utf-8').hex()

    def _build_listings_url(self, offset=0):
        return f"{self.LISTINGS_URL}?encodedParameters={self._build_encoded_params(offset=offset)}"

    # ------------------------------------------------------------------ #
    #  Fetch helpers                                                       #
    # ------------------------------------------------------------------ #

    def _fetch_direct(self, url):
        """Direct GET with browser-like headers. Returns HTML or None."""
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        }
        for attempt in range(4):
            wait = 2 ** attempt
            if attempt > 0:
                print(f"  Retry {attempt}/3 – waiting {wait}s...")
                time.sleep(wait)
            try:
                resp = requests.get(url, headers=headers, timeout=30)
                if resp.status_code == 200:
                    return resp.text
                print(f"  [warn] HTTP {resp.status_code}")
            except Exception as e:
                print(f"  [error] {e}")
        return None

    def _fetch_via_zenrows(self, url):
        """ZenRows fallback. Returns HTML or None."""
        params = {'url': url, 'apikey': self.ZENROWS_API_KEY}
        for attempt in range(4):
            wait = 2 ** attempt
            if attempt > 0:
                print(f"  Retry {attempt}/3 (ZenRows) – waiting {wait}s...")
                time.sleep(wait)
            try:
                resp = requests.get(self.ZENROWS_API, params=params, timeout=60)
                if resp.status_code == 200:
                    return resp.text
                print(f"  [warn] ZenRows {resp.status_code}: {resp.text[:200]}")
            except Exception as e:
                print(f"  [error] ZenRows: {e}")
        return None

    def _fetch(self, url):
        """Try direct fetch; fall back to ZenRows."""
        html = self._fetch_direct(url)
        if not html:
            print("  Direct fetch failed — trying ZenRows...")
            html = self._fetch_via_zenrows(url)
        return html

    # ------------------------------------------------------------------ #
    #  NEXT_DATA extraction                                                #
    # ------------------------------------------------------------------ #

    def _get_next_data(self, html):
        """Extract and parse the __NEXT_DATA__ JSON blob from a Next.js page."""
        soup = BeautifulSoup(html, 'html.parser')
        tag = soup.find('script', id='__NEXT_DATA__', type='application/json')
        if not tag:
            return None
        try:
            return json.loads(tag.string)
        except (json.JSONDecodeError, AttributeError):
            return None

    def _find_key(self, data, key):
        """Depth-first search for a key anywhere in nested dicts/lists."""
        if isinstance(data, dict):
            if key in data:
                return data[key]
            for v in data.values():
                result = self._find_key(v, key)
                if result is not None:
                    return result
        elif isinstance(data, list):
            for item in data:
                result = self._find_key(item, key)
                if result is not None:
                    return result
        return None

    # ------------------------------------------------------------------ #
    #  Scraping                                                            #
    # ------------------------------------------------------------------ #

    def parse_job_listings(self):
        """Fetch all Fresenius job listings via paginated encodedParameters URLs."""
        print("Fetching Fresenius job listings...")
        existing_ids = self._get_existing_job_ids()
        new_count = 0
        offset = 0
        total = None

        while True:
            url = self._build_listings_url(offset=offset)
            print(f"\n  Fetching offset={offset} (total={total if total is not None else '?'})...")

            html = self._fetch(url)
            if not html:
                print("  Failed to fetch page — stopping.")
                break

            next_data = self._get_next_data(html)
            if not next_data:
                print("  Could not parse __NEXT_DATA__ — falling back to HTML parse.")
                page_jobs = self._parse_html_fallback(html, existing_ids)
                self.jobs.extend(page_jobs)
                new_count += len(page_jobs)
                break

            initial_results = self._find_key(next_data, 'initialResults')
            if not initial_results:
                print("  No initialResults in __NEXT_DATA__.")
                break

            if total is None:
                total = initial_results.get('count', 0)
                print(f"  Total matching jobs: {total}")

            job_ads = initial_results.get('jobAds', [])
            if not job_ads:
                print("  No job ads returned — done.")
                break

            for ad in job_ads:
                job_id = ad.get('id', '')
                if not job_id or job_id in existing_ids:
                    continue

                url_path = ad.get('url', '')
                # url_path is like /job-detail/{uuid}/{slug}
                full_url = f"{self.BASE_URL}/en-US{url_path}"

                location = ad.get('location', '')
                segment = ad.get('businessSegment', '')
                company = self.COMPANY_SEGMENT_MAP.get(segment, 'Fresenius Group')

                job = {
                    'title': ad.get('title', ''),
                    'job_id': job_id,
                    'link': full_url,
                    'location': location,
                    'city': location,
                    'country': '',
                    'job_type': '',
                    'posted_date': '',
                    'salary': '',
                    'company': company,
                    'category': '',
                    'department': '',
                    'description': '',
                    'description_fetched': False,
                    'skills': [],
                    'status': 'active',
                    'source': 'fresenius',
                    'date': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                }
                self.jobs.append(job)
                existing_ids.add(job_id)
                new_count += 1
                print(f"  + {job['title'][:70]} | {location} | {company}")

            offset += self.PAGE_LIMIT
            if total is not None and offset >= total:
                break

            time.sleep(2)

        self._save_jobs()
        print(f"\n{'='*60}")
        print(f"  New jobs found    : {new_count}")
        print(f"  Total jobs stored : {len(self.jobs)}")
        print(f"{'='*60}")

    def _parse_html_fallback(self, html, existing_ids):
        """Parse job listings directly from rendered HTML (used when NEXT_DATA unavailable)."""
        jobs = []
        soup = BeautifulSoup(html, 'html.parser')

        for a in soup.find_all('a', class_='job-result_job-name'):
            try:
                href = a.get('href', '')
                title = a.get('title', '') or a.get_text(strip=True)

                uuid_match = re.search(r'/job-detail/([0-9a-f-]{36})/', href)
                if not uuid_match:
                    continue
                job_id = uuid_match.group(1)
                if job_id in existing_ids:
                    continue

                full_url = f"{self.BASE_URL}{href}" if href.startswith('/') else href

                # Location span: sibling column with data-id="undefined-span"
                container = a.find_parent(class_=re.compile(r'JobSearchResult__'))
                location = ''
                if container:
                    loc_span = container.find('span', attrs={'data-id': 'undefined-span'})
                    if loc_span:
                        location = loc_span.get_text(strip=True)

                job = {
                    'title': title,
                    'job_id': job_id,
                    'link': full_url,
                    'location': location,
                    'city': location,
                    'country': '',
                    'job_type': '',
                    'posted_date': '',
                    'salary': '',
                    'company': 'Fresenius Group',
                    'category': '',
                    'department': '',
                    'description': '',
                    'description_fetched': False,
                    'skills': [],
                    'status': 'active',
                    'source': 'fresenius',
                    'date': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                }
                jobs.append(job)
                existing_ids.add(job_id)
                print(f"  + {title[:70]} | {location}")
            except Exception as e:
                print(f"  Error parsing job card: {e}")

        return jobs

    # ------------------------------------------------------------------ #
    #  Description fetching                                                #
    # ------------------------------------------------------------------ #

    def fetch_job_descriptions(self, delay=2):
        """Fetch full descriptions for jobs that don't have one yet."""
        jobs_to_update = [j for j in self.jobs if not j.get('description_fetched', False)]
        if not jobs_to_update:
            print("\nAll jobs already have descriptions.")
            return

        print(f"\nFetching descriptions for {len(jobs_to_update)} jobs...")
        success_count = 0
        failed_count = 0

        for i, job in enumerate(jobs_to_update):
            url = job.get('link', '')
            if not url:
                failed_count += 1
                continue

            print(f"\n  [{i + 1}/{len(jobs_to_update)}] {job.get('title', '')[:60]}")
            print(f"    URL: {url}")

            html = self._fetch(url)
            if not html:
                print("    Failed to fetch detail page.")
                failed_count += 1
                continue

            soup = BeautifulSoup(html, 'html.parser')
            description = ''
            date_posted = ''
            country = ''

            # Primary: __NEXT_DATA__ job detail props
            next_data = self._get_next_data(html)
            if next_data:
                job_detail = self._find_key(next_data, 'job') or self._find_key(next_data, 'jobAd') or {}
                if isinstance(job_detail, dict):
                    description = (
                        job_detail.get('description', '')
                        or job_detail.get('bodyText', '')
                        or job_detail.get('content', '')
                        or ''
                    )
                    date_posted = (
                        job_detail.get('publishDate', '')
                        or job_detail.get('datePosted', '')
                        or ''
                    )
                    loc = job_detail.get('location', {})
                    if isinstance(loc, dict):
                        country = loc.get('country', '')

            # Fallback: JSON-LD structured data
            if len(description) < 50:
                ld_tag = soup.find('script', type='application/ld+json')
                if ld_tag:
                    try:
                        ld = json.loads(ld_tag.string)
                        description = ld.get('description', '') or description
                        date_posted = ld.get('datePosted', '') or date_posted
                        address = (ld.get('jobLocation') or {}).get('address', {})
                        country = address.get('addressCountry', '') or country
                    except (json.JSONDecodeError, AttributeError):
                        pass

            # Fallback: og:description meta
            if len(description) < 50:
                og = soup.find('meta', property='og:description')
                if og:
                    description = og.get('content', '').strip()

            job['description'] = description
            job['description_fetched'] = True
            if date_posted and not job.get('posted_date'):
                job['posted_date'] = date_posted
            if country and not job.get('country'):
                job['country'] = country

            if description:
                print(f"    Description: {len(description)} chars")
                success_count += 1
            else:
                print(f"    [warn] No description found")
                failed_count += 1

            self._save_jobs()
            time.sleep(delay)

        print(f"\n{'='*60}")
        print(f"  Description Success : {success_count}")
        print(f"  Description Failed  : {failed_count}")
        print(f"{'='*60}")
        print(f"Description Fetching Complete!")

    def run(self, fetch_descriptions=True):
        self.parse_job_listings()
        if fetch_descriptions:
            self.fetch_job_descriptions()


if __name__ == '__main__':
    scraper = FreseniusScraper(output_file='json_files/fresenius_jobs.json')
    scraper.run()