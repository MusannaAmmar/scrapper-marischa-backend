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




class MedtronicsScraper:
    def __init__(self, apikey=os.getenv('ZENROWS'), output_file='json_files/medtronics_jobs.json'):
        self.apikey = apikey
        self.output_file = output_file
        self.jobs = []
        self._load_existing_jobs()
        
        # Workday API configuration
        self.base_url = 'https://medtronic.wd1.myworkdayjobs.com'
        self.api_endpoint = f'{self.base_url}/wday/cxs/medtronic/MedtronicCareers/jobs'
        
        # Netherlands location ID
        self.netherlands_id = '9696868b09c64d52a62ee13b052383cc'
        
        # Job Family Group filters (static)
        self.job_family_groups = [
            'dbaf47119668100109f8c75c272b0000',
            '2df7911d885445ecb54c1ed4670e05e7',
            'd5575fc80af44949aae34f0770ad3fcf',
            '5d03e9707876432d93848a9e7146e1ad',
            'a1fac31977894774aedd9a134ec054ad',
            '2fe8588f35e84eb98ef535f4d738f243',
            '3b9e5dd261944d18b3f8d166e2c447bc',
            'ae3cca7615db4991b6c46b1d1e235e88',
            '4e8537909ca04133879bbd846eef97bf'
        ]

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

    def fetch_jobs_from_api(self, offset=0, limit=20, location_country_id=None, job_family_groups=None):
        """Fetch jobs directly from Workday API"""
        payload = {
            "appliedFacets": {},
            "limit": limit,
            "offset": offset,
            "searchText": ""
        }
        
        # Add Netherlands location filter
        if location_country_id:
            payload["appliedFacets"]["locationCountry"] = [location_country_id]
        
        # Add job family group filters
        if job_family_groups:
            payload["appliedFacets"]["jobFamilyGroup"] = job_family_groups
        
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        try:
            response = requests.post(self.api_endpoint, json=payload, headers=headers, timeout=30)
            
            if response.status_code == 200:
                return response.json()
            else:
                print(f"⚠ API returned status {response.status_code}")
                return None
                
        except Exception as e:
            print(f"Error fetching from API: {e}")
            return None

    def parse_job_listings(self):
        """Parse job listings from Workday API"""
        print(f"Fetching jobs from Workday API for Netherlands...")
        print(f"Filtering by {len(self.job_family_groups)} job family groups...")
        
        existing_ids = self._get_existing_job_ids()
        new_count = 0
        skipped_count = 0
        offset = 0
        limit = 20
        
        while True:
            print(f"\nFetching jobs {offset} to {offset + limit}...")
            
            api_data = self.fetch_jobs_from_api(
                offset=offset, 
                limit=limit, 
                location_country_id=self.netherlands_id,
                job_family_groups=self.job_family_groups
            )
            
            if not api_data:
                break
            
            job_postings = api_data.get('jobPostings', [])
            total_available = api_data.get('total', 0)
            
            if not job_postings:
                print("No more jobs found")
                break
            
            print(f"Found {len(job_postings)} jobs in this batch (Total: {total_available})")
            
            for job_data in job_postings:
                try:
                    title = job_data.get('title', '')
                    bullet_fields = job_data.get('bulletFields', [])
                    job_id = bullet_fields[0] if bullet_fields else ''
                    
                    if job_id and job_id in existing_ids:
                        skipped_count += 1
                        continue
                    
                    location_text = job_data.get('locationsText', '')
                    posted_date = job_data.get('postedOn', '')
                    external_path = job_data.get('externalPath', '')
                    time_type = job_data.get('timeType', '')
                    
                    # Build proper job URL
                    if external_path:
                        # Remove '/job/' prefix if present
                        if external_path.startswith('/job/'):
                            external_path = external_path[5:]  # Remove '/job/'
                        elif external_path.startswith('job/'):
                            external_path = external_path[4:]  # Remove 'job/'
                        
                        job_url = f"{self.base_url}/en-US/MedtronicCareers/job/{external_path}"
                    else:
                        job_url = ''
                    
                    job_record = {
                        'title': title,
                        'job_id': job_id,
                        'link': job_url,
                        'location': location_text,
                        'job_type': time_type,
                        'posted_date': posted_date,
                        'company': 'Medtronic',
                        'description': None,
                        'status': 'active',
                        'source': 'medtronic',
                        'date': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                    }
                    
                    self.jobs.append(job_record)
                    if job_id:
                        existing_ids.add(job_id)
                    new_count += 1
                    
                    print(f"  [{new_count}] {title} - {location_text} (ID: {job_id})")
                    
                except Exception as e:
                    print(f"Error parsing job: {e}")
                    continue
            
            # Check if we got all jobs
            if offset + limit >= total_available:
                print(f"\nFetched all {total_available} available jobs")
                break
            
            offset += limit
            time.sleep(1)
        
        self._save_jobs()
        print(f"\n{'='*60}")
        print(f"✓ Found {new_count} new jobs")
        print(f"✓ Skipped {skipped_count} duplicates")
        print(f"📊 Total jobs: {len(self.jobs)}")
        print(f"{'='*60}")

    @staticmethod
    def _extract_relevant_description(description: str) -> str:
        """
        Extract only relevant sections from job description:
        - Responsibilities
        - Required Knowledge and Experience
        
        Exclude:
        - Company overview (At Medtronic...)
        - A Day in the Life intro
        - Physical Job Requirements
        - Benefits & Compensation
        - About Medtronic
        
        Reduces description from ~3000 words to ~500-800 words.
        """
        if not description or description == 'N/A':
            return 'N/A'
        
        # Find the start marker: "Responsibilities may include"
        start_match = re.search(
            r'Responsibilities may include the following',
            description,
            re.IGNORECASE
        )
        
        # Find the end marker: "Physical Job Requirements"
        end_match = re.search(
            r'Physical Job Requirements',
            description,
            re.IGNORECASE
        )
        
        # Extract the section between markers
        if start_match and end_match:
            relevant_text = description[start_match.start():end_match.start()].strip()
        elif start_match:
            # If no end marker, take from start to a reasonable cutoff
            # Look for "Benefits" or "About" as fallback end markers
            fallback_end = re.search(
                r'Benefits & Compensation|About Medtronic|Learn more about',
                description,
                re.IGNORECASE
            )
            if fallback_end:
                relevant_text = description[start_match.start():fallback_end.start()].strip()
            else:
                # Take from start marker to end
                relevant_text = description[start_match.start():].strip()
        else:
            # Fallback: Look for "Required Knowledge" or "Requirements"
            requirements_match = re.search(
                r'Required Knowledge and Experience|Requirements:|Qualifications:',
                description,
                re.IGNORECASE
            )
            
            if requirements_match:
                # Extract from requirements to end markers
                end_match = re.search(
                    r'Physical Job Requirements|Benefits & Compensation|About Medtronic',
                    description,
                    re.IGNORECASE
                )
                if end_match:
                    relevant_text = description[requirements_match.start():end_match.start()].strip()
                else:
                    relevant_text = description[requirements_match.start():].strip()
            else:
                # Last resort: return first 1000 words
                words = description.split()
                if len(words) > 1000:
                    relevant_text = ' '.join(words[:1000]) + '...'
                else:
                    relevant_text = description
        
        # Clean up extra whitespace
        relevant_text = re.sub(r'\n\s*\n+', '\n\n', relevant_text)
        relevant_text = re.sub(r' +', ' ', relevant_text)
        
        # Limit to maximum 1000 words for safety
        words = relevant_text.split()
        if len(words) > 1000:
            relevant_text = ' '.join(words[:1000]) + '...'
        
        return relevant_text.strip()
    

    def fetch_job_descriptions(self, delay=8):
        """Fetch detailed job descriptions using ZenRows and extract relevant sections"""
        jobs_to_update = [job for job in self.jobs if job.get('link') and not job.get('description')]
    
        if not jobs_to_update:
            print("\n✓ All jobs already have descriptions.")
            return
    
        print(f"\nFetching descriptions for {len(jobs_to_update)} jobs...")
        print("This will take a while - be patient!")
    
        success_count = 0
        failed_count = 0
        failed_jobs = []
        total_words_saved = 0
    
        for i, job in enumerate(jobs_to_update):
            link = job.get('link')
    
            if not link:
                failed_count += 1
                continue
            
            print(f"\n[{i+1}/{len(jobs_to_update)}] {job['title']}")
    
            # Try up to 2 times with increasing wait times
            for attempt in range(2):
                try:
                    wait_time = '8000' if attempt == 0 else '12000'
    
                    params = {
                        'url': link,
                        'apikey': self.apikey,
                        'js_render': 'true',
                        'wait': wait_time,
                        'premium_proxy': 'true',
                    }
                    response = requests.get('https://api.zenrows.com/v1/', params=params, timeout=90)
    
                    if response.status_code == 200:
                        soup = BeautifulSoup(response.text, 'html.parser')
    
                        # Try primary selector
                        desc_div = soup.find('div', attrs={'data-automation-id': 'jobPostingDescription'})
    
                        # Try alternative selectors if primary fails
                        if not desc_div:
                            desc_div = soup.find('div', class_='jobPostingDescription')
    
                        if not desc_div:
                            # Try finding by text content indicators
                            desc_div = soup.find('div', string=lambda text: text and 'Responsibilities' in text)
                            if desc_div:
                                desc_div = desc_div.find_parent('div')
    
                        if desc_div:
                            # Get full description
                            full_description = desc_div.get_text(separator='\n', strip=True)
                            original_words = len(full_description.split())
                            
                            # Extract only relevant sections
                            relevant_description = self._extract_relevant_description(full_description)
                            relevant_words = len(relevant_description.split())
                            
                            # Save relevant description
                            job['description'] = relevant_description
                            
                            # Calculate savings
                            words_saved = original_words - relevant_words
                            total_words_saved += words_saved
                            reduction_percent = int((words_saved / original_words) * 100) if original_words > 0 else 0
                            
                            print(f"  ✓ Success ({relevant_words} words, {reduction_percent}% reduction)")
                            success_count += 1
                            break  # Success, exit retry loop
                        else:
                            if attempt == 0:
                                print(f"  ⚠ Description not found, retrying with longer wait...")
                                time.sleep(3)
                                continue
                            else:
                                print(f"  ✗ Description not found in HTML")
                                failed_count += 1
                                break
                    else:
                        if attempt == 0:
                            print(f"  ⚠ HTTP {response.status_code} - Retrying...")
                            time.sleep(3)
                            continue
                        else:
                            print(f"  ⚠ HTTP {response.status_code} - Skipping")
                            failed_count += 1
                            break
                        
                except Exception as e:
                    if attempt == 0:
                        print(f"  ⚠ Error: {e} - Retrying...")
                        time.sleep(3)
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
        print(f"  💰 Words saved: ~{total_words_saved:,} ({int(total_words_saved * 0.00075)} tokens)")
        print(f"  💸 Estimated cost savings: ~${(total_words_saved * 0.00075 * 0.00002):.4f} per embedding run")
    
        if failed_jobs:
            print(f"\nFailed jobs:")
            for fj in failed_jobs:
                print(f"  - {fj['job_id']}: {fj['title']}")
                print(f"    {fj['link']}")
    
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
    print("Starting Medtronic Netherlands job scraper...")
    print("=" * 60)
    
    scraper = MedtronicsScraper()
    scraper.run(fetch_descriptions=True)
    
    print("\n" + "=" * 60)
    print(f"✓ Scraping complete! Total jobs: {len(scraper.jobs)}")
    print("=" * 60)