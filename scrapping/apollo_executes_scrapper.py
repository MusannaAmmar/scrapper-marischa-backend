# import os
# import json
# import re
# import time
# from bs4 import BeautifulSoup
# from dotenv import load_dotenv
# from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
# from datetime import datetime, timezone

# load_dotenv()

# BASE_URL = 'https://roles.apolloexecutives.com'
# EMAIL = os.getenv('APOLLO_EMAIL')
# PASSWORD = os.getenv('APOLLO_PASSWORD')
# SESSION_FILE = 'apollo_executes_session.json'


# class ApolloExecutesScraper:
#     def __init__(self, output_file='json_files/apollo_executes_jobs.json'):
#         self.output_file = output_file
#         self.jobs = []
#         self._load_existing_jobs()

#         self.base_url = BASE_URL
#         self.login_url = f'{self.base_url}/backup-email-login'
#         self.jobs_url = f'{self.base_url}/'

#     # ------------------------------------------------------------------ #
#     # Persistence helpers
#     # ------------------------------------------------------------------ #

#     def _load_existing_jobs(self):
#         if os.path.exists(self.output_file):
#             with open(self.output_file, 'r', encoding='utf-8') as f:
#                 self.jobs = json.load(f)
#             print(f"Loaded {len(self.jobs)} existing jobs from {self.output_file}")
#         else:
#             self.jobs = []

#     def _get_existing_job_ids(self):
#         return {job['job_id'] for job in self.jobs if job.get('job_id')}

#     def _save_jobs(self):
#         with open(self.output_file, 'w', encoding='utf-8') as f:
#             json.dump(self.jobs, f, indent=2, ensure_ascii=False)

#     # ------------------------------------------------------------------ #
#     # Browser / auth helpers
#     # ------------------------------------------------------------------ #

#     def _build_context(self, playwright):
#         """Launch Chromium and return (browser, context, page)."""
#         browser = playwright.chromium.launch(
#             headless=True,
#             slow_mo=400,
#             args=['--disable-blink-features=AutomationControlled', '--no-sandbox'],
#         )
#         storage = SESSION_FILE if os.path.exists(SESSION_FILE) else None
#         context = browser.new_context(
#             user_agent=(
#                 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
#                 'AppleWebKit/537.36 (KHTML, like Gecko) '
#                 'Chrome/121.0.0.0 Safari/537.36'
#             ),
#             viewport={'width': 1280, 'height': 800},
#             storage_state=storage,
#         )
#         page = context.new_page()
#         return browser, context, page

#     def _check_logged_in(self, page):
#         """Return True if the current page is NOT the login/sign-up wall."""
#         return page.query_selector('#linkedin-login') is None

#     def _login(self, playwright):
#         """Return (browser, context, page) with an authenticated session."""
#         browser, context, page = self._build_context(playwright)

#         # --- Try saved session first ---
#         if os.path.exists(SESSION_FILE):
#             print("Found saved session. Verifying…")
#             page.goto(self.jobs_url, wait_until='networkidle', timeout=30000)
#             time.sleep(2)
#             if self._check_logged_in(page):
#                 print("Session valid — skipping login.")
#                 return browser, context, page
#             print("Session expired — logging in again…")
#             context.close()
#             browser.close()
#             os.remove(SESSION_FILE)
#             browser, context, page = self._build_context(playwright)

#         # --- Fresh email login ---
#         print(f"Navigating to login page: {self.login_url}")
#         page.goto(self.login_url, wait_until='networkidle', timeout=30000)
#         time.sleep(2)

#         # Fill email
#         try:
#             page.wait_for_selector('input[type="email"], input[name="email"]', timeout=10000)
#             page.fill('input[type="email"], input[name="email"]', EMAIL)
#         except PlaywrightTimeoutError:
#             raise RuntimeError("Could not find email input on login page.")

#         # Fill password
#         try:
#             page.wait_for_selector('input[type="password"], input[name="password"]', timeout=10000)
#             page.fill('input[type="password"], input[name="password"]', PASSWORD)
#         except PlaywrightTimeoutError:
#             raise RuntimeError("Could not find password input on login page.")

#         # Submit
#         page.keyboard.press('Enter')
#         page.wait_for_load_state('networkidle', timeout=30000)
#         time.sleep(2)

