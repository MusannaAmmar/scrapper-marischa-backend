import os
import json
import re
import time
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from datetime import datetime, timezone

load_dotenv()


class ThemaGroupScraper:
    def __init__(self, apikey=os.getenv('ZENROWS'), output_file='json_files/thema_group_jobs.json'):
        self.apikey = apikey
        self.output_file = output_file
        self.jobs = []
        self.new_job_ids = set()
        self._load_existing_jobs()

        self.base_url = 'https://www.themagroup.eu'
        self.search_url = (
            f'{self.base_url}/nl/jobs/'
            '?title=&location=62992'
            '&category%5B%5D=10676'
            '&category%5B%5D=10678'
            '&category%5B%5D=10773'
            '&category%5B%5D=12233'
            '&category%5B%5D=10677'
        )

    def _load_existing_jobs(self):
        if os.path.exists(self.output_file):
            with open(self.output_file, 'r', encoding='utf-8') as f:
                self.jobs = json.load(f)
            print(f"Loaded {len(self.jobs)} existing jobs from {self.output_file}")
        else:
            self.jobs = []

    def _get_existing_job_ids(self):
        return {job['job_id'] for job in self.jobs if job.get('job_id')}

    def _fetch_page(self, url, wait='5000', debug_file=None):
        """Fetch a page via ZenRows with JS rendering."""
        params = {
            'url': url,
            'apikey': self.apikey,
            'js_render': 'true',
            'wait': wait,
            'premium_proxy': 'true',
        }
        try:
            response = requests.get('https://api.zenrows.com/v1/', params=params, timeout=90)
            if response.status_code == 200:
                if debug_file:
                    with open(debug_file, 'w', encoding='utf-8') as f:
                        f.write(response.text)
                    print(f"  Debug HTML saved to {debug_file}")
                return response.text
            else:
                print(f"  ZenRows returned status {response.status_code}: {response.text[:200]}")
                return None
        except Exception as e:
            print(f"  Error fetching {url}: {e}")
            return None

    def parse_job_listings(self, debug=False):
        """Fetch the search page and extract job cards."""
        print("Fetching Thema Group job listings...")
        html = self._fetch_page(
            self.search_url,
            wait='5000',
            debug_file='thema_group_debug.html' if debug else None,
        )
        if not html:
            print("Failed to fetch search page.")
            return

        soup = BeautifulSoup(html, 'html.parser')
        cards = soup.find_all('div', class_='jobs-md-item')
        print(f"Found {len(cards)} job cards")

        existing_ids = self._get_existing_job_ids()
        self.new_job_ids = set()
        new_count = 0
        skipped_count = 0

        for card in cards:
            try:
                title_elem = card.find('h3')
                title = title_elem.get_text(strip=True) if title_elem else 'N/A'

                loc_elem = card.find('span', class_='location-txt')
                location_raw = loc_elem.get_text(strip=True) if loc_elem else ''
                location = re.sub(r'^Locatie\s*:\s*', '', location_raw, flags=re.IGNORECASE).strip()

                link_elem = card.find('a', href=re.compile(r'jid='))
                if not link_elem:
                    continue
                href = link_elem.get('href', '')
                full_link = href if href.startswith('http') else self.base_url + href

                jid_match = re.search(r'jid=([^&]+)', href)
                job_id = jid_match.group(1) if jid_match else href

                if job_id in existing_ids:
                    skipped_count += 1
                    continue

                job = {
                    'title': title,
                    'job_id': job_id,
                    'job_seq_no': job_id,
                    'link': full_link,
                    'location': location,
                    'city': location,
                    'country': 'Netherlands',
                    'job_type': '',
                    'posted_date': '',
                    'company': 'Thema Group Life Sciences',
                    'category': '',
                    'department': '',
                    'description': '',
                    'skills': [],
                    'status': 'active',
                    'source': 'thema group life sciences',
                    'date': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                }

                self.jobs.append(job)
                existing_ids.add(job_id)
                self.new_job_ids.add(job_id)
                new_count += 1
                print(f"  + {title} | {location} | {job_id}")

            except Exception as e:
                print(f"  Error parsing card: {e}")
                continue

        self._save_jobs()
        print(f"\n{'='*60}")
        print(f"  New jobs found    : {new_count}")
        print(f"  Duplicates skipped: {skipped_count}")
        print(f"  Total jobs stored : {len(self.jobs)}")
        print(f"{'='*60}")

    def fetch_job_descriptions(self, delay=8):
        """Fetch full description for each job that lacks one."""
        jobs_to_update = [
            j for j in self.jobs
            if str(j.get('job_id') or '') in self.new_job_ids
            and (not j.get('description') or len(j.get('description', '')) < 300)
        ]

        if not jobs_to_update:
            print("\nAll jobs already have descriptions.")
            return

        print(f"\nFetching descriptions for {len(jobs_to_update)} jobs...")

        success_count = 0
        failed_count = 0
        total_words_saved = 0

        for i, job in enumerate(jobs_to_update):
            link = job.get('link')
            if not link:
                failed_count += 1
                continue

            print(f"\n[{i+1}/{len(jobs_to_update)}] {job['title']}")

            for attempt in range(2):
                try:
                    wait_time = '8000' if attempt == 0 else '12000'
                    html = self._fetch_page(link, wait=wait_time)
                    if not html:
                        print(f"  Attempt {attempt+1}: no HTML returned")
                        time.sleep(3)
                        continue

                    soup = BeautifulSoup(html, 'html.parser')

                    # Thema Group detail page: job content is inside .job-detail-description
                    # or falls back to the main content area
                    desc_elem = (
                        soup.find('div', class_=re.compile(r'job.?detail|job.?desc|vacancy.?detail|jd.?content', re.I)) or
                        soup.find('div', class_='entry-content') or
                        soup.find('article') or
                        soup.find('main')
                    )

                    if not desc_elem:
                        print(f"  Attempt {attempt+1}: description element not found")
                        time.sleep(3)
                        continue

                    raw_text = desc_elem.get_text(separator='\n', strip=True)

                    if raw_text and len(raw_text) >= 120:
                        previous = str(job.get('description') or '')
                        # Keep the richer version to avoid regressions on transient pages.
                        if not previous or len(raw_text) >= len(previous):
                            job['description'] = raw_text
                        success_count += 1
                        total_words_saved += len(raw_text.split())
                        break

                    print(f"  Attempt {attempt+1}: description too short")
                    time.sleep(3)

                except Exception as e:
                    print(f"  Attempt {attempt+1} error: {e}")
                    time.sleep(3)
            else:
                failed_count += 1

            if (i + 1) % 3 == 0:
                self._save_jobs()
                print(f"  Saved progress ({success_count}/{i+1} successful)")

            time.sleep(delay)

        self._save_jobs()

        print(f"\n{'='*60}")
        print(f"Description Fetching Complete!")
        print(f"  Successful : {success_count}")
        print(f"  Failed     : {failed_count}")
        if total_words_saved > 0:
            print(f"  Words saved: ~{total_words_saved:,} ({int(total_words_saved * 0.00075)} tokens)")
        print(f"{'='*60}")


    def _save_jobs(self):
        with open(self.output_file, 'w', encoding='utf-8') as f:
            json.dump(self.jobs, f, indent=2, ensure_ascii=False)

    def run(self, fetch_descriptions=True, debug=False):
        self.parse_job_listings(debug=debug)
        if fetch_descriptions:
            self.fetch_job_descriptions()


if __name__ == '__main__':
    print("Starting Thema Group Life Sciences job scraper...")
    print("=" * 60)

    scraper = ThemaGroupScraper()
    scraper.run(fetch_descriptions=True, debug=False)

    print("\n" + "=" * 60)
    print(f"Done! Total jobs: {len(scraper.jobs)}")
    print("=" * 60)