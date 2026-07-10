import os
import json
import re
import time
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from datetime import datetime, timezone

load_dotenv()


class LyncwiseScraper:
    def __init__(self, apikey=os.getenv('ZENROWS'), output_file='json_files/lyncwise_jobs.json'):
        self.apikey = apikey
        self.output_file = output_file
        self.jobs = []
        self._load_existing_jobs()

        self.base_url = 'https://lyncwise.nl'
        self.search_url = f'{self.base_url}/open-vacatures/'

    def _load_existing_jobs(self):
        if os.path.exists(self.output_file):
            with open(self.output_file, 'r', encoding='utf-8') as f:
                self.jobs = json.load(f)
            print(f"Loaded {len(self.jobs)} existing jobs from {self.output_file}")
        else:
            self.jobs = []

    def _get_existing_job_ids(self):
        return {job['job_id'] for job in self.jobs if job.get('job_id')}

    def _fetch_page(self, url, js_render=False, wait='3000', debug_file=None):
        """Fetch a page via ZenRows."""
        params = {
            'url': url,
            'apikey': self.apikey,
            'premium_proxy': 'true',
        }
        if js_render:
            params['js_render'] = 'true'
            params['wait'] = wait

        t_start = time.time()
        try:
            response = requests.get('https://api.zenrows.com/v1/', params=params, timeout=120)
            elapsed = time.time() - t_start
            if response.status_code == 200:
                print(f"  [debug] fetched in {elapsed:.1f}s | {len(response.text)} chars")
                if debug_file:
                    with open(debug_file, 'w', encoding='utf-8') as f:
                        f.write(response.text)
                    print(f"  Debug HTML saved to {debug_file}")
                return response.text
            else:
                print(f"  [debug] {elapsed:.1f}s | ZenRows status {response.status_code}: {response.text[:200]}")
                return None
        except Exception as e:
            elapsed = time.time() - t_start
            print(f"  [debug] {elapsed:.1f}s | Error fetching {url}: {e}")
            return None

    def _extract_icon_text(self, icon_row, icon_class):
        """Extract text from a span.wordwrap that contains an icon with the given class."""
        for span in icon_row.find_all('span', class_='wordwrap'):
            icon = span.find('i', class_=icon_class)
            if icon:
                # Remove the icon tag, return remaining text
                icon.decompose()
                return span.get_text(strip=True)
        return ''

    def parse_job_listings(self, debug=False):
        """Fetch the listings page and extract all job cards."""
        print("Fetching Lyncwise job listings...")

        existing_ids = self._get_existing_job_ids()
        total_new = 0
        total_skipped = 0

        html = self._fetch_page(
            self.search_url,
            js_render=False,
            debug_file='lyncwise_jobs.html' if debug else None,
        )
        if not html:
            print("  Failed to fetch listings page.")
            return

        soup = BeautifulSoup(html, 'html.parser')
        cards = soup.find_all('div', class_=lambda c: c and 'vacature' in c.split() and 'vc_col-sm-12' in c.split())
        print(f"  Found {len(cards)} job cards")

        for card in cards:
            try:
                link_elem = card.find('a', class_='vacature-link')
                if not link_elem:
                    continue
                full_link = link_elem.get('href', '').strip()

                # Use the URL slug as the stable job_id
                slug = full_link.rstrip('/').split('/')[-1]
                if not slug:
                    continue

                if slug in existing_ids:
                    total_skipped += 1
                    continue

                title_elem = card.find('h3')
                title = title_elem.get_text(strip=True) if title_elem else slug

                desc_elem = card.find('p', class_='vacature-text')
                description = desc_elem.get_text(strip=True) if desc_elem else ''

                icon_row = card.find('div', class_='vacature-icon-row')
                job_type = ''
                category = ''
                location = ''
                if icon_row:
                    job_type = self._extract_icon_text(icon_row, 'fa-briefcase')
                    category = self._extract_icon_text(icon_row, 'fa-file-invoice')
                    location = self._extract_icon_text(icon_row, 'fa-map-marker-alt')

                job = {
                    'title': title,
                    'job_id': slug,
                    'job_seq_no': slug,
                    'link': full_link,
                    'location': location,
                    'city': location,
                    'country': 'Netherlands',
                    'job_type': job_type,
                    'posted_date': '',
                    'salary': '',
                    'company': 'Lyncwise',
                    'category': category,
                    'department': '',
                    'description': description,
                    'description_fetched': False,
                    'skills': [],
                    'status': 'active',
                    'source': 'lyncwise',
                    'date': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                }

                self.jobs.append(job)
                existing_ids.add(slug)
                total_new += 1
                print(f"  + {title} | {location}")

            except Exception as e:
                print(f"  Error parsing card: {e}")
                continue

        self._save_jobs()

        print(f"\n{'='*60}")
        print(f"  New jobs found    : {total_new}")
        print(f"  Duplicates skipped: {total_skipped}")
        print(f"  Total jobs stored : {len(self.jobs)}")
        print(f"{'='*60}")

    def fetch_job_descriptions(self, delay=5):
        """Fetch full description for each job that only has a snippet."""
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

            print(f"\n[{i+1}/{len(jobs_to_update)}] {job['title']}")

            for attempt in range(2):
                html = self._fetch_page(url, js_render=False)
                if not html:
                    print(f"  Attempt {attempt+1}: no HTML returned")
                    time.sleep(3)
                    continue

                soup = BeautifulSoup(html, 'html.parser')

                # Try common WordPress single-post content containers
                desc_elem = (
                    soup.find('div', class_='justify-text') or
                    soup.find('div', class_=re.compile(r'entry.?content|post.?content|vacature.?content|job.?description', re.I)) or
                    soup.find('div', class_='post_text') or
                    soup.find('div', class_='entry-content') or
                    soup.find('article') or
                    soup.find('main')
                )

                if not desc_elem:
                    print(f"  Attempt {attempt+1}: description element not found")
                    time.sleep(3)
                    continue

                raw_text = desc_elem.get_text(separator='\n', strip=True)

                if len(raw_text) < 100:
                    print(f"  Attempt {attempt+1}: text too short ({len(raw_text)} chars), retrying...")
                    time.sleep(3)
                    continue

                job['description'] = raw_text
                job['description_fetched'] = True
                success_count += 1
                print(f"  Description: {len(raw_text.split())} words")
                break
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
        print(f"{'='*60}")

    def _save_jobs(self):
        with open(self.output_file, 'w', encoding='utf-8') as f:
            json.dump(self.jobs, f, indent=2, ensure_ascii=False)

    def run(self, fetch_descriptions=True, debug=False):
        self.parse_job_listings(debug=debug)
        if fetch_descriptions:
            self.fetch_job_descriptions()


if __name__ == '__main__':
    print("Starting Lyncwise scraper...")
    print("=" * 60)

    scraper = LyncwiseScraper()
    scraper.run(fetch_descriptions=True, debug=False)

    print("\n" + "=" * 60)
    print(f"Done! Total jobs: {len(scraper.jobs)}")
    print("=" * 60)
