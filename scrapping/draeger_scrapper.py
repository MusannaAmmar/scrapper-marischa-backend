import os
import json
import re
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from urllib.parse import urlencode, urljoin, parse_qs, urlparse
from dotenv import load_dotenv
load_dotenv()

class DraegerScraper:
    """
    Scrapes Drägerwerk AG job listings from their career portal.

    Platform: MUZ Global Jobboard Client (jQuery-based, results injected via JS)
    Source URL:
        https://erecruitment.draeger.com/index.php
            ?ac=search_result
            &search_criterion_entry_level[]=9
            &search_criterion_channel[]=12
            &btn_dosearch=

    Job detail pages: /index.php?ac=jobad&id={job_id}
    Pagination:       /index.php?ac=search_result&page={n}&...
    """

    BASE_URL   = 'https://erecruitment.draeger.com'
    COMPANY    = 'Dräger'

    # Base search URL with required filters pre-applied
    SEARCH_URL = (
        'https://erecruitment.draeger.com/index.php'
        '?ac=search_result'
        '&search_criterion_entry_level%5B%5D=9'
        '&search_criterion_channel%5B%5D=12'
        '&btn_dosearch='
    )

    ZENROWS_API     = 'https://api.zenrows.com/v1/'
    ZENROWS_API_KEY = os.getenv('ZENROWS')

    def __init__(self, output_file='json_files/draeger_jobs.json'):
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
    #  Fetch helpers                                                       #
    # ------------------------------------------------------------------ #

    def _fetch_direct(self, url):
        headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 Chrome/120.0 Safari/537.36'
            ),
            'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8',
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
        """Fetch a JS-rendered page — required because results are injected by JavaScript.
        wait=8000 gives the MUZ jobboard AJAX call time to complete before returning HTML.
        wait_for waits until a job link is present in the DOM.
        """
        params = {
            'url': url,
            'apikey': self.ZENROWS_API_KEY,
            'js_render': 'true',
            'premium_proxy': 'true',
            'wait': '8000',
            'wait_for': 'a[href*="ac=jobad"]',
        }
        for attempt in range(4):
            wait = 2 ** attempt
            if attempt > 0:
                print(f"  Retry {attempt}/3 (ZenRows) – waiting {wait}s...")
                time.sleep(wait)
            try:
                resp = requests.get(self.ZENROWS_API, params=params, timeout=120)
                if resp.status_code == 200:
                    return resp.text
                print(f"  [warn] ZenRows {resp.status_code}: {resp.text[:200]}")
            except Exception as e:
                print(f"  [error] ZenRows: {e}")
        return None

    def _fetch_json_api(self, url):
        """
        MUZ Global Jobboard Client exposes results as JSON via the same URL
        with an extra 'output=json' parameter. This is the fastest path and
        requires no JS rendering.
        """
        json_url = url + '&output=json' if '?' in url else url + '?output=json'
        headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 Chrome/120.0 Safari/537.36'
            ),
            'Accept': 'application/json, text/javascript, */*',
            'X-Requested-With': 'XMLHttpRequest',
            'Referer': self.BASE_URL,
        }
        try:
            resp = requests.get(json_url, headers=headers, timeout=30)
            if resp.status_code == 200:
                ct = resp.headers.get('Content-Type', '')
                if 'json' in ct or resp.text.strip().startswith('{'):
                    print(f'  JSON API succeeded ({len(resp.text)} bytes)')
                    return resp.json()
        except Exception as e:
            print(f'  [warn] JSON API: {e}')
        return None

    def _fetch_html(self, url):
        """
        Fetch strategy (in order):
        1. JSON API  — fastest, no rendering needed (MUZ jobboard feature).
           Returns HTML synthesised from JSON if successful.
        2. Direct GET — works if the platform server-renders for bots.
        3. ZenRows JS render with wait — fallback for full client-side rendering.
        """
        # 1 — JSON API
        data = self._fetch_json_api(url)
        if data:
            html = self._json_to_html(data)
            if html and self._has_job_results(html):
                return html

        # 2 — Direct
        html = self._fetch_direct(url)
        if html and self._has_job_results(html):
            return html

        # 3 — ZenRows
        print('  Direct fetch returned no results — trying ZenRows (JS render + wait)...')
        return self._fetch_via_zenrows(url)

    @staticmethod
    def _json_to_html(data):
        """
        Convert the MUZ JSON API response into minimal HTML so the existing
        BeautifulSoup parser can process it without changes.
        The MUZ JSON payload typically contains a 'jobs' or 'results' list,
        each item having 'id', 'title', 'location', 'url' keys.
        """
        jobs = (
            data.get('jobs')
            or data.get('results')
            or data.get('jobads')
            or data.get('data')
            or []
        )
        if not jobs:
            return None

        parts = ['<html><body>']
        for job in jobs:
            jid  = job.get('id') or job.get('jobad_id') or ''
            title = job.get('title') or job.get('name') or ''
            loc   = (
                job.get('location')
                or job.get('city')
                or job.get('place')
                or ''
            )
            href = f'/index.php?ac=jobad&id={jid}'
            parts.append(
                f'<div class="job-item">'  
                f'<a href="{href}">{title}</a>'
                f'<span class="location">{loc}</span>'
                f'</div>'
            )
        parts.append('</body></html>')
        return '\n'.join(parts)

    @staticmethod
    def _has_job_results(html):
        """Quick check: does the page contain any job links?"""
        return bool(re.search(r'ac=jobad&id=', html))

    # ------------------------------------------------------------------ #
    #  Listings parser                                                     #
    # ------------------------------------------------------------------ #

    def _parse_listings_page(self, html, existing_ids):
        """Extract job cards from a rendered search-results page."""
        soup = BeautifulSoup(html, 'html.parser')
        new_jobs = []

        # ---- Collect all links pointing to job detail pages ---- #
        # Pattern: /index.php?ac=jobad&id=XXXXX
        job_links = soup.find_all('a', href=re.compile(r'ac=jobad&id=\d+'))

        seen_ids_this_page = set()

        for link in job_links:
            href = link.get('href', '')
            m = re.search(r'ac=jobad&id=(\d+)', href)
            if not m:
                continue
            job_id = m.group(1)

            if job_id in existing_ids or job_id in seen_ids_this_page:
                continue
            seen_ids_this_page.add(job_id)

            title = link.get_text(strip=True)
            if not title:
                # Title might be in a sibling/child heading element
                parent = link.find_parent(['li', 'article', 'div', 'tr'])
                if parent:
                    h = parent.find(['h1', 'h2', 'h3', 'h4', 'h5'])
                    if h:
                        title = h.get_text(strip=True)

            if not title:
                continue

            full_url = urljoin(self.BASE_URL, href)

            # Location — look in the nearest container for location text
            location = ''
            container = link.find_parent(['li', 'article', 'div', 'tr'])
            if container:
                # Common MUZ selectors for location
                loc_el = (
                    container.find(class_=re.compile(r'location|city|country|place', re.I))
                    or container.find('span', string=re.compile(r','))
                )
                if loc_el:
                    location = loc_el.get_text(strip=True)

            job = {
                'title':               title,
                'job_id':              job_id,
                'link':                full_url,
                'location':            location,
                'city':                location,
                'country':             '',
                'job_type':            '',
                'remote':              'Yes' if re.search(r'\bremote\b', location + ' ' + title, re.I) else '',
                'posted_date':         '',
                'salary':              '',
                'company':             self.COMPANY,
                'category':            '',
                'department':          '',
                'description':         '',
                'description_fetched': False,
                'skills':              [],
                'status':              'active',
                'source':              'Dräger',
                'date':                datetime.now(timezone.utc).strftime('%Y-%m-%d'),
            }
            new_jobs.append(job)
            existing_ids.add(job_id)
            print(f"  + {title[:70]} | {location[:50]}")

        return new_jobs

    def _get_next_page_url(self, soup, current_page):
        """
        Find the next-page link from a rendered listings page.
        MUZ jobboard wraps pagination in <ul class="pagination"> or similar.
        """
        # Look for a link containing the next page number
        next_page = current_page + 1

        # Check for explicit "Next" or numbered pagination links
        pagination = soup.find(class_=re.compile(r'pagination', re.I))
        if not pagination:
            return None

        for a in pagination.find_all('a', href=True):
            href = a.get('href', '')
            if re.search(rf'page={next_page}\b', href) or re.search(r'(next|›|»)', a.get_text(), re.I):
                return urljoin(self.BASE_URL, href)

        return None

    def _build_page_url(self, page):
        """Append page number to the base search URL."""
        if page == 1:
            return self.SEARCH_URL
        return self.SEARCH_URL + f'&page={page}'

    # ------------------------------------------------------------------ #
    #  Listings orchestrator                                               #
    # ------------------------------------------------------------------ #

    def parse_job_listings(self):
        existing_ids = self._get_existing_job_ids()
        total_new = 0
        page = 1

        print('\nFetching Dräger job listings...')

        while True:
            url = self._build_page_url(page)
            print(f'\n  Page {page}: {url}')

            html = self._fetch_html(url)
            if not html:
                print(f'  Failed to fetch page {page} — stopping.')
                break

            soup = BeautifulSoup(html, 'html.parser')

            # Print result count if visible
            count_el = soup.find(class_=re.compile(r'result.?count|search.?result.?header|total', re.I))
            if count_el:
                print(f'  {count_el.get_text(strip=True)}')

            new_jobs = self._parse_listings_page(html, existing_ids)
            if not new_jobs:
                print(f'  No new jobs on page {page} — done.')
                break

            self.jobs.extend(new_jobs)
            total_new += len(new_jobs)

            next_url = self._get_next_page_url(soup, page)
            if not next_url:
                break

            page += 1
            time.sleep(2)

        self._save_jobs()
        print(f"\n{'='*60}")
        print(f'  New jobs found    : {total_new}')
        print(f'  Total jobs stored : {len(self.jobs)}')
        print(f"{'='*60}")

    # ------------------------------------------------------------------ #
    #  Description fetching                                                #
    # ------------------------------------------------------------------ #

    def _extract_description_from_html(self, html):
        """Extract the full job description from a Dräger job detail page."""
        soup = BeautifulSoup(html, 'html.parser')

        # Primary: job detail content area (MUZ jobboard classes)
        for selector in [
            {'class': re.compile(r'job.?detail|jobad.?content|job.?description|vacancy.?detail', re.I)},
            {'id': re.compile(r'job.?detail|jobad.?content|vacancy', re.I)},
        ]:
            container = soup.find(attrs=selector)
            if container:
                for tag in container.find_all(['nav', 'header', 'footer', 'form', 'script', 'style']):
                    tag.decompose()
                return container.get_text(separator='\n', strip=True)

        # Fallback: main content area
        main = soup.find('main') or soup.find('div', id='content')
        if main:
            for tag in main.find_all(['nav', 'header', 'footer', 'form', 'script', 'style']):
                tag.decompose()
            return main.get_text(separator='\n', strip=True)

        # Last resort: og:description
        og = soup.find('meta', property='og:description')
        if og:
            return og.get('content', '').strip()

        return ''

    def _extract_metadata_from_html(self, html):
        """Extract location, job type, department from the job detail page."""
        soup = BeautifulSoup(html, 'html.parser')
        meta = {}

        page_text = soup.get_text(separator=' ')

        # Location: look for labelled fields
        for label_pattern, key in [
            (r'(?:Location|City|Place|Ort)\s*[:\|]\s*([^\n|<]+)', 'location'),
            (r'(?:Country|Land)\s*[:\|]\s*([^\n|<]+)',             'country'),
            (r'(?:Department|Abteilung|Function)\s*[:\|]\s*([^\n|<]+)', 'department'),
            (r'(?:Job\s*type|Employment\s*type|Stellenart)\s*[:\|]\s*([^\n|<]+)', 'job_type'),
        ]:
            m = re.search(label_pattern, page_text, re.I)
            if m:
                meta[key] = m.group(1).strip()

        # Try structured dl/dt/dd pairs
        for dl in soup.find_all('dl'):
            dts = dl.find_all('dt')
            dds = dl.find_all('dd')
            for dt, dd in zip(dts, dds):
                label = dt.get_text(strip=True).lower()
                value = dd.get_text(strip=True)
                if 'location' in label or 'city' in label or 'ort' in label:
                    meta.setdefault('location', value)
                elif 'country' in label or 'land' in label:
                    meta.setdefault('country', value)
                elif 'department' in label or 'function' in label:
                    meta.setdefault('department', value)
                elif 'type' in label or 'art' in label:
                    meta.setdefault('job_type', value)

        return meta

    def fetch_job_descriptions(self, delay=2):
        jobs_to_update = [j for j in self.jobs if not j.get('description_fetched', False)]
        if not jobs_to_update:
            print('\nAll jobs already have descriptions.')
            return

        print(f'\nFetching descriptions for {len(jobs_to_update)} jobs...')
        success_count = 0
        failed_count  = 0

        for i, job in enumerate(jobs_to_update):
            url = job.get('link', '')
            print(f"\n  [{i + 1}/{len(jobs_to_update)}] {job.get('title', '')[:60]}")

            description = ''

            if url:
                html = self._fetch_html(url)
                if html:
                    description = self._extract_description_from_html(html)
                    description = re.sub(r'\n{3,}', '\n\n', description).strip()

                    meta = self._extract_metadata_from_html(html)
                    if meta.get('location'):
                        job['location'] = meta['location']
                        job['city']     = meta['location']
                    if meta.get('country'):
                        job['country'] = meta['country']
                    if meta.get('department'):
                        job['department'] = meta['department']
                    if meta.get('job_type'):
                        job['job_type'] = meta['job_type']

            # Trim to 400 words
            if description:
                words = description.split()
                if len(words) > 400:
                    description = ' '.join(words[:400]) + '...'

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
    scraper = DraegerScraper(output_file='json_files/draeger_jobs.json')
    scraper.run()
