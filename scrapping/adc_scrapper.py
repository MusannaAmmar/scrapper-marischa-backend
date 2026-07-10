import os
import json
import re
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

class ADCScraper:
    """
    Scrapes Amsterdam Data Collective job listings.
    ADC hosts their careers on a Recruitee custom domain:
      https://careers.amsterdamdatacollective.com
    Listings and descriptions come from the Recruitee public JSON API.
    RSC payload from the main site is used as fallback for listings only.
    """

    BASE_URL      = 'https://adc-consulting.com'
    CAREERS_URL   = 'https://adc-consulting.com/careers/'
    CAREERS_DOMAIN = 'https://careers.amsterdamdatacollective.com'
    COMPANY       = 'Amsterdam Data Collective'

    ZENROWS_API     = 'https://api.zenrows.com/v1/'
    ZENROWS_API_KEY = os.getenv('ZENROWS')

    def __init__(self, output_file='json_files/adc_jobs.json'):
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

    def _fetch_json(self, url):
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36',
            'Accept': 'application/json',
        }
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code == 200:
                return resp.json()
            print(f"  [warn] HTTP {resp.status_code} for {url}")
        except Exception as e:
            print(f"  [error] {e}")
        return None

    def _fetch_via_zenrows(self, url):
        params = {'url': url, 
                'apikey': self.ZENROWS_API_KEY,
                'js_render': 'true',
	            'premium_proxy': 'true',}
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
    #  RSC payload parser                                                  #
    # ------------------------------------------------------------------ #

    def _extract_rsc_payload(self, html):
        """Concatenate all self.__next_f.push([1, "..."]) string content."""
        pattern = r'self\.__next_f\.push\(\[1\s*,\s*"((?:[^"\\]|\\.)*)"\]\)'
        chunks = re.findall(pattern, html)
        combined = ''
        for chunk in chunks:
            # unescape JSON-encoded string
            combined += chunk.encode('utf-8').decode('unicode_escape')
        return combined

    # ------------------------------------------------------------------ #
    #  Listings                                                            #
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    #  Helper: extract a JSON array by bracket matching                   #
    # ------------------------------------------------------------------ #

    def _extract_json_array(self, text, key):
        """Find "key":[ and return the full array string by bracket depth."""
        search = f'"{key}":'
        idx = text.find(search)
        if idx == -1:
            return None
        start = text.find('[', idx + len(search))
        if start == -1:
            return None
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == '[':
                depth += 1
            elif ch == ']':
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        return None

    # ------------------------------------------------------------------ #
    #  Listings                                                            #
    # ------------------------------------------------------------------ #

    def parse_job_listings(self):
        print("Fetching ADC job listings via Recruitee API (custom domain)...")
        existing_ids = self._get_existing_job_ids()
        new_count = 0

        # Primary: Recruitee public API at the custom careers domain
        data = self._fetch_json(f"{self.CAREERS_DOMAIN}/api/offers/")
        if data and data.get('offers'):
            offers = data['offers']
            print(f"  Total offers returned: {len(offers)}")
            for offer in offers:
                job_id = str(offer.get('id', ''))
                if not job_id or job_id in existing_ids:
                    continue

                slug        = offer.get('slug', '')
                title       = offer.get('title', '').strip()
                city        = offer.get('city', '') or ''
                country     = offer.get('country_code', '') or ''
                location    = offer.get('location', '') or f"{city}, {country}".strip(', ')
                department  = offer.get('department', '') or ''
                emp_type    = offer.get('employment_type_code', '') or ''
                remote      = offer.get('remote', False)
                careers_url = f"{self.CAREERS_DOMAIN}/o/{slug}" if slug else (
                    offer.get('careers_url', '') or f"{self.CAREERS_DOMAIN}/o/{job_id}"
                )

                job = {
                    'title':               title,
                    'job_id':              job_id,
                    'link':                careers_url,
                    'location':            location,
                    'city':                city,
                    'country':             country,
                    'job_type':            emp_type,
                    'remote':              'Yes' if remote else '',
                    'posted_date':         offer.get('created_at', '') or '',
                    'salary':              '',
                    'company':             self.COMPANY,
                    'category':            department,
                    'department':          department,
                    'description':         '',
                    'description_fetched': False,
                    'skills':              [],
                    'status':              'active',
                    'source':              'Amsterdam Data Collective',
                    'date':                datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                }
                self.jobs.append(job)
                existing_ids.add(job_id)
                new_count += 1
                print(f"  + {title[:70]} | {location} | {department}")

        else:
            # Fallback: parse Next.js RSC payload from main site
            print("  Recruitee API unavailable — falling back to RSC payload...")
            html = self._fetch_html(self.CAREERS_URL)
            if not html:
                print("  Failed to fetch careers page.")
                return

            payload = self._extract_rsc_payload(html)
            countries_json = self._extract_json_array(payload, 'countries')
            if not countries_json:
                countries_json = self._extract_json_array(html, 'countries')

            if not countries_json:
                print("  [warn] Could not find 'countries' vacancies block in page.")
                return

            try:
                countries_data = json.loads(countries_json)
            except json.JSONDecodeError as e:
                print(f"  [error] Failed to parse countries JSON: {e}")
                return

            for country_block in countries_data:
                country   = country_block.get('country', '')
                vacancies = country_block.get('vacancies', [])
                for vacancy in vacancies:
                    job_id = str(vacancy.get('id', ''))
                    if not job_id or job_id in existing_ids:
                        continue

                    title    = vacancy.get('title', '').strip()
                    location = vacancy.get('location', '')
                    # RSC internal links (/careers/slug) are NOT the real job URLs.
                    # We can't fetch descriptions from them; store as placeholder.
                    full_url = f"{self.CAREERS_DOMAIN}/o/{vacancy.get('link','').split('/')[-1]}"

                    job = {
                        'title':               title,
                        'job_id':              job_id,
                        'link':                full_url,
                        'location':            location,
                        'city':                location,
                        'country':             country,
                        'job_type':            '',
                        'remote':              '',
                        'posted_date':         '',
                        'salary':              '',
                        'company':             self.COMPANY,
                        'category':            '',
                        'department':          '',
                        'description':         '',
                        'description_fetched': False,
                        'skills':              [],
                        'status':              'active',
                        'source':              'Amsterdam Data Collective',
                        'date':                datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                    }
                    self.jobs.append(job)
                    existing_ids.add(job_id)
                    new_count += 1
                    print(f"  + {title[:70]} | {location} | {country}")

        self._save_jobs()
        print(f"\n{'='*60}")
        print(f"  New jobs found    : {new_count}")
        print(f"  Total jobs stored : {len(self.jobs)}")
        print(f"{'='*60}")

        # ------------------------------------------------------------------ #
        #  Description fetching                                                #
        # ------------------------------------------------------------------ #

    def _extract_description_from_html(self, html):
        """Parse description from a Next.js job detail page."""
        # Try RSC payload first — look for a description/body field
        payload = self._extract_rsc_payload(html)
        for key in ('"description"', '"body"', '"content"', '"jobDescription"'):
            m = re.search(key + r'\s*:\s*"((?:[^"\\]|\\.){100,})"', payload)
            if m:
                text = m.group(1).encode('utf-8').decode('unicode_escape')
                soup = BeautifulSoup(text, 'html.parser')
                return soup.get_text(separator='\n', strip=True)

        # Fallback: parse visible HTML
        soup = BeautifulSoup(html, 'html.parser')
        desc_tag = (
            soup.find(class_=re.compile(r'job.?desc|description|content|body', re.I))
            or soup.find('article')
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
        failed_count = 0

        for i, job in enumerate(jobs_to_update):
            job_id = job.get('job_id', '')
            url    = job.get('link', '')
            print(f"\n  [{i + 1}/{len(jobs_to_update)}] {job.get('title', '')[:60]}")

            description = ''

            # Primary: Recruitee JSON API detail endpoint
            if job_id:
                detail = self._fetch_json(f"{self.CAREERS_DOMAIN}/api/offers/{job_id}")
                if detail:
                    offer = detail.get('offer', detail)
                    raw = offer.get('description', '') or ''
                    if raw:
                        soup = BeautifulSoup(raw, 'html.parser')
                        description = soup.get_text(separator='\n', strip=True)
                        description = re.sub(r'\n{3,}', '\n\n', description).strip()

            # Fallback: scrape the Recruitee careers page HTML
            if len(description) < 50 and url and self.CAREERS_DOMAIN in url:
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
    scraper = ADCScraper(output_file='json_files/adc_jobs.json')
    scraper.run()