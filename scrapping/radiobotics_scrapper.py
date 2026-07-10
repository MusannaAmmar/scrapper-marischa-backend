import os
import json
import re
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

class RadioboticsScraper:
    """
    Scrapes Radiobotics job listings from their WordPress/Elementor careers page.
    Jobs are rendered server-side in an Elementor loop-grid under:
      https://radiobotics.com/company/careers/#jobs
    Each job links to an individual WordPress post:
      https://radiobotics.com/open-position/{slug}/
    """

    BASE_URL    = 'https://radiobotics.com'
    CAREERS_URL = 'https://radiobotics.com/company/careers/'
    COMPANY     = 'Radiobotics'

    ZENROWS_API     = 'https://api.zenrows.com/v1/'
    ZENROWS_API_KEY = os.getenv('ZENROWS')

    def __init__(self, output_file='json_files/radiobotics_jobs.json'):
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

    def parse_job_listings(self):
        print("Fetching Radiobotics job listings from careers page...")
        existing_ids = self._get_existing_job_ids()
        new_count = 0

        html = self._fetch_html(self.CAREERS_URL)
        if not html:
            print("  Failed to fetch careers page.")
            return

        soup = BeautifulSoup(html, 'html.parser')

        # Each job card lives in a div with class "e-loop-item"
        job_cards = soup.find_all('div', class_='e-loop-item')
        print(f"  Found {len(job_cards)} job card(s)")

        for card in job_cards:
            # Extract WordPress post ID from class "e-loop-item-{id}"
            job_id = ''
            for cls in card.get('class', []):
                m = re.match(r'^e-loop-item-(\d+)$', cls)
                if m:
                    job_id = m.group(1)
                    break

            if not job_id or job_id in existing_ids:
                continue

            # Title and link
            title_tag = card.find('h3', class_='elementor-heading-title')
            if not title_tag:
                continue
            link_tag = title_tag.find('a')
            if not link_tag:
                continue
            title = link_tag.get_text(strip=True)
            link  = link_tag.get('href', '').strip()

            # Short excerpt shown on the listings page
            excerpt = ''
            excerpt_div = card.find(class_='elementor-widget-theme-post-excerpt')
            if excerpt_div:
                inner = excerpt_div.find(class_='elementor-widget-container')
                excerpt = (inner or excerpt_div).get_text(separator=' ', strip=True)

            # Category from CSS class "category-{slug}" (skip the generic one)
            category = 'Open Position'
            for cls in card.get('class', []):
                if cls.startswith('category-') and cls != 'category-open-position':
                    category = cls.replace('category-', '').replace('-', ' ').title()
                    break

            job = {
                'title':               title,
                'job_id':              job_id,
                'link':                link,
                'location':            'Copenhagen, Denmark',
                'city':                'Copenhagen',
                'country':             'Denmark',
                'job_type':            '',
                'remote':              '',
                'posted_date':         '',
                'salary':              '',
                'company':             self.COMPANY,
                'category':            category,
                'department':          category,
                'description':         excerpt,
                'description_fetched': False,
                'skills':              [],
                'status':              'active',
                'source':              'Radiobotics',
                'date':                datetime.now(timezone.utc).strftime('%Y-%m-%d'),
            }
            self.jobs.append(job)
            existing_ids.add(job_id)
            new_count += 1
            print(f"  + {title[:70]} | {category}")

        self._save_jobs()
        print(f"\n{'='*60}")
        print(f"  New jobs found    : {new_count}")
        print(f"  Total jobs stored : {len(self.jobs)}")
        print(f"{'='*60}")

    # ------------------------------------------------------------------ #
    #  Description fetching                                                #
    # ------------------------------------------------------------------ #

    def _extract_description_from_html(self, html):
        """Extract full job description from an individual job detail page."""
        soup = BeautifulSoup(html, 'html.parser')

        # WordPress/Elementor content area
        content = (
            soup.find('div', class_=re.compile(r'entry-content|post-content', re.I))
            or soup.find('article')
            or soup.find('main')
        )
        if content:
            return content.get_text(separator='\n', strip=True)

        # Last resort: og:description meta tag
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

            # Trim to 400 words
            if description:
                words = description.split()
                if len(words) > 400:
                    description = ' '.join(words[:400]) + '...'

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
    scraper = RadioboticsScraper(output_file='json_files/radiobotics_jobs.json')
    scraper.run()