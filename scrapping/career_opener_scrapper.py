import os
import json
import re
import time
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from datetime import datetime, timezone

load_dotenv()


class CareerOpenerScraper:
    def __init__(self, apikey=os.getenv('ZENROWS'), output_file='json_files/career_opener_jobs.json'):
        self.apikey = apikey
        self.output_file = output_file
        self.jobs = []
        self._load_existing_jobs()

        self.base_url = 'https://careeropeners.nl'
        self.search_url = f'{self.base_url}/executive-search/jobs/'

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
        """Fetch the jobs listing page and extract job cards."""
        print("Fetching Career Opener job listings...")
        html = self._fetch_page(
            self.search_url,
            wait='5000',
            debug_file='career_opener_debug.html' if debug else None,
        )
        if not html:
            print("Failed to fetch search page.")
            return

        soup = BeautifulSoup(html, 'html.parser')

        # Each job is in div.post-items__item — exclude the last CTA card (post-items__item--open)
        cards = [
            c for c in soup.find_all('div', class_='post-items__item')
            if 'post-items__item--open' not in c.get('class', [])
        ]
        print(f"Found {len(cards)} job cards")

        existing_ids = self._get_existing_job_ids()
        new_count = 0
        skipped_count = 0

        for card in cards:
            try:
                article = card.find('article', class_='post-item')
                if not article:
                    continue

                title_elem = article.find('h3', class_='post-item__title')
                if not title_elem:
                    continue
                link_elem = title_elem.find('a')
                if not link_elem:
                    continue

                title = link_elem.get_text(strip=True)
                href = link_elem.get('href', '')
                full_link = href if href.startswith('http') else self.base_url + href

                slug_match = re.search(r'/job/([^/]+)/?$', href)
                job_id = slug_match.group(1) if slug_match else href

                if job_id in existing_ids:
                    skipped_count += 1
                    continue

                meta_items = article.select('div.post-item__meta ul.meta-list li')
                category = ', '.join(li.get_text(strip=True) for li in meta_items) if meta_items else ''

                job = {
                    'title': title,
                    'job_id': job_id,
                    'job_seq_no': job_id,
                    'link': full_link,
                    'location': '',
                    'city': '',
                    'country': 'Netherlands',
                    'job_type': '',
                    'posted_date': '',
                    'company': 'Career Openers',
                    'category': category,
                    'department': '',
                    'description': '',
                    'skills': [],
                    'status': 'active',
                    'source': 'careeropeners',
                    'date': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                }

                self.jobs.append(job)
                existing_ids.add(job_id)
                new_count += 1
                print(f"  + {title} | {category} | {job_id}")

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
            if not j.get('description') or len(j.get('description', '')) < 300
        ]

        if not jobs_to_update:
            print("\nAll jobs already have descriptions.")
            return

        print(f"\nFetching descriptions for {len(jobs_to_update)} jobs...")

        success_count = 0
        failed_count = 0

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

                    desc_elem = (
                        soup.find('div', class_='wysiwyg-element') or
                        soup.find('div', class_='content--columns') or
                        soup.find('div', class_=re.compile(r'vacancy.?content|exec.?vacancy|job.?content|post.?content', re.I)) or
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
                    success_count += 1
                    print(f"  Description: {len(raw_text.split())} words")
                    break

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
        print(f"{'='*60}")

    def _save_jobs(self):
        with open(self.output_file, 'w', encoding='utf-8') as f:
            json.dump(self.jobs, f, indent=2, ensure_ascii=False)

    def run(self, fetch_descriptions=True, debug=False):
        self.parse_job_listings(debug=debug)
        if fetch_descriptions:
            self.fetch_job_descriptions()


if __name__ == '__main__':
    print("Starting Career Openers Executive Search job scraper...")
    print("=" * 60)

    scraper = CareerOpenerScraper()
    scraper.run(fetch_descriptions=True, debug=False)

    print("\n" + "=" * 60)
    print(f"Done! Total jobs: {len(scraper.jobs)}")
    print("=" * 60)