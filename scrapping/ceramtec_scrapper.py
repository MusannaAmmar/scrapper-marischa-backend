import os
import json
import re
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

class CeramTecScraper:
    """
    Scrapes CeramTec Group job listings from their Abacus-Umantis ATS portal.
    Listings are paginated (10 per page) at:
      https://recruitingapp-5382.de.umantis.com/Jobs/1?lang=ger&ContentOnly=
    Individual job descriptions at:
      https://recruitingapp-5382.de.umantis.com/Vacancies/{ID}/Description/1
    """

    BASE_URL    = 'https://recruitingapp-5382.de.umantis.com'
    CAREERS_URL = 'https://recruitingapp-5382.de.umantis.com/Jobs/1?lang=ger&ContentOnly='
    COMPANY     = 'CeramTec Group'

    ZENROWS_API     = 'https://api.zenrows.com/v1/'
    ZENROWS_API_KEY = os.getenv('ZENROWS')

    def __init__(self, output_file='json_files/ceramtec_jobs.json'):
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

    def _parse_page(self, html, existing_ids):
        """Parse one page of job listings. Returns (new_jobs, next_page_url)."""
        soup = BeautifulSoup(html, 'html.parser')
        new_jobs = []

        rows = soup.find_all('tr', class_=re.compile(r'tableaslist_contentrow'))
        for row in rows:
            cell = row.find('td', class_='tableaslist_cell')
            if not cell:
                continue

            # Title and link — .HSTableLinkSubTitle inside element_3473
            title_span = cell.find(class_='tableaslist_element_3473')
            if not title_span:
                continue
            link_tag = title_span.find('a')
            if not link_tag:
                continue

            title = link_tag.get_text(strip=True)
            href  = link_tag.get('href', '').strip()

            # Job ID from path /Vacancies/{ID}/Description/1
            m = re.search(r'/Vacancies/(\d+)/', href)
            if not m:
                continue
            job_id = m.group(1)
            if job_id in existing_ids:
                continue

            full_url = self.BASE_URL + href if href.startswith('/') else href

            # Posted date — "Online seit: MM/DD/YYYY"
            date_span = cell.find(class_='tableaslist_element_3472')
            posted_date = ''
            if date_span:
                raw = date_span.get_text(strip=True)
                dm = re.search(r'(\d{2}/\d{2}/\d{4})', raw)
                if dm:
                    posted_date = dm.group(1)

            # Work type — "Art: Vollzeit" / "Art: Teilzeit"
            type_span = cell.find(class_='tableaslist_element_3474')
            job_type = ''
            if type_span:
                raw = type_span.get_text(strip=True).replace('\xa0', ' ')
                tm = re.search(r'Art:\s*(.+)', raw)
                if tm:
                    job_type = tm.group(1).strip()

            # Contract type — "Vertragsart: Unbefristet" / "Vertragsart: Befristet"
            contract_span = cell.find(class_='tableaslist_element_3475')
            contract_type = ''
            if contract_span:
                raw = contract_span.get_text(strip=True).replace('\xa0', ' ')
                cm = re.search(r'Vertragsart:\s*(.+)', raw)
                if cm:
                    contract_type = cm.group(1).strip()

            # Location — element_26475
            loc_span = cell.find(class_='tableaslist_element_26475')
            location = ''
            if loc_span:
                location = loc_span.get_text(strip=True).replace('\xa0', '').strip(' |')

            job = {
                'title':               title,
                'job_id':              job_id,
                'link':                full_url,
                'location':            location,
                'city':                location,
                'country':             'Germany',
                'job_type':            job_type,
                'contract_type':       contract_type,
                'remote':              '',
                'posted_date':         posted_date,
                'salary':              '',
                'company':             self.COMPANY,
                'category':            '',
                'department':          '',
                'description':         '',
                'description_fetched': False,
                'skills':              [],
                'status':              'active',
                'source':              'CeramTec Group',
                'date':                datetime.now(timezone.utc).strftime('%Y-%m-%d'),
            }
            new_jobs.append(job)
            existing_ids.add(job_id)
            print(f"  + {title[:70]} | {location} | {job_type}")

        # Pagination: find the next-page href from data attribute.
        # next_href looks like "?tc66856=p2&_search_token66856=...#anchor"
        # We merge those params with the base CAREERS_URL params.
        next_url = None
        nav = soup.find(attrs={'data-pagination-next-href': True})
        if nav:
            next_href = nav['data-pagination-next-href']
            if next_href.startswith('?'):
                # Strip any fragment, then append the extra params to the base URL
                next_href_clean = next_href.split('#')[0]  # remove #connectortable_...
                next_url = self.CAREERS_URL + '&' + next_href_clean.lstrip('?')
            else:
                next_url = self.BASE_URL + next_href

        return new_jobs, next_url

    def parse_job_listings(self):
        print("Fetching CeramTec Group job listings (Umantis ATS)...")
        existing_ids = self._get_existing_job_ids()
        new_count    = 0
        page_num     = 1
        url          = self.CAREERS_URL

        while url:
            print(f"\n  -- Page {page_num} --")
            html = self._fetch_html(url)
            if not html:
                print("  Failed to fetch page.")
                break

            new_jobs, next_url = self._parse_page(html, existing_ids)

            if not new_jobs and page_num > 1:
                # No new jobs on this page — all already known or page empty
                break

            self.jobs.extend(new_jobs)
            new_count += len(new_jobs)

            # Stop if no next page or no jobs were found at all
            if not next_url or not new_jobs:
                break

            url = next_url
            page_num += 1
            time.sleep(2)

        self._save_jobs()
        print(f"\n{'='*60}")
        print(f"  New jobs found    : {new_count}")
        print(f"  Total jobs stored : {len(self.jobs)}")
        print(f"{'='*60}")

    # ------------------------------------------------------------------ #
    #  Description fetching                                                #
    # ------------------------------------------------------------------ #

    def _extract_description_from_html(self, html):
        """Extract job description from a Umantis vacancy detail page."""
        soup = BeautifulSoup(html, 'html.parser')

        # Umantis renders description inside .PositionDescription or .text-formated
        desc_tag = (
            soup.find(class_=re.compile(r'PositionDescription|positiondescription', re.I))
            or soup.find(class_=re.compile(r'vacancy.?description|job.?description', re.I))
            or soup.find('div', class_='container_content')
            or soup.find('main')
        )
        if desc_tag:
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
    scraper = CeramTecScraper(output_file='json_files/ceramtec_jobs.json')
    scraper.run()