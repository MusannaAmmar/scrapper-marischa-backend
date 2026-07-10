import requests
import json
import os
import re
import time
from dotenv import load_dotenv
from datetime import datetime, timezone

load_dotenv()


class IndeedScraper:
    def __init__(self, apikey=os.getenv('ZENROWS'), output_file='json_files/indeed_parsed_jobs.json'):
        self.apikey = apikey
        self.output_file = output_file
        self.jobs = []
        self._load_existing_jobs()

    def _load_existing_jobs(self):
        """Load existing jobs from JSON file to avoid duplicates"""
        if os.path.exists(self.output_file):
            with open(self.output_file, 'r', encoding='utf-8') as f:
                self.jobs = json.load(f)
            print(f"Loaded {len(self.jobs)} existing jobs from {self.output_file}")
        else:
            self.jobs = []

    def _get_existing_job_ids(self):
        """Return set of already saved job IDs"""
        return {job['job_id'] for job in self.jobs if job.get('job_id')}

    def fetch_url(self, url):
        """Fetch a URL via ZenRows API"""
        params = {
            'url': url,
            'apikey': self.apikey,
            'js_render': 'true',
            'premium_proxy': 'true',
            'autoparse': 'true',
        }
        response = requests.get('https://api.zenrows.com/v1/', params=params, timeout=60)
        return response

    def _extract_job_id_from_link(self, link):
        """Extract job ID (jk param) from Indeed job link"""
        if not link:
            return None
        match = re.search(r'jk=([a-f0-9]+)', link)
        return match.group(1) if match else None

    def _extract_job_types(self, taxonomy_attributes):
        """Extract job types from taxonomyAttributes"""
        if not taxonomy_attributes:
            return None
        for attr in taxonomy_attributes:
            if attr.get('label') == 'job-types' and attr.get('attributes'):
                return ', '.join([a['label'] for a in attr['attributes']])
        return None

    def _extract_remote(self, taxonomy_attributes):
        """Extract remote work model from taxonomyAttributes"""
        if not taxonomy_attributes:
            return None
        for attr in taxonomy_attributes:
            if attr.get('label') == 'remote' and attr.get('attributes'):
                return ', '.join([a['label'] for a in attr['attributes']])
        return None

    def _extract_benefits(self, taxonomy_attributes):
        """Extract benefits from taxonomyAttributes"""
        if not taxonomy_attributes:
            return None
        for attr in taxonomy_attributes:
            if attr.get('label') == 'benefits' and attr.get('attributes'):
                return ', '.join([a['label'] for a in attr['attributes']])
        return None

    def _build_full_link(self, view_job_link):
        """Build full Indeed job link from relative path"""
        if not view_job_link:
            return None
        return f"https://www.indeed.com{view_job_link}"

    
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
        if IndeedScraper.is_job_too_old(validity_text):
            return 'expired'

        return 'active'


    def parse_job_listings(self, search_url):
        """Fetch and parse job listings from Indeed search page"""
        print(f"Fetching job listings from: {search_url}")
        response = self.fetch_url(search_url)

        if response.status_code != 200:
            print(f"⚠ HTTP {response.status_code}: {response.text[:200]}")
            return

        try:
            raw = response.json()
            if isinstance(raw, dict) and 'html' in raw:
                job_list = json.loads(raw['html'])
            elif isinstance(raw, list):
                job_list = raw
            else:
                print("✗ Unexpected response format")
                return
        except (json.JSONDecodeError, ValueError) as e:
            print(f"✗ Failed to parse response: {e}")
            return

        existing_ids = self._get_existing_job_ids()
        new_count = 0
        skipped_count = 0
        skipped_expired = 0

        for item in job_list:
            link = item.get('link', '')
            job_id = self._extract_job_id_from_link(link)

            if job_id and job_id in existing_ids:
                skipped_count += 1
                continue

            validity_text = item.get('formattedRelativeTime')

            # ---- Determine status instead of checking if too old ----
            status = self.get_job_status(validity_text)

            # ---- Skip jobs that are expired ----
            if status == 'expired':
                skipped_expired += 1
                print(f"  [EXPIRED] Skipping '{item.get('title')}' — posted: '{validity_text}'")
                continue

            taxonomy = item.get('taxonomyAttributes', [])
            salary = item.get('salarySnippet', {})

            job = {
                'job_id':          job_id,
                'title':           item.get('title'),
                'company':         item.get('company'),
                'location':        item.get('formattedLocation'),
                'link':            self._build_full_link(item.get('viewJobLink')),
                'status':          status,  # Changed from validity_text to status
                'snippet':         item.get('snippet'),
                'description':     None,
                'salary':          salary.get('text') if salary else None,
                'job_types':       self._extract_job_types(taxonomy),
                'remote':          self._extract_remote(taxonomy),
                'benefits':        self._extract_benefits(taxonomy),
                'company_rating':  item.get('companyRating'),
                'review_count':    item.get('companyReviewCount'),
                'new_job':         item.get('newJob', False),
                'source':          'indeed',
                'date':            datetime.now(timezone.utc).strftime('%Y-%m-%d'),
            }

            self.jobs.append(job)
            if job_id:
                existing_ids.add(job_id)
            new_count += 1

        self._save_jobs()
        print(f"Found {new_count} new jobs, skipped {skipped_count} duplicates, skipped {skipped_expired} expired jobs. Total: {len(self.jobs)} jobs.")

    def fetch_job_descriptions(self, delay=5):
        """Fetch full job description for each job"""
        for i, job in enumerate(self.jobs):
            link = job.get('link')

            # Skip if no link or description already fetched
            if not link or job.get('description'):
                continue

            print(f"[{i+1}/{len(self.jobs)}] Fetching description: {job.get('title')} @ {job.get('company')}")

            try:
                response = self.fetch_url(link)

                if response.status_code != 200:
                    print(f"  ⚠ HTTP {response.status_code}: {response.text[:200]}")
                    continue

                # Try to parse job description from response
                try:
                    data = response.json()
                    # If autoparse returns structured data
                    if isinstance(data, dict):
                        description = (
                            data.get('description') or
                            data.get('jobDescription') or
                            data.get('html', '')
                        )
                        job['description'] = description[:5000] if description else None
                    else:
                        job['description'] = str(data)[:5000]

                    print(f"  ✓ Description fetched ({len(job['description'] or '')} chars)")

                except (json.JSONDecodeError, ValueError):
                    # Fallback: save raw text
                    job['description'] = response.text[:5000]
                    print(f"  ✓ Raw description saved ({len(job['description'])} chars)")

            except Exception as e:
                print(f"  ✗ Error: {e}")

            self._save_jobs()
            time.sleep(delay)

        print(f"\nDone. Updated {self.output_file}")

    def _save_jobs(self):
        """Save jobs to JSON file"""
        with open(self.output_file, 'w', encoding='utf-8') as f:
            json.dump(self.jobs, f, indent=2, ensure_ascii=False)

    def run(self, search_url):
        """Run full scraping pipeline"""
        self.parse_job_listings(search_url)
        self.fetch_job_descriptions()


if __name__ == '__main__':
    search_url = 'https://www.indeed.com/jobs?q=chief+innovation+officer&l=Netherlands'
    scraper = IndeedScraper()
    scraper.run(search_url)