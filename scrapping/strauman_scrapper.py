import os
import json
import re
import time
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from datetime import datetime, timezone

load_dotenv()


class StraumanScraper:
    def __init__(self, apikey=os.getenv('ZENROWS'), output_file='json_files/strauman_jobs.json'):
        self.apikey = apikey
        self.output_file = output_file
        self.jobs = []
        self._load_existing_jobs()
        
        # Straumann careers configuration
        self.base_url = 'https://careers.straumann.com'
        self.search_url = f'{self.base_url}/global/en/search-results?qcountry=Netherlands'
        
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

    def fetch_search_page(self, debug=False):
        """Fetch search results page using ZenRows"""
        print("Fetching Straumann job search page...")
        
        params = {
            'url': self.search_url,
            'apikey': self.apikey,
            'js_render': 'true',
            'wait': '6000',
            'premium_proxy': 'true',
        }
        
        try:
            response = requests.get('https://api.zenrows.com/v1/', params=params, timeout=90)
            
            if response.status_code == 200:
                html = response.text
                if debug:
                    with open('strauman_debug.html', 'w', encoding='utf-8') as f:
                        f.write(html)
                    print("  💾 Debug HTML saved to strauman_debug.html")
                return html
            else:
                print(f"⚠ ZenRows returned status {response.status_code}: {response.text[:300]}")
                return None
                
        except Exception as e:
            print(f"Error fetching search page: {e}")
            return None

    def parse_job_listings(self, debug=False):
        """Parse job listings from search results HTML"""
        print(f"Fetching jobs from Straumann for Netherlands...")
        
        html = self.fetch_search_page(debug=debug)
        
        if not html:
            print("Failed to fetch search page")
            return
        
        soup = BeautifulSoup(html, 'html.parser')
        
        jobs_data = []
        
        # Strategy 1: find job data embedded in phApp.ddo script block
        # Phenom People inlines the search results as JSON inside a <script> tag
        all_scripts = soup.find_all('script')
        for script in all_scripts:
            content = script.string or ''
            if 'eagerLoadRefineSearch' not in content:
                continue
            try:
                # Extract the jobs array directly: "jobs":[{...}],"aggregations"
                jobs_match = re.search(
                    r'"jobs"\s*:\s*(\[\{.+?\}\])\s*,\s*"aggregations"',
                    content,
                    re.DOTALL
                )
                if jobs_match:
                    jobs_data = json.loads(jobs_match.group(1))
                    print(f"Found {len(jobs_data)} jobs in embedded script data")
                    break
            except Exception as e:
                print(f"Error parsing embedded job data: {e}")
                continue
        
        # Strategy 2: fallback to HTML job cards
        if not jobs_data:
            print("Could not find job data in page scripts. Trying HTML card parsing...")
            jobs_data = self._parse_job_cards_from_html(soup)
        
        existing_ids = self._get_existing_job_ids()
        new_count = 0
        skipped_count = 0
        
        for job_data in jobs_data:
            try:
                # Extract job details
                job_id = job_data.get('jobId') or job_data.get('reqId', '')
                job_seq_no = job_data.get('jobSeqNo', '')
                
                # Skip if already exists
                if job_id in existing_ids:
                    skipped_count += 1
                    continue
                
                # Extract description
                ml_parser = job_data.get('ml_job_parser', {})
                description_text = (
                    ml_parser.get('descriptionTeaser_ats') or 
                    ml_parser.get('descriptionTeaser') or
                    job_data.get('descriptionTeaser', '')
                )
                
                # Build job object (use pre-resolved link if set by fallback parser)
                link = job_data.get('link_override') or f"{self.base_url}/global/en/job/{job_seq_no}/{self._slugify(job_data.get('title', ''))}"
                job = {
                    'title': job_data.get('title', 'N/A'),
                    'job_id': job_id,
                    'job_seq_no': job_seq_no,
                    'link': link,
                    'location': job_data.get('location', 'N/A'),
                    'city': job_data.get('city', ''),
                    'location': job_data.get('country', ''),
                    'job_type': job_data.get('type', ''),
                    'posted_date': job_data.get('postedDate', ''),
                    'company': 'Straumann Group',
                    'category': ', '.join(job_data.get('multi_category', [])) if job_data.get('multi_category') else job_data.get('category', ''),
                    'department': job_data.get('department', ''),
                    'description': description_text,  # Store teaser initially
                    # 'skills': job_data.get('ml_skills', []),
                    'status': 'active',
                    'source': 'straumann',
                    'date': datetime.now(timezone.utc).strftime('%Y-%m-%d')
                }
                
                self.jobs.append(job)
                existing_ids.add(job_id)
                new_count += 1
                
                print(f"  ✓ Added: {job['title']} (ID: {job_id})")
                
            except Exception as e:
                print(f"  ✗ Error parsing job: {e}")
                continue
        
        self._save_jobs()
        print(f"\n{'='*60}")
        print(f"✓ Found {new_count} new jobs")
        print(f"✓ Skipped {skipped_count} duplicates")
        print(f"📊 Total jobs: {len(self.jobs)}")
        print(f"{'='*60}")

    def _parse_job_cards_from_html(self, soup):
        """Fallback: Parse job cards directly from rendered HTML"""
        jobs = []
        
        # Phenom People renders job cards as <li> with data-ph-at-id or class patterns
        job_cards = (
            soup.find_all('li', attrs={'data-ph-at-id': re.compile(r'job')}) or
            soup.find_all('li', class_=re.compile(r'jobs-list-item|job-item|search-result')) or
            soup.find_all('div', attrs={'data-ph-at-id': re.compile(r'job-item')})
        )
        
        print(f"Found {len(job_cards)} job cards in HTML")
        
        for card in job_cards:
            try:
                # Find job link — Phenom People uses /job/<jobSeqNo>/<title-slug>
                link_elem = card.find('a', href=re.compile(r'/job/'))
                if not link_elem:
                    continue
                
                href = link_elem.get('href', '')
                full_link = href if href.startswith('http') else self.base_url + href
                
                # Extract jobSeqNo from URL: /job/<jobSeqNo>/<slug>
                seq_match = re.search(r'/job/([^/]+)/', href)
                job_seq_no = seq_match.group(1) if seq_match else ''
                
                # Extract numeric job ID from jobSeqNo (e.g. STGRGLOBAL20047EXTERNALENGLOBAL -> 20047)
                id_match = re.search(r'STGRGLOBAL(\d+)', job_seq_no, re.IGNORECASE)
                job_id = id_match.group(1) if id_match else job_seq_no
                
                if not job_id:
                    continue
                
                # Title
                title_elem = (
                    card.find(attrs={'data-ph-at-id': re.compile(r'job-title')}) or
                    card.find(['h3', 'h2', 'h4']) or
                    link_elem
                )
                title = title_elem.get_text(strip=True) if title_elem else 'N/A'
                
                # Location
                location_elem = card.find(attrs={'data-ph-at-id': re.compile(r'location')})
                location = location_elem.get_text(strip=True) if location_elem else 'N/A'
                
                job = {
                    'jobId': job_id,
                    'jobSeqNo': job_seq_no,
                    'title': title,
                    'location': location,
                    'link_override': full_link,   # store resolved link directly
                }
                jobs.append(job)
                print(f"  Card: {title} | {location} | {job_id}")
            except Exception as e:
                print(f"Error parsing job card: {e}")
                continue
        
        return jobs

    def fetch_job_descriptions(self, delay=8):
        """Fetch detailed job descriptions using ZenRows"""
        jobs_to_update = [job for job in self.jobs if job.get('link') and (not job.get('description') or len(job.get('description', '')) < 500)]
    
        if not jobs_to_update:
            print("\n✓ All jobs already have descriptions.")
            return
    
        print(f"\nFetching detailed descriptions for {len(jobs_to_update)} jobs...")
        print("This will take a while - be patient!")
    
        success_count = 0
        failed_count = 0
        total_words_saved = 0
    
        for i, job in enumerate(jobs_to_update):
            link = job.get('link')
    
            if not link:
                failed_count += 1
                continue
            
            print(f"\n[{i+1}/{len(jobs_to_update)}] {job['title']}")
    
            # Try up to 2 times
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
    
                        # Phenom People job detail page selectors
                        desc_div = (
                            soup.find('div', attrs={'data-ph-at-id': 'jobdescription-text'}) or
                            soup.find('div', class_='jd-info') or
                            soup.find('section', class_='job-description') or
                            soup.find('div', class_=re.compile(r'job-description|description-content'))
                        )
    
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
                            break
                        else:
                            if attempt == 0:
                                print(f"  ⚠ Description not found, retrying...")
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
        if total_words_saved > 0:
            print(f"  💰 Words saved: ~{total_words_saved:,} ({int(total_words_saved * 0.00075)} tokens)")
            print(f"  💸 Estimated cost savings: ~${(total_words_saved * 0.00075 * 0.00002):.4f} per embedding run")
        print(f"{'='*60}")

    @staticmethod
    def _slugify(text):
        """Convert text to URL-friendly slug"""
        text = text.lower()
        text = re.sub(r'[^\w\s-]', '', text)
        text = re.sub(r'[-\s]+', '-', text)
        return text.strip('-')

    @staticmethod
    def _extract_relevant_description(description: str) -> str:
        """
        Extract only relevant sections from Straumann job descriptions.

        Strategy:
        - If a known relevant section header is found, slice from there to the
          first trailing boilerplate section (or end of text).
        - Otherwise strip known boilerplate blocks from the top and bottom.

        Dutch relevant headers:  Kernverantwoordelijkheden, Benodigde Competenties,
                                  Opleidingsniveau, Werkervaring, Functie-eisen
        English relevant headers: Responsibilities, Requirements, Qualifications,
                                   The role, Your role, Key tasks
        """
        if not description or description == 'N/A':
            return 'N/A'

        # ── Step 1: find the earliest relevant section start ──────────────────
        relevant_start_patterns = [
            # Dutch
            r'Kernverantwoordelijkheden',
            r'Benodigde\s+Competenties',
            r'Opleidingsniveau',
            r'Werkervaring',
            r'Functie(?:omschrijving|-eisen|taken)',
            r'Taken\s+en\s+verantwoordelijkheden',
            r'Vereisten',
            r'Wat\s+(?:ga\s+je\s+doen|verwachten\s+wij)',
            # English
            r'(?:Key\s+)?Responsibilities',
            r'The\s+role\b',
            r'Your\s+(?:role|tasks|responsibilities)',
            r'What\s+you(?:\'ll)?\s+(?:do|be\s+doing)',
            r'Key\s+tasks',
            r'About\s+the\s+(?:role|position)',
        ]
        relevant_start = None
        for pat in relevant_start_patterns:
            m = re.search(pat, description, re.IGNORECASE)
            if m and (relevant_start is None or m.start() < relevant_start):
                relevant_start = m.start()

        # ── Step 2: find where boilerplate resumes (end boundary) ─────────────
        end_patterns = [
            # Dutch
            r'Wat\s+wij\s+bieden',
            r'Ons\s+aanbod',
            r'Wat\s+mag\s+je\s+verwachten',
            r'Solliciteren',
            r'gelijke\s+kansen\s+werkgever',
            # English
            r'What\s+we\s+offer',
            r'Our\s+offer',
            r'Benefits\s*(?:&|and)\s*Compensation',
            r'About\s+Straumann\s+Group',
            r'Equal\s+[Oo]pportunity',
            r'We\s+are\s+an\s+equal',
            r'All\s+qualified\s+applicants',
        ]
        relevant_end = None
        search_from = relevant_start or 0
        for pat in end_patterns:
            m = re.search(pat, description[search_from:], re.IGNORECASE)
            if m:
                abs_pos = search_from + m.start()
                if relevant_end is None or abs_pos < relevant_end:
                    relevant_end = abs_pos

        # ── Step 3: slice the relevant portion ───────────────────────────────
        if relevant_start is not None:
            cleaned = description[relevant_start:relevant_end].strip()
        elif relevant_end is not None:
            # No clear start found but end found – take from the beginning
            cleaned = description[:relevant_end].strip()
        else:
            # No markers at all – strip known top-of-page boilerplate blocks
            boilerplate_top = [
                r'^#WeChangeDentistry.*?\n\n',
                r'^Become a part of it\..*?\n\n',
                r'^About Straumann\n.*?\n\n',
                r'^Straumann(?:\s+Group)?\s+[-–]\s+\w.*?\n\n',
                r'^The Straumann Group is a global leader.*?\n\n',
                r'^Over Straumann.*?\n\n',
                r'^De Straumann Group.*?\n\n',
            ]
            cleaned = description
            for pat in boilerplate_top:
                cleaned = re.sub(pat, '', cleaned, flags=re.IGNORECASE | re.DOTALL)
            cleaned = cleaned.strip()

        # ── Step 4: clean up whitespace ───────────────────────────────────────
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
        cleaned = re.sub(r'[ \t]+', ' ', cleaned)
        cleaned = cleaned.strip()

        # Safety: if cleaning destroyed almost everything, return original
        if len(cleaned) < 100 and len(description) > 200:
            return description.strip()

        return cleaned

    def _save_jobs(self):
        """Save jobs to JSON file"""
        with open(self.output_file, 'w', encoding='utf-8') as f:
            json.dump(self.jobs, f, indent=2, ensure_ascii=False)

    def run(self, fetch_descriptions=True, debug=False):
        """Run the complete scraping process"""
        self.parse_job_listings(debug=debug)
        if fetch_descriptions:
            self.fetch_job_descriptions()


if __name__ == '__main__':
    print("Starting Straumann Netherlands job scraper...")
    print("=" * 60)
    
    scraper = StraumanScraper()
    scraper.run(fetch_descriptions=True, debug=False)
    
    print("\n" + "=" * 60)
    print(f"✓ Scraping complete! Total jobs: {len(scraper.jobs)}")
    print("=" * 60)