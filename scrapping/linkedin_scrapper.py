
# pip install requests beautifulsoup4
import requests
import json
import re
import time
import os
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from datetime import datetime, timezone


load_dotenv()



class LinkedinScraper:
    def __init__(self, apikey=os.getenv('ZENROWS'), output_file='json_files/parsed_jobs_file.json'):
        self.apikey = apikey
        self.output_file = output_file
        self.jobs = []
        self._load_existing_jobs()

    def _load_existing_jobs(self):
        if os.path.exists(self.output_file):
            with open(self.output_file, 'r', encoding='utf-8') as f:
                self.jobs = json.load(f)
            print(f"Loaded {len(self.jobs)} existing jobs from {self.output_file}")
        else:
            self.jobs = []

    def _get_existing_job_ids(self):
        return {job['job_id'] for job in self.jobs if job.get('job_id')}

    def fetch_html(self, url):
        params = {
            'url': url,
            'apikey': self.apikey,
            'mode': 'auto',
            'premium_proxy': 'true',
            'js_render': 'true',
        }
        response = requests.get('https://api.zenrows.com/v1/', params=params, timeout=60)
        return response

    # ---- Helper: Check if job is older than 2 weeks based on validity_text ----
    @staticmethod
    def is_job_too_old(validity_text):
        """
        Returns True if the job is older than 2 weeks.
        Handles: '3 weeks ago', '1 month ago', '2 months ago', '10 days ago', etc.
        """
        if not validity_text:
            return False

        text = validity_text.lower().strip()

        try:
            if 'month' in text:
                return True  # Any month+ is > 2 weeks

            if 'week' in text:
                match = re.search(r'(\d+)\+?\s*week', text)
                if match:
                    return int(match.group(1)) >= 2

            if 'day' in text:
                match = re.search(r'(\d+)\+?\s*day', text)
                if match:
                    return int(match.group(1)) >= 14

        except Exception:
            pass

        return False

    # ---- Helper: Determine job status based on validity_text ----
    @staticmethod
    def get_job_status(validity_text):
        """
        Returns 'expired' if job is older than 2 weeks, otherwise 'active'.
        Also checks for explicit 'closed' or 'expired' keywords.
        """
        if not validity_text:
            return 'active'

        text = validity_text.lower().strip()

        # Check for explicit expired/closed keywords
        if 'closed' in text or 'expired' in text or 'no longer accepting' in text:
            return 'expired'

        # Check if job is too old
        if LinkedinScraper.is_job_too_old(validity_text):
            return 'expired'

        return 'active'


    def parse_job_listings(self, search_url):
        print(f"Fetching job listings from: {search_url}")
        response = self.fetch_html(search_url)

        if response.status_code != 200:
            print(f"⚠ HTTP {response.status_code}: {response.text[:200]}")
            return

        soup = BeautifulSoup(response.text, 'html.parser')
        job_cards = soup.find_all(class_='job-search-card')

        existing_ids = self._get_existing_job_ids()
        new_count = 0
        skipped_count = 0
        skipped_old = 0

        for card in job_cards:
            # Extract job ID first to check for duplicates
            job_id = None
            entity_urn = card.get('data-entity-urn', '')
            match = re.search(r'urn:li:jobPosting:(\d+)', entity_urn)
            if match:
                job_id = match.group(1)

            if job_id and job_id in existing_ids:
                skipped_count += 1
                continue

            title_tag = card.find('h3', class_='base-search-card__title')
            title = title_tag.get_text(strip=True) if title_tag else None

            link = None
            if card.name == 'a' and card.get('href'):
                link = card['href']
            else:
                link_tag = card.find('a', class_='base-card__full-link')
                if link_tag:
                    link = link_tag['href']

            location_tag = card.find('span', class_='job-search-card__location')
            location = location_tag.get_text(strip=True) if location_tag else None

            subtitle_tag = card.find('h4', class_='base-search-card__subtitle')
            company = subtitle_tag.get_text(strip=True) if subtitle_tag else None

            time_tag = card.find('time')
            validity = time_tag.get('datetime') if time_tag else None
            validity_text = time_tag.get_text(strip=True) if time_tag else None

            # ---- Determine status instead of checking if too old ----
            status = self.get_job_status(validity_text)

            # ---- Skip jobs older than 2 weeks (expired) ----
            if status == 'expired':
                skipped_old += 1
                print(f"  [EXPIRED] Skipping '{title}' — posted: '{validity_text}'")
                continue

            self.jobs.append({
                'title': title,
                'link': link,
                'job_id': job_id,
                'location': location,
                'company': company,
                'description': None,
                'validity': validity,
                'status': status,  # Changed from validity_text to status
                'date': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
            })

            existing_ids.add(job_id)
            new_count += 1

        self._save_jobs()
        print(f"Found {new_count} new jobs, skipped {skipped_count} duplicates, skipped {skipped_old} expired jobs. Total: {len(self.jobs)} jobs.")

    def fetch_job_descriptions(self, delay=5):
        for i, job in enumerate(self.jobs):
            link = job.get('link')
            if not link or job.get('description'):
                continue

            url = link.replace('https://nl.linkedin.com/', 'https://www.linkedin.com/')

            print(f"[{i+1}/{len(self.jobs)}] Fetching: {job['title']}")

            try:
                response = self.fetch_html(url)

                if response.status_code != 200:
                    print(f"  ⚠ HTTP {response.status_code}: {response.text[:200]}")
                    continue

                soup = BeautifulSoup(response.text, 'html.parser')
                desc_div = soup.find('div', class_='show-more-less-html__markup')

                if desc_div:
                    job['description'] = desc_div.get_text(separator='\n', strip=True)
                    print(f"  ✓ Description fetched ({len(job['description'])} chars)")
                else:
                    print(f"  ✗ Description div not found")

            except Exception as e:
                print(f"  ✗ Error: {e}")

            self._save_jobs()
            time.sleep(delay)

        print(f"\nDone. Updated {self.output_file}")

    def _save_jobs(self):
        with open(self.output_file, 'w', encoding='utf-8') as f:
            json.dump(self.jobs, f, indent=2, ensure_ascii=False)

    def run(self, search_url):
        self.parse_job_listings(search_url)
        self.fetch_job_descriptions()


if __name__ == '__main__':
    search_url = 'https://www.linkedin.com/jobs/search/?keywords=Managing+Director&geoId=102890719&trk=d_flagship3_salary_explorer'
    scraper = LinkedinScraper()
    scraper.run(search_url)