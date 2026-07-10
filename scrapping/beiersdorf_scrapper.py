import os
import json
import re
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()




class BeiersdorfScraper:
    """
    Scrapes Beiersdorf job listings from their Sitecore-based career site.
    Uses ZenRows for all page fetching to bypass the GDPR consent wall.
    Pagination uses the internal JobResultAjax endpoint with a ZenRows fallback.
    """

    BASE_URL = 'https://www.beiersdorf.com'
    LISTINGS_URL = f'{BASE_URL}/career/your-application/job-search'
    AJAX_URL = f'{BASE_URL}/ajax/Jobboard/JobResultAjax'
    AJAX_CONTEXT_ID = '{213FB95D-4545-426C-9F6A-7CD5753A00EA}'
    FILTER_COUNTRIES = 'Czech Republic,France,Germany,Netherlands,Switzerland'
    FILTER_LEVELS = 'Manager,Professional'

    ZENROWS_API = 'https://api.zenrows.com/v1/'
    ZENROWS_API_KEY = os.getenv('ZENROWS')

    def __init__(self, output_file='json_files/beiersdorf_jobs.json'):
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
    #  ZenRows helper                                                      #
    # ------------------------------------------------------------------ #

    def _fetch_via_zenrows(self, url):
        """Fetch a URL via ZenRows with retry + backoff. Returns HTML or None."""
        params = {
            'url': url,
            'apikey': self.ZENROWS_API_KEY,
        }
        for attempt in range(4):
            wait = 2 ** attempt
            if attempt > 0:
                print(f"  Retry {attempt}/3 – waiting {wait}s...")
                time.sleep(wait)
            try:
                resp = requests.get(self.ZENROWS_API, params=params, timeout=60)
                if resp.status_code == 200:
                    return resp.text
                print(f"  [warn] ZenRows {resp.status_code}: {resp.text[:200]}")
            except Exception as e:
                print(f"  [error] ZenRows: {e}")
        return None

    # ------------------------------------------------------------------ #
    #  AJAX pagination helper                                              #
    # ------------------------------------------------------------------ #

    def _fetch_ajax_page(self, page, filter_state, count=100, sort='date'):
        """
        POST to Beiersdorf's JobResultAjax endpoint for pages 2+.
        Returns a BeautifulSoup of the response HTML, or None on failure.
        """
        params = {
            'db': 'web',
            'contextItemId': self.AJAX_CONTEXT_ID,
            'lang': 'en',
        }
        form_data = {
            'page': str(page),
            'count': str(count),
            'sort': sort,
            'level': self.FILTER_LEVELS,
            'country': self.FILTER_COUNTRIES,

        }
        if filter_state:
            form_data['filters'] = filter_state

        for attempt in range(4):
            wait = 2 ** attempt
            if attempt > 0:
                print(f"  Retry {attempt}/3 – waiting {wait}s...")
                time.sleep(wait)
            try:
                resp = requests.post(
                    self.AJAX_URL,
                    params=params,
                    data=form_data,
                    headers={
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36',
                        'Accept': 'text/html,application/xhtml+xml,*/*;q=0.9',
                        'Referer': self.LISTINGS_URL,
                        'X-Requested-With': 'XMLHttpRequest',
                    },
                    timeout=30,
                )
                if resp.status_code == 200 and resp.text.strip():
                    return BeautifulSoup(resp.text, 'html.parser')
                print(f"  [warn] AJAX page {page} returned {resp.status_code}")
            except Exception as e:
                print(f"  [error] AJAX request: {e}")
        return None

    # ------------------------------------------------------------------ #
    #  Job card parser                                                     #
    # ------------------------------------------------------------------ #

    def _parse_jobs_from_soup(self, soup, existing_ids):
        """Extract job dicts from a BeautifulSoup of a listings page or AJAX fragment."""
        new_jobs = []
        ul = soup.find('ul', class_='cw-jobs')
        if not ul:
            return new_jobs

        for li in ul.find_all('li', class_='cw-job'):
            try:
                a = li.find('a', href=True)
                if not a:
                    continue

                href = a['href']
                if href.startswith('/'):
                    href = self.BASE_URL + href

                job_id = href.rstrip('/').split('/')[-1]
                if job_id in existing_ids:
                    continue

                title_div = a.find('div', class_='cw-job-title')
                title = title_div.get_text(strip=True) if title_div else ''

                # Collect non-separator spans in document order:
                # [0]=category, [1]=level, [2]=location, [3]=work_mode (optional)
                meta_spans = [
                    s.get_text(strip=True)
                    for s in a.find_all('span', recursive=False)
                    if 'cw-separator' not in (s.get('class') or [])
                ]

                category = meta_spans[0] if len(meta_spans) > 0 else ''
                level = meta_spans[1] if len(meta_spans) > 1 else ''
                location_raw = meta_spans[2] if len(meta_spans) > 2 else ''
                work_mode = meta_spans[3] if len(meta_spans) > 3 else ''

                if ',' in location_raw:
                    parts = [p.strip() for p in location_raw.split(',')]
                    city = parts[0]
                    country = parts[-1]
                else:
                    city = location_raw
                    country = ''

                job = {
                    'title': title,
                    'job_id': job_id,
                    'link': href,
                    'location': location_raw,
                    'city': city,
                    'country': country,
                    'job_type': work_mode,
                    'posted_date': '',
                    'salary': '',
                    'company': 'Beiersdorf',
                    'category': category,
                    'level': level,
                    'department': '',
                    'description': '',
                    'description_fetched': False,
                    'skills': [],
                    'status': 'active',
                    'source': 'beiersdorf',
                    'date': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                }
                new_jobs.append(job)
                existing_ids.add(job_id)
                print(f"  + {title[:70]} | {location_raw}")

            except Exception as e:
                print(f"  Error parsing job card: {e}")

        return new_jobs

    # ------------------------------------------------------------------ #
    #  Scraping                                                            #
    # ------------------------------------------------------------------ #

    def parse_job_listings(self):
        """Fetch all pages of Beiersdorf job listings."""
        print("Fetching Beiersdorf job listings...")
        existing_ids = self._get_existing_job_ids()
        new_count = 0

        # Build filtered URL
        filtered_url = (
            f"{self.LISTINGS_URL}"
            f"?level={requests.utils.quote(self.FILTER_LEVELS)}"
            f"&country={requests.utils.quote(self.FILTER_COUNTRIES)}"
            f"&count=100&sort=date"
        )
        # Page 1: full page via ZenRows (bypasses GDPR consent wall)
        print("\n  Fetching page 1 via ZenRows...")
        html = self._fetch_via_zenrows(filtered_url)
        if not html:
            print("  Failed to fetch listings page.")
            return

        soup = BeautifulSoup(html, 'html.parser')

        # Read filter state encoded in data-atob (passed to AJAX calls)
        tag_filter = soup.find('div', class_='cw-tag-filter')
        filter_state = tag_filter.get('data-atob', '') if tag_filter else ''

        # Total result count and page count from the rendered HTML
        result_div = soup.find('div', class_='cw-job-list', id='result')
        result_count = int(result_div.get('data-result-count', 0)) if result_div else 0

        pagination = soup.find('div', class_='cw-pagination')
        page_items = pagination.find_all('span', class_='cw-pagination-item') if pagination else []
        total_pages = max(
            (int(s.get('data-page', 1)) for s in page_items),
            default=1,
        )
        print(f"  Total results: {result_count} | Pages: {total_pages}")

        jobs_page1 = self._parse_jobs_from_soup(soup, existing_ids)
        self.jobs.extend(jobs_page1)
        new_count += len(jobs_page1)

        # Pages 2+: AJAX endpoint, with ZenRows fallback
        for page in range(2, total_pages + 1):
            print(f"\n  Fetching page {page} via AJAX...")
            time.sleep(2)
            page_soup = self._fetch_ajax_page(page, filter_state, count=100)
            if page_soup:
                page_jobs = self._parse_jobs_from_soup(page_soup, existing_ids)
            else:
                print(f"  AJAX failed — trying ZenRows fallback for page {page}...")
                page_html = self._fetch_via_zenrows(
                f"{self.LISTINGS_URL}?level={requests.utils.quote(self.FILTER_LEVELS)}"
                f"&country={requests.utils.quote(self.FILTER_COUNTRIES)}"
                f"&count=100&sort=date&page={page}"
                )
                page_jobs = self._parse_jobs_from_soup(
                    BeautifulSoup(page_html, 'html.parser'), existing_ids
                ) if page_html else []

            self.jobs.extend(page_jobs)
            new_count += len(page_jobs)

        self._save_jobs()
        print(f"\n{'='*60}")
        print(f"  New jobs found    : {new_count}")
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

            print(f"\n  [{i + 1}/{len(jobs_to_update)}] {job.get('title', '')[:60]}")
            print(f"    URL: {url}")

            html = self._fetch_via_zenrows(url)
            if not html:
                print(f"    Failed to fetch detail page.")
                failed_count += 1
                continue

            soup = BeautifulSoup(html, 'html.parser')
            description = ''
            date_posted = ''

            # Primary: JSON-LD structured data
            ld_tag = soup.find('script', type='application/ld+json')
            if ld_tag:
                try:
                    data = json.loads(ld_tag.string)
                    description = data.get('description', '') or ''
                    date_posted = data.get('datePosted', '') or ''
                except (json.JSONDecodeError, AttributeError):
                    pass

            # Fallback: og:description meta tag
            if len(description) < 50:
                og_desc = soup.find('meta', property='og:description')
                if og_desc:
                    description = og_desc.get('content', '').strip()

            # Fallback: largest text-content div matching common job-detail selectors
            if len(description) < 50:
                candidates = [
                    div.get_text(separator='\n', strip=True)
                    for div in soup.find_all('div', class_=re.compile(
                        r'job.*desc|description|content|detail|cw-text', re.I
                    ))
                ]
                if candidates:
                    description = max(candidates, key=len)

            job['description'] = description
            job['description_fetched'] = True
            if date_posted and not job.get('posted_date'):
                job['posted_date'] = date_posted

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
    scraper = BeiersdorfScraper()
    scraper.run()