import os
import json
import re
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone


class PartnersAtWorkScraper:
    def __init__(self, output_file='json_files/partners_at_work_jobs.json'):
        self.output_file = output_file
        self.jobs = []
        self._load_existing_jobs()

        self.base_url = 'https://partnersatwork.nl'
        self.listings_url = f'{self.base_url}/en/vacancies/'
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

    def _fetch_page(self, url, debug_file=None):
        """Fetch a page with retry + exponential backoff."""
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
                    print(f"  [debug] fetched in {elapsed:.1f}s | {len(response.text)} chars")
                    if debug_file:
                        with open(debug_file, 'w', encoding='utf-8') as f:
                            f.write(response.text)
                        print(f"  Debug HTML saved to {debug_file}")
                    return response.text
                else:
                    print(f"  [debug] {elapsed:.1f}s | HTTP {response.status_code}")
                    return None
            except requests.exceptions.ConnectionError as e:
                elapsed = time.time() - t_start
                print(f"  [debug] {elapsed:.1f}s | Connection error (attempt {attempt+1}/4): {type(e).__name__}")
            except Exception as e:
                elapsed = time.time() - t_start
                print(f"  [debug] {elapsed:.1f}s | Error fetching {url}: {e}")
                return None
        print(f"  All retries exhausted for {url}")
        return None

    def parse_job_listings(self, debug=False):
        """Fetch the vacancies page and extract all job entries."""
        print("Fetching Partners at Work job listings...")

        html = self._fetch_page(
            self.listings_url,
            debug_file='partners_at_work_debug.html' if debug else None,
        )
        if not html:
            print("Failed to fetch listings page.")
            return

        soup = BeautifulSoup(html, 'html.parser')
        container = soup.find('div', class_='vacancylist')
        if not container:
            print("Could not find vacancylist container.")
            return

        existing_ids = self._get_existing_job_ids()
        new_count = 0
        skipped_count = 0

        for link_elem in container.find_all('a', href=True):
            vac_div = link_elem.find('div', id=re.compile(r'^vacancy-\d+$'))
            if not vac_div:
                continue

            try:
                # Job ID from div id="vacancy-XXXXX"
                job_id = re.search(r'vacancy-(\d+)', vac_div.get('id', '')).group(1)

                if job_id in existing_ids:
                    skipped_count += 1
                    continue

                # Title
                title_elem = vac_div.find('h3', class_='mt0')
                title = title_elem.get_text(strip=True) if title_elem else ''

                # Status — closed if title contains (GESLOTEN)
                status = 'closed' if 'GESLOTEN' in title.upper() else 'active'

                # Company name: text in div.detail before the div.vac_add
                detail_div = vac_div.find('div', class_='detail')
                company = ''
                posted_date = ''
                job_seq_no = ''

                if detail_div:
                    # Remove the vac_add child to isolate company text
                    vac_add = detail_div.find('div', class_='vac_add')
                    if vac_add:
                        # Get full text from vac_add for date + reference
                        vac_add_text = vac_add.get_text(separator=' ', strip=True)
                        # Date pattern: DD-MM-YYYY
                        date_match = re.search(r'(\d{2}-\d{2}-\d{4})', vac_add_text)
                        if date_match:
                            raw_date = date_match.group(1)
                            # Convert DD-MM-YYYY → YYYY-MM-DD
                            try:
                                posted_date = datetime.strptime(raw_date, '%d-%m-%Y').strftime('%Y-%m-%d')
                            except ValueError:
                                posted_date = raw_date
                        # Reference pattern: YYYY-XXXXX
                        ref_match = re.search(r'(\d{4}-\d{5})', vac_add_text)
                        if ref_match:
                            job_seq_no = ref_match.group(1)

                        vac_add.decompose()  # remove so we can cleanly get company name

                    # Company is now the remaining text, pattern: "CompanyName - "
                    detail_text = detail_div.get_text(strip=True)
                    company = re.sub(r'\s*-\s*$', '', detail_text).strip()

                job = {
                    'title': title,
                    'job_id': job_id,
                    'job_seq_no': job_seq_no,
                    'link': link_elem['href'],
                    'location': 'Netherlands',
                    'city': '',
                    'country': 'Netherlands',
                    'job_type': '',
                    'posted_date': posted_date,
                    'salary': '',
                    'company': company,
                    'category': '',
                    'department': '',
                    'description': '',
                    'description_fetched': False,
                    'skills': [],
                    'status': status,
                    'source': 'partners-at-work',
                    'date': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                }
                self.jobs.append(job)
                existing_ids.add(job_id)
                new_count += 1
                print(f"  + [{status}] {title[:70]} | {company}")

            except Exception as e:
                print(f"  Error parsing job card: {e}")
                continue

        self._save_jobs()

        print(f"\n{'='*60}")
        print(f"  New jobs found    : {new_count}")
        print(f"  Duplicates skipped: {skipped_count}")
        print(f"  Total jobs stored : {len(self.jobs)}")
        print(f"{'='*60}")

    def fetch_job_descriptions(self, delay=3, skip_closed=True):
        """Fetch job description from each detail page."""
        jobs_to_update = [
            j for j in self.jobs
            if not j.get('description_fetched', False)
            and (not skip_closed or j.get('status') != 'closed')
        ]

        if not jobs_to_update:
            print("\nAll active jobs already have descriptions.")
            return

        print(f"\nFetching descriptions for {len(jobs_to_update)} jobs...")

        success_count = 0
        failed_count = 0

        for i, job in enumerate(jobs_to_update):
            url = job.get('link', '')
            if not url:
                failed_count += 1
                continue

            print(f"\n[{i+1}/{len(jobs_to_update)}] {job['title'][:70]}")

            html = self._fetch_page(url)
            if not html:
                print(f"  No HTML returned — cooling down 30s...")
                time.sleep(30)
                failed_count += 1
                continue

            soup = BeautifulSoup(html, 'html.parser')

            # WordPress post content — try common selectors
            desc_elem = (
                soup.find('div', class_='entry-content') or
                soup.find('div', class_='post-content') or
                soup.find('article') or
                soup.find('div', class_=re.compile(r'content', re.I))
            )

            if not desc_elem:
                print(f"  Description element not found")
                failed_count += 1
                continue

            raw_text = desc_elem.get_text(separator='\n', strip=True)

            if len(raw_text) < 50:
                print(f"  Text too short ({len(raw_text)} chars), skipping")
                failed_count += 1
                continue

            job['description'] = raw_text
            job['description_fetched'] = True
            success_count += 1
            print(f"  Description: {len(raw_text.split())} words")

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

    def run(self, fetch_descriptions=True, debug=False):
        self.parse_job_listings(debug=debug)
        if fetch_descriptions:
            self.fetch_job_descriptions()


if __name__ == '__main__':
    print("Starting Partners at Work scraper...")
    print("=" * 60)

    scraper = PartnersAtWorkScraper()
    scraper.run(fetch_descriptions=True, debug=False)

    print("\n" + "=" * 60)
    print(f"Done! Total jobs: {len(scraper.jobs)}")
    print("=" * 60)