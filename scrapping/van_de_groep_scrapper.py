import os
import json
import re
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone


class VanDeGroepScraper:
    def __init__(self, output_file='json_files/vandegroep_jobs.json'):
        self.output_file = output_file
        self.jobs = []
        self._load_existing_jobs()

        self.base_url = 'https://www.vandegroep.nl'
        self.listings_url = f'{self.base_url}/vacatures/'
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'nl-NL,nl;q=0.9,en;q=0.5',
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

    def _get_next_page_url(self, soup):
        """Extract the next page URL from <link rel='next'> in <head>."""
        link = soup.find('link', rel='next')
        if link and link.get('href'):
            return link['href']
        return None

    def parse_job_listings(self, debug=False):
        print("Fetching Van de Groep job listings...")

        existing_ids = self._get_existing_job_ids()
        total_new = 0
        total_skipped = 0
        page_url = self.listings_url
        page_num = 1

        while page_url:
            print(f"\n  Fetching page {page_num}: {page_url}")
            html = self._fetch_page(
                page_url,
                debug_file=f'vandegroep_debug_p{page_num}.html' if debug else None,
            )
            if not html:
                print("  Failed to fetch page — stopping.")
                break

            soup = BeautifulSoup(html, 'html.parser')

            # Each job card is a div with class 'e-loop-item' containing a .dark-vacature link
            items = soup.find_all('div', class_=re.compile(r'\be-loop-item\b'))

            print(f"  Found {len(items)} items on page {page_num}")

            new_count = 0
            skipped_count = 0

            for item in items:
                # Only process actual vacature loop items (skip nav/other loop containers)
                link_elem = item.find('a', class_='dark-vacature')
                if not link_elem:
                    continue

                try:
                    # Post ID from class e-loop-item-XXXXX
                    id_match = re.search(r'\be-loop-item-(\d+)\b', ' '.join(item.get('class', [])))
                    job_id = id_match.group(1) if id_match else ''

                    if not job_id:
                        continue

                    if job_id in existing_ids:
                        skipped_count += 1
                        total_skipped += 1
                        continue

                    link = link_elem.get('href', '')

                    # Title from h4.elementor-heading-title
                    title_elem = item.find('h4', class_='elementor-heading-title')
                    title = title_elem.get_text(strip=True) if title_elem else ''

                    # Tagline (subtitle/keywords) — first text-editor in the middle section
                    tagline_elem = item.find('div', class_='elementor-element-ada79fc')
                    tagline = tagline_elem.get_text(strip=True) if tagline_elem else ''

                    # City — first text-editor in the bottom row
                    city_elem = item.find('div', class_='elementor-element-cf96643')
                    city = city_elem.get_text(strip=True) if city_elem else ''

                    # Category/branche — second text-editor in the bottom row
                    category_elem = item.find('div', class_='elementor-element-91a4bc4')
                    category = category_elem.get_text(strip=True) if category_elem else ''

                    # Category slug from vacature_branche-* CSS class as fallback
                    if not category:
                        branche_match = re.search(r'\bvacature_branche-([\w-]+)', ' '.join(item.get('class', [])))
                        if branche_match:
                            category = branche_match.group(1).replace('-', ' ').title()

                    job = {
                        'title': title,
                        'job_id': job_id,
                        'job_seq_no': job_id,
                        'link': link,
                        'location': city,
                        'city': city,
                        'country': 'Netherlands',
                        'job_type': '',
                        'posted_date': '',
                        'salary': '',
                        'company': '',
                        'category': category,
                        'department': '',
                        'description': tagline,   # tagline as preview; full desc fetched later
                        'description_fetched': False,
                        'skills': [],
                        'status': 'active',
                        'source': 'van-de-groep',
                        'date': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                    }

                    self.jobs.append(job)
                    existing_ids.add(job_id)
                    new_count += 1
                    total_new += 1
                    print(f"  + {title} | {city} | {category}")

                except Exception as e:
                    print(f"  Error parsing job card: {e}")
                    continue

            self._save_jobs()
            print(f"  New: {new_count} | Skipped: {skipped_count}")

            page_url = self._get_next_page_url(soup)
            page_num += 1
            time.sleep(1)

        print(f"\n{'='*60}")
        print(f"  New jobs found    : {total_new}")
        print(f"  Duplicates skipped: {total_skipped}")
        print(f"  Total jobs stored : {len(self.jobs)}")
        print(f"{'='*60}")

    def fetch_job_descriptions(self, delay=3):
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

            html = self._fetch_page(url)
            if not html:
                print(f"  No HTML returned.")
                failed_count += 1
                time.sleep(delay)
                continue

            soup = BeautifulSoup(html, 'html.parser')

            # Description: main content area (WordPress entry-content or Elementor section)
            # Collect ALL text-editor widgets and keep only substantial ones
            text_editors = soup.find_all('div', class_='elementor-widget-text-editor')
            blocks = []
            for editor in text_editors:
                text = editor.get_text(separator='\n', strip=True)
                if len(text) >= 100:   # skip short nav/footer snippets
                    blocks.append(text)

            description = '\n\n'.join(blocks)

            # if desc_elem:
            #     description = desc_elem.get_text(separator='\n', strip=True)

            # Company: try to extract from the page title or meta
            company = ''
            og_title = soup.find('meta', property='og:title')
            if og_title:
                og_text = og_title.get('content', '')
                # Pattern: "Job Title | Company Name | Van de Groep & Olsthoorn"
                parts = [p.strip() for p in og_text.split('|')]
                if len(parts) >= 2:
                    company = parts[1] if 'Van de Groep' not in parts[1] else ''

            # Fallback: extract company from URL slug
            # e.g. /vacatures/sales-manager-creamy-creations/ → last meaningful segment
            if not company:
                slug = url.rstrip('/').split('/')[-1]
                # Remove title words to isolate company (rough heuristic)
                title_slug = re.sub(r'[^a-z0-9-]', '-', job['title'].lower())
                title_words = set(title_slug.split('-'))
                slug_words = [w for w in slug.split('-') if w not in title_words and len(w) > 2]
                if slug_words:
                    company = ' '.join(w.capitalize() for w in slug_words)

            if len(description) < 50:
                print(f"  Description too short ({len(description)} chars)")
                failed_count += 1
            else:
                job['description'] = description
                job['description_fetched'] = True
                if company:
                    job['company'] = company
                success_count += 1
                print(f"  Description: {len(description.split())} words | Company: {company}")

            if (i + 1) % 5 == 0:
                self._save_jobs()
                print(f"  Progress saved ({i+1}/{len(jobs_to_update)})")

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
    print("Starting Van de Groep & Olsthoorn scraper...")
    print("=" * 60)

    scraper = VanDeGroepScraper()
    scraper.run(fetch_descriptions=True, debug=False)

    print("\n" + "=" * 60)
    print(f"Done! Total jobs: {len(scraper.jobs)}")
    print("=" * 60)