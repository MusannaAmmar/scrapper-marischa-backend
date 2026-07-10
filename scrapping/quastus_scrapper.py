import os
import json
import re
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()


class QuaestusScraper:
    def __init__(self, apikey=os.getenv('ZENROWS'), output_file='json_files/quaestus_jobs.json'):
        self.apikey = apikey
        self.output_file = output_file
        self.jobs = []
        self._load_existing_jobs()

        self.base_url = 'https://www.quaestus.eu'
        self.listings_url = f'{self.base_url}/jobs'
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        }

    def _load_existing_jobs(self):
        if os.path.exists(self.output_file):
            with open(self.output_file, 'r', encoding='utf-8') as f:
                self.jobs = json.load(f)
            print(f"Loaded {len(self.jobs)} existing jobs from {self.output_file}")
        else:
            self.jobs = []

    def _get_existing_job_ids(self):
        return {job['job_id'] for job in self.jobs if job.get('job_id')}

    def _fetch_page(self, url):
        """Fetch a plain HTML page with retry + exponential backoff."""
        for attempt in range(4):
            wait = 2 ** attempt
            if attempt > 0:
                print(f"  Retry {attempt}/3 — waiting {wait}s...")
                time.sleep(wait)
            t_start = time.time()
            try:
                response = requests.get(url, headers=self.headers, timeout=30)
                elapsed = time.time() - t_start
                if response.status_code == 200:
                    print(f"  [fetch] {elapsed:.1f}s | {len(response.text)} chars")
                    return response.text
                else:
                    print(f"  [fetch] {elapsed:.1f}s | HTTP {response.status_code}")
                    return None
            except requests.exceptions.ConnectionError as e:
                elapsed = time.time() - t_start
                print(f"  [fetch] {elapsed:.1f}s | Connection error (attempt {attempt+1}/4): {type(e).__name__}")
            except Exception as e:
                elapsed = time.time() - t_start
                print(f"  [fetch] {elapsed:.1f}s | Error: {e}")
                return None
        print(f"  All retries exhausted for {url}")
        return None

    def _fetch_with_zenrows(self, url, wait=8000):
        """Fetch a JS-rendered page via ZenRows."""
        params = {
            'url': url,
            'apikey': self.apikey,
            'js_render': 'true',
            'wait': wait,
            'premium_proxy': 'true',
        }
        t_start = time.time()
        try:
            response = requests.get('https://api.zenrows.com/v1/', params=params, timeout=120)
            elapsed = time.time() - t_start
            if response.status_code == 200:
                print(f"  [zenrows] {elapsed:.1f}s | {len(response.text)} chars")
                return response.text
            else:
                print(f"  [zenrows] {elapsed:.1f}s | HTTP {response.status_code}: {response.text[:200]}")
                return None
        except Exception as e:
            elapsed = time.time() - t_start
            print(f"  [zenrows] {elapsed:.1f}s | Error: {e}")
            return None

    def _extract_description(self, html):
        """Extract job description text from a rendered Workable detail page."""
        soup = BeautifulSoup(html, 'html.parser')

        candidates = [
            soup.find(attrs={'data-ui': 'job-description'}),
            soup.find(attrs={'data-ui': 'job-requirements'}),
            soup.find('div', class_='job-description'),
            soup.find('main'),
            soup.find('article'),
        ]

        parts = []
        for container in candidates[:2]:
            if container:
                text = container.get_text(separator='\n', strip=True)
                if len(text) > 50:
                    parts.append(text)

        if parts:
            return '\n\n'.join(parts)

        for container in candidates[2:]:
            if container:
                text = container.get_text(separator='\n', strip=True)
                if len(text) > 100:
                    return text

        return ''

    def _extract_job_id(self, url):
        """Extract Workable job shortcode from URL."""
        match = re.search(r'/j/([A-Z0-9]+)/?', url, re.IGNORECASE)
        return match.group(1).upper() if match else None

    def parse_job_listings(self, include_closed=False):
        """Fetch the Quaestus jobs page and extract all open (and optionally closed) jobs."""
        print("Fetching Quaestus job listings...")

        existing_ids = self._get_existing_job_ids()
        new_count = 0
        skipped_count = 0
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')

        pages_to_fetch = [
            (f'{self.listings_url}?status=Open&used_filters=true', 'active'),
        ]
        if include_closed:
            pages_to_fetch.append(
                (f'{self.listings_url}?status=Closed&used_filters=true', 'closed')
            )

        for url, status in pages_to_fetch:
            print(f"\n  Fetching {status} jobs...")
            html = self._fetch_page(url)
            if not html:
                print(f"  Failed to fetch {status} jobs page.")
                continue

            soup = BeautifulSoup(html, 'html.parser')
            cards = soup.find_all('div', class_='jobs-card')
            print(f"  Found {len(cards)} job cards on {status} page")

            for card in cards:
                try:
                    link_elem = card.find('a', class_='jobs-card__image')
                    if not link_elem:
                        continue
                    job_link = link_elem.get('href', '').strip()

                    job_id = self._extract_job_id(job_link)
                    if not job_id:
                        print(f"  Could not extract job ID from: {job_link}")
                        continue

                    if job_id in existing_ids:
                        skipped_count += 1
                        continue

                    content_elem = card.find('a', class_='content')
                    if not content_elem:
                        continue

                    title_elem = content_elem.find('h2', class_='h2')
                    title = title_elem.get_text(strip=True) if title_elem else ''

                    h4_elem = content_elem.find('h4')
                    category = ''
                    location = ''
                    if h4_elem:
                        loc_span = h4_elem.find('span')
                        if loc_span:
                            location = loc_span.get_text(strip=True)
                            loc_span.decompose()
                        h4_text = h4_elem.get_text(separator=' ', strip=True)
                        category = re.split(r'\s*\|\s*', h4_text)[0].strip()

                    company_elem = content_elem.find('p')
                    company = company_elem.get_text(strip=True) if company_elem else 'Quaestus'

                    country = 'Netherlands'
                    city = re.sub(r',\s*Nederland$', '', location).strip()

                    job = {
                        'title': title,
                        'job_id': job_id,
                        'job_seq_no': '',
                        'link': job_link,
                        'location': location,
                        'city': city,
                        'country': country,
                        'job_type': '',
                        'posted_date': '',
                        'salary': '',
                        'company': company,
                        'category': category,
                        'department': category,
                        'description': '',
                        'description_fetched': False,
                        'skills': [],
                        'status': status,
                        'source': 'quaestus',
                        'date': today,
                    }
                    self.jobs.append(job)
                    existing_ids.add(job_id)
                    new_count += 1
                    print(f"  + [{status}] {title[:65]} | {company} | {city}")

                except Exception as e:
                    print(f"  Error parsing job card: {e}")
                    continue

        self._save_jobs()

        print(f"\n{'='*60}")
        print(f"  New jobs found    : {new_count}")
        print(f"  Duplicates skipped: {skipped_count}")
        print(f"  Total jobs stored : {len(self.jobs)}")
        print(f"{'='*60}")

    def fetch_job_descriptions(self, delay=5, skip_closed=True):
        """Load each job's detail link from JSON and scrape the description via ZenRows."""
        jobs_to_update = [
            j for j in self.jobs
            if not j.get('description_fetched', False)
            and (not skip_closed or j.get('status') != 'closed')
        ]

        if not jobs_to_update:
            print("\nAll active jobs already have descriptions.")
            return

        print(f"\nFetching descriptions for {len(jobs_to_update)} jobs via ZenRows...")

        success_count = 0
        failed_count = 0

        for i, job in enumerate(jobs_to_update):
            print(f"\n[{i+1}/{len(jobs_to_update)}] {job['title'][:70]}")
            print(f"  Link: {job['link']}")

            html = self._fetch_with_zenrows(job['link'])

            if not html:
                print(f"  Failed to fetch detail page")
                failed_count += 1
                time.sleep(delay)
                continue

            description = self._extract_description(html)

            if not description:
                print(f"  Could not extract description")
                failed_count += 1
                time.sleep(delay)
                continue

            if len(description) < 50:
                print(f"  Description too short ({len(description)} chars), skipping")
                failed_count += 1
                time.sleep(delay)
                continue

            job['description'] = description
            job['description_fetched'] = True
            success_count += 1
            print(f"  Description: {len(description.split())} words")

            if (i + 1) % 5 == 0:
                self._save_jobs()
                print(f"  Saved progress ({success_count}/{i+1} successful)")

            time.sleep(delay)

        self._save_jobs()

        print(f"\n{'='*60}")
        print(f"Description Fetching Complete!")
        print(f"  Successful : {success_count}")
        print(f"  Failed     : {failed_count}")
        print(f"{'='*60}")

    def _save_jobs(self):
        with open(self.output_file, 'w', encoding='utf-8') as f:
            json.dump(self.jobs, f, indent=2, ensure_ascii=False)

    def run(self, fetch_descriptions=True, include_closed=False):
        self.parse_job_listings(include_closed=include_closed)
        if fetch_descriptions:
            self.fetch_job_descriptions()


if __name__ == '__main__':
    print("Starting Quaestus scraper...")
    print("=" * 60)

    scraper = QuaestusScraper()
    scraper.run(fetch_descriptions=True, include_closed=False)

    print("\n" + "=" * 60)
    print(f"Done! Total jobs: {len(scraper.jobs)}")
    print("=" * 60)