#         if not self._check_logged_in(page):
#             raise RuntimeError("Login failed — still on login/signup wall after submission.")

#         print("Login successful.")
#         context.storage_state(path=SESSION_FILE)
#         print(f"Session saved to {SESSION_FILE}")
#         return browser, context, page

#     # ------------------------------------------------------------------ #
#     # Scraping helpers
#     # ------------------------------------------------------------------ #

#     def _extract_job_id(self, href: str) -> str:
#         """Derive a stable job_id from the job URL."""
#         # e.g. /vacancy/cfo-at-acme  or  /job/123  or  /roles/123-cfo-acme
#         for pattern in (
#             r'/(?:vacancy|job|role|position)/([^/?#]+)',
#             r'[\?&]id=([^&]+)',
#         ):
#             m = re.search(pattern, href, re.I)
#             if m:
#                 return m.group(1)
#         # Fall back to using the path
#         return re.sub(r'[^a-z0-9\-]', '-', href.strip('/').lower())

#     def _parse_card(self, card) -> dict | None:
#         """Extract job metadata from a single listing card (BeautifulSoup tag)."""
#         # Title + link — try common selectors used by Laravel job boards
#         link_elem = (
#             card.find('a', class_=re.compile(r'job.?title|vacancy.?title|card.?title|post.?title', re.I))
#             or card.find('h2')
#             or card.find('h3')
#             or card.find('h4')
#         )
#         if not link_elem:
#             return None

#         # If the heading itself is an <a>, use it; otherwise find nested <a>
#         if link_elem.name == 'a':
#             anchor = link_elem
#         else:
#             anchor = link_elem.find('a')
#         if not anchor:
#             return None

#         title = anchor.get_text(strip=True)
#         href = anchor.get('href', '')
#         if not href:
#             return None

#         full_link = href if href.startswith('http') else self.base_url + href
#         job_id = self._extract_job_id(href)

#         # Location — look for common location indicators
#         location = ''
#         location_elem = (
#             card.find(class_=re.compile(r'location|city|place', re.I))
#             or card.find('span', string=re.compile(r'\bNetherlands\b|\bAmsterdam\b|\bUtrecht\b', re.I))
#         )
#         if location_elem:
#             location = location_elem.get_text(strip=True)

#         # Category / function
#         category = ''
#         cat_elem = card.find(class_=re.compile(r'categ|sector|function|type', re.I))
#         if cat_elem:
#             category = cat_elem.get_text(strip=True)

#         return {
#             'title': title,
#             'job_id': job_id,
#             'job_seq_no': job_id,
#             'link': full_link,
#             'location': location,
#             'city': '',
#             'country': 'Netherlands',
#             'job_type': '',
#             'posted_date': '',
#             'company': '',
#             'category': category,
#             'department': '',
#             'description': '',
#             'skills': [],
#             'status': 'active',
#             'source': 'apolloexecutives',
#             'date': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
#         }

#     # ------------------------------------------------------------------ #
#     # Public scraping methods
#     # ------------------------------------------------------------------ #

#     def parse_job_listings(self, page, debug=False):
#         """Navigate to the jobs index and extract all job cards."""
#         print("\nFetching Apollo Executives job listings…")
#         page.goto(self.jobs_url, wait_until='networkidle', timeout=30000)
#         time.sleep(3)

#         if debug:
#             with open('apollo_executes_debug.html', 'w', encoding='utf-8') as f:
#                 f.write(page.content())
#             print("Debug HTML saved to apollo_executes_debug.html")

#         html = page.content()
#         soup = BeautifulSoup(html, 'html.parser')

#         # Try multiple container selectors from most-to-least specific
#         cards = (
#             soup.find_all(class_=re.compile(r'job.?card|vacancy.?card|job.?item|vacancy.?item|job.?listing|job.?widget', re.I))
#             or soup.find_all('article')
#             or soup.find_all(class_=re.compile(r'grid.?job|post.?item', re.I))
#         )

#         # Deduplicate — the regex above may match nested elements
#         seen_tags = set()
#         unique_cards = []
#         for c in cards:
#             tag_id = id(c)
#             if tag_id not in seen_tags:
#                 seen_tags.add(tag_id)
#                 unique_cards.append(c)
#         cards = unique_cards

#         print(f"Found {len(cards)} job card(s).")

