import os
import json
import re
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone

# No ZenRows needed — the Odgers opportunities board serves static HTML

# --- Filter constants ---
EUROPE_LOCATION_IDS = ["1", "2", "3", "4", "5", "6", "7", "8", "10", "11", "12", "13", "14"]
HEALTHCARE_FUNCTION_IDS = ["15", "16"]
HEALTHTECH_INDUSTRY_IDS = ["12", "14", "16"]


class OdgersBerndtsonScraper:
    def __init__(self, output_file='json_files/odgers_brendston_jobs.json', use_filters=True):
        self.output_file = output_file
        self.use_filters = use_filters
        self.jobs = []
        self._load_existing_jobs()

        self.base_url = 'https://opportunities-board.odgersberndtson.com'
        self.search_url = f'{self.base_url}/'
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
        """Fetch a page via direct HTTP with retry + exponential backoff."""
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
                    print(f"  [debug] {elapsed:.1f}s | HTTP {response.status_code}: {response.text[:200]}")
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

    def _fetch_listings_page(self, page=1):
        """POST to opportunities board for paginated results, with optional filters."""
        t_start = time.time()
        try:
            data = {'page': str(page)}

            if self.use_filters:
                data['location_id[]'] = EUROPE_LOCATION_IDS
                data['function_id[]'] = HEALTHCARE_FUNCTION_IDS
                data['industry_id[]'] = HEALTHTECH_INDUSTRY_IDS

            response = requests.post(
                self.search_url,
                data=data,
                headers=self.headers,
                timeout=30,
            )
            elapsed = time.time() - t_start
            if response.status_code == 200:
                print(f"  [debug] page {page} fetched in {elapsed:.1f}s | {len(response.text)} chars")
                return response.text
            else:
                print(f"  [debug] {elapsed:.1f}s | HTTP {response.status_code}")
                return None
        except Exception as e:
            print(f"  Error fetching page {page}: {e}")
            return None

    def _extract_jobs_from_html(self, html, existing_ids):
        """Parse an opportunities board page and return (new_jobs, skipped_count)."""
        soup = BeautifulSoup(html, 'html.parser')
        table = soup.find('table', id='opboard_list')
        if not table:
            return [], 0

        new_jobs = []
        skipped = 0

        for row in table.find_all('tr'):
            link_elem = row.find('a', class_='opboard_link')
            if not link_elem:
                continue

            try:
                href = link_elem.get('href', '')
                path_match = re.search(r"odg_page\('([^']+)'\)", href)
                if not path_match:
                    continue
                path = path_match.group(1)

                id_match = re.search(r'/(\d+)/$', path)
                if not id_match:
                    continue
                job_id = id_match.group(1)

                if job_id in existing_ids:
                    skipped += 1
                    continue

                tds = row.find_all('td')
                title = link_elem.get_text(strip=True)
                location_raw = tds[1].get_text(strip=True) if len(tds) > 1 else ''
                reference = tds[2].get_text(strip=True) if len(tds) > 2 else ''

                country = ''
                if any(k in location_raw for k in ('UK', 'England', 'Scotland', 'Wales', 'Ireland')):
                    country = 'United Kingdom'
                elif 'Australia' in location_raw:
                    country = 'Australia'
                elif 'UAE' in location_raw:
                    country = 'UAE'
                elif 'Singapore' in location_raw:
                    country = 'Singapore'
                elif 'China' in location_raw or 'Hong Kong' in location_raw:
                    country = 'China'

                job = {
                    'title': title,
                    'job_id': job_id,
                    'job_seq_no': reference,
                    'link': self.base_url + path,
                    'location': location_raw,
                    'city': location_raw,
                    'country': country,
                    'job_type': '',
                    'posted_date': '',
                    'salary': '',
                    'company': 'Odgers Berndtson',
                    'category': '',
                    'department': '',
                    'description': '',
                    'description_fetched': False,
                    'skills': [],
                    'status': 'active',
                    'source': 'odgers-berndtson',
                    'date': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                }
                new_jobs.append(job)
                existing_ids.add(job_id)
                print(f"  + {title[:70]} | {location_raw}")

            except Exception as e:
                print(f"  Error parsing row: {e}")
                continue

        return new_jobs, skipped

    def _extract_opportunity_section(self, soup):
        """
        Extract only the 'The Opportunity' section text from a job detail page.
        Falls back to the full description div if heading is not found.
        """
        # The page has two div.col.text-break:
        #   1st: small one with language tag + job title (we skip this)
        #   2nd: inside div.row.mb-3 — contains the full description paragraphs
        desc_elem = soup.select_one('div.row.mb-3 div.col.text-break')
        if not desc_elem:
            # Fallback: pick the largest text-break div
            candidates = soup.find_all('div', class_='text-break')
            desc_elem = max(candidates, key=lambda d: len(d.get_text()), default=None)

        if not desc_elem:
            return ''

        # Walk through all paragraph/heading elements and collect text
        # from "The Opportunity" heading onwards
        paragraphs = []
        found_heading = False

        for elem in desc_elem.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'li']):
            text = elem.get_text(strip=True)
            if not text:
                continue

            if not found_heading:
                if 'the opportunity' in text.lower():
                    found_heading = True
                continue

            # Stop if we hit a new major section heading (strong-only paragraph)
            # e.g. "How to Apply", "About the Organisation", etc.
            if elem.name == 'p' and elem.find('strong') and len(text) < 60 and text.lower() != 'the opportunity':
                break

            paragraphs.append(text)

        if paragraphs:
            return '\n\n'.join(paragraphs)

        # Fallback: return full text from the description div
        return desc_elem.get_text(separator='\n', strip=True)

    def parse_job_listings(self, debug=False):
        """Fetch opportunities board pages and extract job rows."""
        filter_note = "with Europe + healthcare filters" if self.use_filters else "no filters"
        print(f"Fetching Odgers Berndtson job listings ({filter_note})...")

        existing_ids = self._get_existing_job_ids()
        total_new = 0
        total_skipped = 0

        for page in range(1, 21):
            if page == 1:
                html = self._fetch_page(
                    self.search_url,
                    debug_file='odgers_brendston_jobs.html' if debug else None,
                )
            else:
                html = self._fetch_listings_page(page)

            if not html:
                print(f"  Failed to fetch page {page}.")
                break

            new_jobs, skipped = self._extract_jobs_from_html(html, existing_ids)
            print(f"  Page {page}: {len(new_jobs)} new, {skipped} skipped")

            self.jobs.extend(new_jobs)
            total_new += len(new_jobs)
            total_skipped += skipped

            if not new_jobs and skipped == 0:
                break

        self._save_jobs()

        print(f"\n{'='*60}")
        print(f"  New jobs found    : {total_new}")
        print(f"  Duplicates skipped: {total_skipped}")
        print(f"  Total jobs stored : {len(self.jobs)}")
        print(f"{'='*60}")

    def fetch_job_descriptions(self, delay=5):
        """Fetch 'The Opportunity' section from each job's detail page."""
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

            print(f"\n[{i+1}/{len(jobs_to_update)}] {job['title'][:70]}")

            html = self._fetch_page(url)
            if not html:
                print(f"  No HTML returned — cooling down for 30s...")
                time.sleep(30)
                failed_count += 1
                continue

            soup = BeautifulSoup(html, 'html.parser')
            raw_text = self._extract_opportunity_section(soup)

            if not raw_text or len(raw_text) < 50:
                print(f"  Description too short or not found ({len(raw_text)} chars)")
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
    print("Starting Odgers Berndtson scraper...")
    print("=" * 60)

    # use_filters=True  → Europe + healthcare/healthtech only
    # use_filters=False → all jobs globally
    scraper = OdgersBerndtsonScraper(use_filters=True)
    scraper.run(fetch_descriptions=True, debug=False)

    print("\n" + "=" * 60)
    print(f"Done! Total jobs: {len(scraper.jobs)}")
    print("=" * 60)