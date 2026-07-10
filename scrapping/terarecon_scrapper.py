# pip install requests beautifulsoup4
import requests
import json
import time
import os
import re
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from datetime import datetime, timezone

load_dotenv()


class TerareconScraper:
    def __init__(self, apikey=os.getenv('ZENROWS'), output_file='json_files/terarecon_jobs.json'):
        self.apikey = apikey
        self.output_file = output_file
        self.jobs = []
        self._load_existing_jobs()
        
        # Updated URLs for Concert AI careers site
        self.base_url = 'https://careers.concertai.com'
        self.search_url = f'{self.base_url}/us/en/search-results'
        
    def _load_existing_jobs(self):
        """Load existing jobs from JSON file to avoid duplicates"""
        if os.path.exists(self.output_file):
            with open(self.output_file, 'r', encoding='utf-8') as f:
                self.jobs = json.load(f)
            print(f"Loaded {len(self.jobs)} existing jobs from {self.output_file}")
        else:
            self.jobs = []

    def _get_existing_job_ids(self):
        """Get set of existing job IDs"""
        return {job['job_id'] for job in self.jobs if job.get('job_id')}

    def fetch_page_with_zenrows(self, url, wait_time='5000'):
        """Fetch a page using ZenRows with JS rendering"""
        params = {
            'url': url,
            'apikey': self.apikey,
            'js_render': 'true',
            'wait': wait_time,
        }
        
        try:
            response = requests.get('https://api.zenrows.com/v1/', params=params, timeout=60)
            
            if response.status_code == 200:
                return response.text
            else:
                print(f"⚠ ZenRows returned status {response.status_code}")
                return None
                
        except Exception as e:
            print(f"Error fetching page: {e}")
            return None

    def parse_job_listings(self):
        """Parse job listings from HTML using ZenRows"""
        print(f"Fetching jobs from TeraRecon/Concert AI careers page...")
        
        existing_ids = self._get_existing_job_ids()
        new_count = 0
        skipped_count = 0
        
        print(f"\nFetching search results page...")
        
        # Fetch the search results page
        html = self.fetch_page_with_zenrows(self.search_url, wait_time='8000')
        
        if not html:
            print("Failed to fetch search results page")
            return
        
        soup = BeautifulSoup(html, 'html.parser')
        
        # Find job listings - Phenom People typically uses these selectors
        job_cards = soup.find_all('li', class_='jobs-list-item') or \
                   soup.find_all('div', class_='job-card') or \
                   soup.find_all('div', attrs={'data-ph-at-id': 'job-item'}) or \
                   soup.find_all('li', attrs={'data-job-id': True})
        
        if not job_cards:
            # Try alternative selectors
            job_cards = soup.select('[data-ph-at-id*="job"]') or \
                       soup.select('.ph-job-search-result-item') or \
                       soup.select('[class*="job"][class*="item"]')
        
        print(f"Found {len(job_cards)} job listings")
        
        if len(job_cards) == 0:
            print("\n⚠ No job cards found. The page structure might have changed.")
            print("Saving HTML for debugging...")
            with open('terarecon_debug.html', 'w', encoding='utf-8') as f:
                f.write(html)
            print("HTML saved to terarecon_debug.html for inspection")
            return
        
        for job_card in job_cards:
            try:
                # Extract job link and ID
                job_link_tag = job_card.find('a', href=True)
                if not job_link_tag:
                    continue
                
                job_href = job_link_tag.get('href', '')
                if job_href.startswith('/'):
                    job_url = f"{self.base_url}{job_href}"
                elif job_href.startswith('http'):
                    job_url = job_href
                else:
                    job_url = f"{self.base_url}/us/en/{job_href}"
                
                # Extract job ID from URL (e.g., P-100769 from /job/P-100769/)
                job_id = None
                if job_href:
                    # Match pattern like /job/P-100769/ or /job/P-100769
                    match = re.search(r'/job/([A-Z]+-\d+)', job_href) or \
                           re.search(r'/job/([^/]+)/', job_href)
                    if match:
                        job_id = match.group(1)
                
                if not job_id:
                    # Fallback: use last meaningful part of URL
                    parts = [p for p in job_href.split('/') if p and not p.startswith('?')]
                    if len(parts) >= 2:
                        # Get the part after 'job' keyword
                        try:
                            job_idx = parts.index('job')
                            if job_idx + 1 < len(parts):
                                job_id = parts[job_idx + 1]
                        except ValueError:
                            job_id = parts[-1] if parts else None
                
                if not job_id:
                    print(f"  ⚠ Could not extract job ID from {job_href}")
                    continue
                
                # Skip if already exists
                if job_id in existing_ids:
                    skipped_count += 1
                    print(f"  [SKIP] {job_id} (duplicate)")
                    continue
                
                # Extract title
                title_tag = job_card.find('h1', class_='job-title') or \
                           job_card.find('h2', class_='job-title') or \
                           job_card.find('h3', class_='job-title') or \
                           job_card.find(class_='job-title') or \
                           job_card.find('h1') or \
                           job_card.find('h2') or \
                           job_card.find('h3') or \
                           job_card.find('a', class_='job-title') or \
                           job_card.find(attrs={'data-ph-at-id': 'job-title'})
                title = title_tag.get_text(strip=True) if title_tag else 'N/A'
                
                # Extract location
                location_tag = job_card.find(attrs={'data-ph-at-id': 'job-location'}) or \
                              job_card.find('span', class_='location') or \
                              job_card.find('div', class_='job-location') or \
                              job_card.find(class_=lambda x: x and 'location' in x.lower())
                location = location_tag.get_text(strip=True) if location_tag else 'N/A'
                
                # Extract category
                category_tag = job_card.find(attrs={'data-ph-at-id': 'job-category'}) or \
                              job_card.find('span', class_='category') or \
                              job_card.find('div', class_='job-category')
                category = category_tag.get_text(strip=True) if category_tag else 'N/A'
                
                # Extract job type
                type_tag = job_card.find(attrs={'data-ph-at-id': 'job-type'}) or \
                          job_card.find('span', class_='job-type')
                job_type = type_tag.get_text(strip=True) if type_tag else 'N/A'
                
                job_record = {
                    'title': title,
                    'job_id': str(job_id),
                    'link': job_url,
                    'location': location,
                    'category': category,
                    'job_type': job_type,
                    'posted_date': '',
                    'company': 'TeraRecon (Concert AI)',
                    'description': None,
                    'status': 'active',
                    'date': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                    'source': 'terarecon',
                }
                
                self.jobs.append(job_record)
                existing_ids.add(str(job_id))
                new_count += 1
                
                print(f"  [{new_count}] {job_id} - {title} - {location}")
                
            except Exception as e:
                print(f"Error parsing job card: {e}")
                continue
        
        self._save_jobs()
        print(f"\n{'='*60}")
        print(f"✓ Found {new_count} new jobs")
        print(f"✓ Skipped {skipped_count} duplicates")
        print(f"📊 Total jobs: {len(self.jobs)}")
        print(f"{'='*60}")

    def fetch_job_descriptions(self, delay=5):
        """Fetch detailed job descriptions using ZenRows"""
        jobs_to_update = [job for job in self.jobs if job.get('link') and not job.get('description')]

        if not jobs_to_update:
            print("\n✓ All jobs already have descriptions.")
            return

        print(f"\nFetching descriptions for {len(jobs_to_update)} jobs...")

        success_count = 0
        failed_count = 0
        failed_jobs = []

        for i, job in enumerate(jobs_to_update):
            link = job.get('link')

            if not link:
                failed_count += 1
                continue
            
            print(f"\n[{i+1}/{len(jobs_to_update)}] {job['title']}")

            # Try up to 2 times
            for attempt in range(2):
                try:
                    wait_time = '5000' if attempt == 0 else '8000'
                    
                    html = self.fetch_page_with_zenrows(link, wait_time=wait_time)

                    if html:
                        soup = BeautifulSoup(html, 'html.parser')

                        # Try multiple selectors for job description
                        desc_div = (
                            soup.find('div', class_='job-description') or
                            soup.find('div', attrs={'data-ph-at-id': 'job-description'}) or
                            soup.find('div', class_='jd-info') or
                            soup.find('section', class_='job-details') or
                            soup.find('div', id='job-description')
                        )

                        if desc_div:
                            full_text = desc_div.get_text(separator='\n', strip=True)
                            
                            # Extract only Role Summary and Responsibilities sections
                            description_parts = []
                            
                            # Try to find Role Summary section
                            if 'Role Summary' in full_text:
                                role_summary_match = re.search(
                                    r'Role Summary\s*(.*?)(?=\n(?:Requirements|Responsibilities|Qualifications|Learn More|EEO|Explore Location|Company Overview|Apply Now|$))',
                                    full_text,
                                    re.DOTALL | re.IGNORECASE
                                )
                                if role_summary_match:
                                    description_parts.append("Role Summary\n" + role_summary_match.group(1).strip())
                            
                            # Try to find Responsibilities section
                            if 'Responsibilities' in full_text:
                                responsibilities_match = re.search(
                                    r'Responsibilities\s*(.*?)(?=\n(?:Requirements|Qualifications|Learn More|EEO|Explore Location|Company Overview|Apply Now|$))',
                                    full_text,
                                    re.DOTALL | re.IGNORECASE
                                )
                                if responsibilities_match:
                                    description_parts.append("Responsibilities\n" + responsibilities_match.group(1).strip())
                            
                            # Try to find Requirements section
                            if 'Requirements' in full_text:
                                requirements_match = re.search(
                                    r'Requirements\s*(.*?)(?=\n(?:Learn More|EEO|Explore Location|Company Overview|Apply Now|$))',
                                    full_text,
                                    re.DOTALL | re.IGNORECASE
                                )
                                if requirements_match:
                                    description_parts.append("Requirements\n" + requirements_match.group(1).strip())
                            
                            # If we found specific sections, use them; otherwise use first 2000 chars
                            if description_parts:
                                job['description'] = '\n\n'.join(description_parts)
                            else:
                                # Fallback: take first 2000 chars to avoid company info
                                job['description'] = full_text[:2000] + ('...' if len(full_text) > 2000 else '')
                            
                            print(f"  ✓ Success ({len(job['description'])} chars)")
                            success_count += 1
                            break
                        else:
                            if attempt == 0:
                                print(f"  ⚠ Description not found, retrying...")
                                time.sleep(2)
                                continue
                            else:
                                print(f"  ✗ Description not found in HTML")
                                failed_jobs.append({
                                    'job_id': job['job_id'],
                                    'title': job['title'],
                                    'link': link
                                })
                                failed_count += 1
                                break
                    else:
                        if attempt == 0:
                            print(f"  ⚠ Failed to fetch page - Retrying...")
                            time.sleep(2)
                            continue
                        else:
                            print(f"  ✗ Failed to fetch page")
                            failed_count += 1
                            break

                except Exception as e:
                    if attempt == 0:
                        print(f"  ⚠ Error: {e} - Retrying...")
                        time.sleep(2)
                        continue
                    else:
                        print(f"  ✗ Error: {e}")
                        failed_count += 1
                        break

            # Save progress every 3 jobs
            if (i + 1) % 3 == 0:
                self._save_jobs()
                print(f"  💾 Saved ({success_count}/{i+1} successful)")

            time.sleep(delay)

        self._save_jobs()

        print(f"\n{'='*60}")
        print(f"Description Fetching Complete!")
        print(f"  ✓ Successful: {success_count}")
        print(f"  ✗ Failed: {failed_count}")
        print(f"  📊 Total: {len(self.jobs)} jobs")

        if failed_jobs:
            print(f"\nFailed jobs:")
            for fj in failed_jobs:
                print(f"  - {fj['job_id']}: {fj['title']}")

        print(f"{'='*60}")

    def _save_jobs(self):
        """Save jobs to JSON file"""
        with open(self.output_file, 'w', encoding='utf-8') as f:
            json.dump(self.jobs, f, indent=2, ensure_ascii=False)

    def run(self, fetch_descriptions=True):
        """Run the complete scraping process"""
        self.parse_job_listings()
        if fetch_descriptions:
            self.fetch_job_descriptions()


if __name__ == '__main__':
    print("Starting TeraRecon/Concert AI job scraper...")
    print("=" * 60)
    
    scraper = TerareconScraper()
    scraper.run(fetch_descriptions=True)
    
    print("\n" + "=" * 60)
    print(f"✓ Scraping complete! Total jobs: {len(scraper.jobs)}")
    print(f"✓ Jobs saved to: terarecon_jobs.json")
    print("=" * 60)