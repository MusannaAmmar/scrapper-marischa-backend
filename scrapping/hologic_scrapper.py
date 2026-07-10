import os
import json
import re
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from dotenv import load_dotenv


load_dotenv()

class HologicScraper:
    """
    Scrapes Hologic EMEA job listings from two job-family search pages:
      - Marketing:
          https://emea.careers.hologic.com/en/search?region=EMEA&jobFamily=Marketing
      - Administration & Executive Support:
          https://emea.careers.hologic.com/en/search?region=EMEA&jobFamily=Administration+%26+Executive+Support

    Both feeds are stored in a single hologic_jobs.json file.
    Individual job detail pages:
      https://emea.careers.hologic.com/en/search/{job_id}/{slug}
    """

    BASE_URL = 'https://emea.careers.hologic.com'
    COMPANY  = 'Hologic'

    # (label_for_department, full_search_url)
    SEARCH_URLS = [
        ('Marketing',
         'https://emea.careers.hologic.com/en/search?region=EMEA&jobFamily=Marketing'),
        ('Administration & Executive Support',
         'https://emea.careers.hologic.com/en/search?region=EMEA&jobFamily=Administration+%26+Executive+Support'),
    ]

    ZENROWS_API     = 'https://api.zenrows.com/v1/'
    ZENROWS_API_KEY = os.getenv('ZENROWS')

    def __init__(self, output_file='json_files/hologic_jobs.json'):
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
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8',
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
                resp = requests.get(self.ZENROWS_API, params=params, timeout=60)
                if resp.status_code == 200:
                    return resp.text
                print(f"  [warn] ZenRows {resp.status_code}: {resp.text[:200]}")
            except Exception as e:
                print(f"  [error] ZenRows: {e}")
        return None

    def _fetch_html(self, url):
        html = self._fetch_direct(url)
        if not html:
            print("  Direct fetch failed — trying ZenRows...")
            html = self._fetch_via_zenrows(url)
        return html

    # ------------------------------------------------------------------ #
    #  Listings parser                                                     #
    # ------------------------------------------------------------------ #

    def _parse_listings_page(self, html, department, existing_ids):
        """Parse all job cards from one search result page."""
        soup = BeautifulSoup(html, 'html.parser')
        new_jobs = []

        cards = soup.find_all('div', class_='result-list-box')
        for card in cards:
            col = card.find('div', class_=re.compile(r'\bcol-sm-10\b'))
            if not col:
                continue

            # Title
            h4 = col.find('h4')
            if not h4:
                continue
            title = h4.get_text(strip=True)

            # Locations — collect all spans inside .basicinfo
            basicinfo = col.find('div', class_='basicinfo')
            locations = []
            if basicinfo:
                for span in basicinfo.find_all('span'):
                    loc = span.get_text(strip=True)
                    if loc:
                        locations.append(loc)
            location = ', '.join(locations)

            # Excerpt — text nodes directly inside col after stripping h4 + basicinfo
            if basicinfo:
                basicinfo.extract()
            if h4:
                h4.extract()
            excerpt = col.get_text(separator=' ', strip=True)
            excerpt = re.sub(r'\s+', ' ', excerpt).strip()

            # Link and job ID
            link_tag = card.find('a', class_='lnkJobDetails')
            if not link_tag:
                continue
            raw_href = link_tag.get('href', '').strip()
            # href format: /search/{job_id}/{slug}
            # The page JS prepends /{lang}, so we do: /en + raw_href
            if raw_href.startswith('/en/'):
                full_url = self.BASE_URL + raw_href
            elif raw_href.startswith('/'):
                full_url = self.BASE_URL + '/en' + raw_href
            else:
                full_url = raw_href

            m = re.search(r'/search/(\d+)/', raw_href)
            if not m:
                continue
            job_id = m.group(1)

            if job_id in existing_ids:
                continue

            job = {
                'title':               title,
                'job_id':              job_id,
                'link':                full_url,
                'location':            location,
                'city':                location,
                'country':             '',
                'job_type':            '',
                'remote':              'Yes' if re.search(r'\bremote\b', location + ' ' + excerpt, re.I) else '',
                'posted_date':         '',
                'salary':              '',
                'company':             self.COMPANY,
                'category':            department,
                'department':          department,
                'description':         excerpt,
                'description_fetched': False,
                'skills':              [],
                'status':              'active',
                'source':              'Hologic',
                'date':                datetime.now(timezone.utc).strftime('%Y-%m-%d'),
            }
            new_jobs.append(job)
            existing_ids.add(job_id)
            print(f"  + {title[:70]} | {location[:50]} | {department}")

        return new_jobs

    def parse_job_listings(self):
        existing_ids = self._get_existing_job_ids()
        total_new = 0

        for department, url in self.SEARCH_URLS:
            print(f"\nFetching [{department}] jobs...")
            print(f"  URL: {url}")

            html = self._fetch_html(url)
            if not html:
                print(f"  Failed to fetch page for [{department}].")
                continue

            # Check if any results
            soup = BeautifulSoup(html, 'html.parser')
            result_title = soup.find(class_='result-title')
            if result_title:
                print(f"  {result_title.get_text(strip=True)}")

            new_jobs = self._parse_listings_page(html, department, existing_ids)
            self.jobs.extend(new_jobs)
            total_new += len(new_jobs)

            time.sleep(2)

        self._save_jobs()
        print(f"\n{'='*60}")
        print(f"  New jobs found    : {total_new}")
        print(f"  Total jobs stored : {len(self.jobs)}")
        print(f"{'='*60}")

    # ------------------------------------------------------------------ #
    #  Description fetching                                                #
    # ------------------------------------------------------------------ #

    def _extract_description_from_html(self, html):
        """Extract full job description from a Hologic job detail page."""
        soup = BeautifulSoup(html, 'html.parser')

        # Primary target: div.jobdetail
        desc_tag = (
            soup.find('div', class_='jobdetail')
            or soup.find('div', class_='layout-content')
            or soup.find('main')
        )
        if desc_tag:
            # Remove nav/header noise if present
            for tag in desc_tag.find_all(['nav', 'header', 'footer']):
                tag.decompose()
            return desc_tag.get_text(separator='\n', strip=True)

        og = soup.find('meta', property='og:description')
        if og:
            return og.get('content', '').strip()

        return ''

    def fetch_job_descriptions(self, delay=2):
        jobs_to_update = [j for j in self.jobs if not j.get('description_fetched', False)]
        if not jobs_to_update:
            print("\nAll jobs already have descriptions.")
            return

        print(f"\nFetching descriptions for {len(jobs_to_update)} jobs...")
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

            job['description']         = description
            job['description_fetched'] = True

            if description:
                print(f"    Description: {len(description.split())} words")
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

    def run(self, fetch_descriptions=True):
        self.parse_job_listings()
        if fetch_descriptions:
            self.fetch_job_descriptions()


if __name__ == '__main__':
    scraper = HologicScraper(output_file='json_files/hologic_jobs.json')
    scraper.run()