#         existing_ids = self._get_existing_job_ids()
#         new_count = 0
#         skipped_count = 0

#         for card in cards:
#             job = self._parse_card(card)
#             if not job:
#                 continue
#             if job['job_id'] in existing_ids:
#                 skipped_count += 1
#                 continue
#             self.jobs.append(job)
#             existing_ids.add(job['job_id'])
#             new_count += 1
#             print(f"  + {job['title']} | {job['category']} | {job['job_id']}")

#         self._save_jobs()
#         print(f"\n{'='*60}")
#         print(f"  New jobs found    : {new_count}")
#         print(f"  Duplicates skipped: {skipped_count}")
#         print(f"  Total jobs stored : {len(self.jobs)}")
#         print(f"{'='*60}")

#     def fetch_job_descriptions(self, page, delay=5):
#         """Visit each job page and extract the full description."""
#         jobs_to_update = [
#             j for j in self.jobs
#             if not j.get('description') or len(j.get('description', '')) < 200
#         ]

#         if not jobs_to_update:
#             print("\nAll jobs already have descriptions.")
#             return

#         print(f"\nFetching descriptions for {len(jobs_to_update)} job(s)…")
#         success_count = 0
#         failed_count = 0

#         for i, job in enumerate(jobs_to_update):
#             link = job.get('link')
#             if not link:
#                 failed_count += 1
#                 continue

#             print(f"\n[{i+1}/{len(jobs_to_update)}] {job['title']}")
#             try:
#                 page.goto(link, wait_until='networkidle', timeout=30000)
#                 time.sleep(3)
#                 html = page.content()
#                 soup = BeautifulSoup(html, 'html.parser')

#                 # Extract company name from job detail page if available
#                 company_elem = soup.find(class_=re.compile(r'company|employer|organisation|client', re.I))
#                 if company_elem and not job.get('company'):
#                     job['company'] = company_elem.get_text(strip=True)

#                 # Extract description — try specific wrappers, fall back to main content
#                 desc_elem = (
#                     soup.find('div', class_=re.compile(r'job.?descr|vacancy.?descr|job.?detail|vacancy.?body|job.?content|post.?content', re.I))
#                     or soup.find('div', class_='wysiwyg-element')
#                     or soup.find('div', class_='entry-content')
#                     or soup.find('section', class_=re.compile(r'detail|content|body', re.I))
#                     or soup.find('article')
#                     or soup.find('main')
#                 )

#                 if not desc_elem:
#                     print("  Description element not found.")
#                     failed_count += 1
#                     continue

#                 raw_text = desc_elem.get_text(separator='\n', strip=True)
#                 if len(raw_text) < 100:
#                     print(f"  Text too short ({len(raw_text)} chars) — skipping.")
#                     failed_count += 1
#                     continue

#                 job['description'] = raw_text
#                 success_count += 1
#                 print(f"  Description: {len(raw_text.split())} words")

#             except Exception as e:
#                 print(f"  Error fetching description: {e}")
#                 failed_count += 1

#             # Save progress every 3 jobs
#             if (i + 1) % 3 == 0:
#                 self._save_jobs()
#                 print(f"  Progress saved ({success_count}/{i+1} successful)")

#             time.sleep(delay)

#         self._save_jobs()
#         print(f"\n{'='*60}")
#         print(f"Description Fetching Complete!")
#         print(f"  Successful : {success_count}")
#         print(f"  Failed     : {failed_count}")
#         print(f"{'='*60}")

#     # ------------------------------------------------------------------ #
#     # Entry point
#     # ------------------------------------------------------------------ #

#     def run(self, fetch_descriptions=True, debug=False):
#         with sync_playwright() as p:
#             browser, context, page = self._login(p)
#             try:
#                 self.parse_job_listings(page, debug=debug)
#                 if fetch_descriptions:
#                     self.fetch_job_descriptions(page)
#             finally:
#                 context.close()
#                 browser.close()


# if __name__ == '__main__':
#     print("Starting Apollo Executives job scraper…")
#     print("=" * 60)

#     scraper = ApolloExecutesScraper()
#     scraper.run(fetch_descriptions=True, debug=False)

#     print("\n" + "=" * 60)
#     print(f"Done! Total jobs: {len(scraper.jobs)}")
#     print("=" * 60)
