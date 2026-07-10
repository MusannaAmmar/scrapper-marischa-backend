import os
import json
import re
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from dotenv import load_dotenv


load_dotenv()

class SectraScraper:
    """
    Scrapes Sectra job listings from the Jobylon embed widget page.
    The career page https://career.sectra.com/job-opportunities/ loads a
    Jobylon iframe (company_id=2380, version=v2). All job data is baked
    into the iframe HTML as JBL.embed_v2['jobs'] = [...] JavaScript.

    Embed URL : https://cdn.jobylon.com/jobs/companies/2380/embed/v2/?target=jobylon-jobs-widget&page_size=100
    Detail URL: https://emp.jobylon.com/jobs/{id}-{slug}/
    """

    COMPANY     = 'Sectra'
    EMBED_URL   = 'https://cdn.jobylon.com/jobs/companies/2380/embed/v2/?target=jobylon-jobs-widget&page_size=100'
    DETAIL_BASE = 'https://emp.jobylon.com'

    ZENROWS_API     = 'https://api.zenrows.com/v1/'
    ZENROWS_API_KEY = os.getenv('ZENROWS')

    def __init__(self, output_file='json_files/sectra_jobs.json'):
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
        os.makedirs(os.path.dirname(self.output_file), exist_ok=True)
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
    #  JS embed parser                                                     #
    # ------------------------------------------------------------------ #

    def _parse_js_string(self, value):
        """Decode JS unicode escapes like \u002D and strip outer quotes."""
        return value.encode('utf-8').decode('unicode_escape').encode('latin-1').decode('utf-8')

    def _extract_field(self, block, key):
        """Extract a single-line field: key: 'value'"""
        m = re.search(rf"\b{key}\s*:\s*'([^']*)'" , block)
        if m:
            try:
                return self._parse_js_string(m.group(1))
            except Exception:
                return m.group(1)
        return ''

    def _extract_array(self, block, key):
        """Extract string items from a JS array block for a given key."""
        # Match key: [ ... ] (multiline)
        m = re.search(rf"\b{key}\s*:\s*\[(.*?)\]", block, re.DOTALL)
        if not m:
            return []
        items = re.findall(r"'([^']*)'" , m.group(1))
        results = []
        for item in items:
            try:
                results.append(self._parse_js_string(item))
            except Exception:
                results.append(item)
        return results

    def _parse_embed_jobs(self, html):
        """
        Extract job list from JBL.embed_v2['jobs'] = [...] inside the page JS.
        Returns a list of dicts with raw job data.
        """
        # Find the jobs array JS block
        m = re.search(r"JBL\.embed_v2\['jobs'\]\s*=\s*\[", html)
        if not m:
            return []

        start = m.end() - 1  # include the opening [
        # Walk the string tracking bracket depth to find matching ]
        depth = 0
        i = start
        while i < len(html):
            if html[i] == '[':
                depth += 1
            elif html[i] == ']':
                depth -= 1
                if depth == 0:
                    break
            i += 1
        jobs_js = html[start:i + 1]

        # Split into individual job blocks by top-level { }
        job_blocks = []
        depth = 0
        block_start = None
        for idx, ch in enumerate(jobs_js):
            if ch == '{':
                if depth == 0:
                    block_start = idx
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0 and block_start is not None:
                    job_blocks.append(jobs_js[block_start:idx + 1])
                    block_start = None

        return job_blocks

    # ------------------------------------------------------------------ #
    #  Listings parser                                                     #
    # ------------------------------------------------------------------ #

    def parse_job_listings(self):
        print(f"\nFetching Sectra jobs from Jobylon embed...")
        print(f"  URL: {self.EMBED_URL}")

        html = self._fetch_direct(self.EMBED_URL)
        if not html:
            print("  Failed to fetch embed page.")
            return

        job_blocks = self._parse_embed_jobs(html)
        print(f"  Found {len(job_blocks)} job blocks in embed JS")

        existing_ids = self._get_existing_job_ids()
        new_jobs = []

        for block in job_blocks:
            job_id = self._extract_field(block, 'id')
            if not job_id or job_id in existing_ids:
                continue

            # Skip internal jobs
            if re.search(r"is_internal\s*:\s*true", block):
                continue

            title = self._extract_field(block, 'title')
            url_path = self._extract_field(block, 'url')  # e.g. /jobs/350451-sectra-ai-adoption-specialist/
            link = self.DETAIL_BASE + url_path if url_path else ''

            # Locations (cities)
            cities = self._extract_array(block, 'locations')
            city = cities[0] if cities else ''

            # Countries from layers_2
            countries = self._extract_array(block, "'layers_2'")
            country = ', '.join(countries)

            location_parts = list(dict.fromkeys(cities + countries))  # deduplicate
            location = ', '.join(location_parts)

            # Function / department
            department = self._extract_field(block, 'function')

            # Category from layers_1
            categories = self._extract_array(block, "'layers_1'")
            category = categories[0] if categories else department

            # Employment type and workspace
            job_type = self._extract_field(block, 'employment_type')
            workspace = self._extract_field(block, 'workspace')
            remote = 'Yes' if workspace.lower() == 'remote' else ''

            # Published date
            posted_date_raw = self._extract_field(block, 'published_date')
            posted_date = ''
            if posted_date_raw:
                try:
                    posted_date = datetime.strptime(posted_date_raw, '%B %d, %Y').strftime('%Y-%m-%d')
                except Exception:
                    posted_date = posted_date_raw

            job = {
                'title':               title,
                'job_id':              job_id,
                'link':                link,
                'location':            location,
                'city':                city,
                'country':             country,
                'job_type':            job_type,
                'remote':              remote,
                'posted_date':         posted_date,
                'salary':              '',
                'company':             self.COMPANY,
                'category':            category,
                'department':          department,
                'description':         '',
                'description_fetched': False,
                'skills':              [],
                'status':              'active',
                'source':              'Sectra',
                'date':                datetime.now(timezone.utc).strftime('%Y-%m-%d'),
            }
            new_jobs.append(job)
            existing_ids.add(job_id)
            print(f"  + {title[:70]} | {location[:50]}")

        self.jobs.extend(new_jobs)
        self._save_jobs()

        print(f"\n{'='*60}")
        print(f"  New jobs found    : {len(new_jobs)}")
        print(f"  Total jobs stored : {len(self.jobs)}")
        print(f"{'='*60}")

    # ------------------------------------------------------------------ #
    #  Description fetching                                                #
    # ------------------------------------------------------------------ #

    def _extract_description_from_html(self, html):
        """Extract full job description from a Jobylon job detail page."""
        soup = BeautifulSoup(html, 'html.parser')

        # Jobylon detail pages use .jobdetail or similar container
        desc_tag = (
            soup.find('div', class_='jobdetail')
            or soup.find('div', class_=re.compile(r'job.?description|job.?body|description-text', re.I))
            or soup.find('section', class_=re.compile(r'description|job', re.I))
            or soup.find('article')
            or soup.find('main')
        )
        if desc_tag:
            for tag in desc_tag.find_all(['nav', 'header', 'footer', 'script', 'style']):
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
    scraper = SectraScraper(output_file='json_files/sectra_jobs.json')
    scraper.run()


    ZENROWS_API     = 'https://api.zenrows.com/v1/'
    ZENROWS_API_KEY = '3116009d20b3d8766b0a8ed0697414e6dba83b90'

    def __init__(self, output_file='json_files/sectra_jobs.json'):
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
        os.makedirs(os.path.dirname(self.output_file), exist_ok=True)
        with open(self.output_file, 'w', encoding='utf-8') as f:
            json.dump(self.jobs, f, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------ #
    #  Fetch helpers                                                       #
    # ------------------------------------------------------------------ #

    def _fetch_direct(self, url, as_json=False):
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36',
            'Accept': 'application/json, text/html, */*;q=0.8',
        }
        for attempt in range(4):
            wait = 2 ** attempt
            if attempt > 0:
                print(f"  Retry {attempt}/3 – waiting {wait}s...")
                time.sleep(wait)
            try:
                resp = requests.get(url, headers=headers, timeout=30)
                if resp.status_code == 200:
                    return resp.json() if as_json else resp.text
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
    #  Listings parser (Jobylon JSON feed)                                 #
    # ------------------------------------------------------------------ #

    def parse_job_listings(self):
        print(f"\nFetching Sectra jobs from Jobylon feed...")
        print(f"  URL: {self.JOBYLON_FEED}")

        data = self._fetch_direct(self.JOBYLON_FEED, as_json=True)
        if not data:
            print("  Failed to fetch Jobylon feed.")
            return

        existing_ids = self._get_existing_job_ids()
        new_jobs = []

        jobs_list = data if isinstance(data, list) else data.get('jobs', data.get('results', []))

        for item in jobs_list:
            job_id = str(item.get('id', '') or item.get('uuid', ''))
            if not job_id or job_id in existing_ids:
                continue

            title = item.get('title', '').strip()

            # Location — Jobylon v2 nests locations as a list
            locations = item.get('locations', [])
            location_parts = []
            city = ''
            country = ''
            if locations:
                loc = locations[0]
                city = loc.get('city', '') or ''
                country_obj = loc.get('country', {}) or {}
                country = country_obj.get('name', '') if isinstance(country_obj, dict) else str(country_obj)
                location_parts = [p for p in [city, country] if p]
                # Multi-location
                for extra in locations[1:]:
                    extra_city = extra.get('city', '') or ''
                    extra_country_obj = extra.get('country', {}) or {}
                    extra_country = extra_country_obj.get('name', '') if isinstance(extra_country_obj, dict) else ''
                    extra_parts = [p for p in [extra_city, extra_country] if p]
                    location_parts.extend(extra_parts)
            location = ', '.join(dict.fromkeys(location_parts))  # deduplicate order-preserving

            # Job type / employment type
            employment_raw = item.get('employment_type', '') or item.get('employment_type_value', '')
            job_type = employment_raw.replace('_', ' ').title() if employment_raw else ''

            # Department / function
            function_obj = item.get('function', {}) or {}
            department = function_obj.get('title', '') if isinstance(function_obj, dict) else str(function_obj)
            if not department:
                department = item.get('department', '') or ''

            # Posted date
            posted_date = (
                item.get('published_date', '')
                or item.get('publish_date', '')
                or item.get('created', '')
                or ''
            )
            if posted_date:
                posted_date = posted_date[:10]  # keep YYYY-MM-DD

            # Link
            link = item.get('url', '') or item.get('apply_url', '') or ''
            if not link and job_id:
                slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')
                link = f"{self.DETAIL_BASE}/jobs/{job_id}/{slug}/"

            # Remote
            remote_flag = item.get('remote', False) or item.get('is_remote', False)
            remote = 'Yes' if remote_flag else ''

            # Brief description / excerpt from feed
            description = item.get('description', '') or item.get('summary', '') or ''
            if description:
                soup = BeautifulSoup(description, 'html.parser')
                description = soup.get_text(separator='\n', strip=True)
                words = description.split()
                if len(words) > 200:
                    description = ' '.join(words[:200]) + '...'

            job = {
                'title':               title,
                'job_id':              job_id,
                'link':                link,
                'location':            location,
                'city':                city,
                'country':             country,
                'job_type':            job_type,
                'remote':              remote,
                'posted_date':         posted_date,
                'salary':              '',
                'company':             self.COMPANY,
                'category':            department,
                'department':          department,
                'description':         description,
                'description_fetched': False,
                'skills':              [],
                'status':              'active',
                'source':              'Sectra',
                'date':                datetime.now(timezone.utc).strftime('%Y-%m-%d'),
            }
            new_jobs.append(job)
            existing_ids.add(job_id)
            print(f"  + {title[:70]} | {location[:50]}")

        self.jobs.extend(new_jobs)
        self._save_jobs()

        print(f"\n{'='*60}")
        print(f"  New jobs found    : {len(new_jobs)}")
        print(f"  Total jobs stored : {len(self.jobs)}")
        print(f"{'='*60}")

    # ------------------------------------------------------------------ #
    #  Description fetching                                                #
    # ------------------------------------------------------------------ #

    def _extract_description_from_html(self, html):
        """Extract full job description from a Jobylon job detail page."""
        soup = BeautifulSoup(html, 'html.parser')

        # Jobylon detail pages keep the body in a dedicated container
        desc_tag = (
            soup.find('div', class_=re.compile(r'job.?description|jobDescription|job-body|description', re.I))
            or soup.find('section', class_=re.compile(r'description|job', re.I))
            or soup.find('article')
            or soup.find('main')
        )
        if desc_tag:
            for tag in desc_tag.find_all(['nav', 'header', 'footer', 'script', 'style']):
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
    scraper = SectraScraper(output_file='json_files/sectra_jobs.json')
    scraper.run()
