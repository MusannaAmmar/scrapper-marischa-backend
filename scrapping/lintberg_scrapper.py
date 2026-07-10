import json
import os
import re
import time
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from datetime import datetime, timezone

load_dotenv()

BASE_URL = 'https://www.lintberg.com'
EMAIL = os.getenv('LINTBERG_EMAIL')
PASSWORD = os.getenv('LINTBERG_PASSWORD')
OUTPUT_FILE = 'json_files/lintberg_jobs.json'

# ---- Filter Categories to select ----
TARGET_CATEGORIES = [
    'Management',
    'Healthcare',
    'IT',
    'Technology',
]
TARGET_REGION = 'Netherlands'


# ---- Load existing job IDs from JSON to avoid duplicates ----
def load_existing_job_ids(json_file):
    if not os.path.exists(json_file):
        return set(), []
    with open(json_file, 'r', encoding='utf-8') as f:
        try:
            existing_jobs = json.load(f)
        except json.JSONDecodeError:
            return set(), []
    existing_ids = {job['job_id'] for job in existing_jobs if job.get('job_id')}
    print(f"[DEDUP] Loaded {len(existing_ids)} existing job IDs from {json_file}")
    return existing_ids, existing_jobs





# ---- Step 1: Launch browser and login ----
def login_and_get_context(playwright):
    print("Launching browser...")
    browser = playwright.chromium.launch(
        headless=True,
        slow_mo=500,
        args=[
            '--disable-blink-features=AutomationControlled',
            '--no-sandbox',
        ]
    )

    context = browser.new_context(
        user_agent=(
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/121.0.0.0 Safari/537.36'
        ),
        viewport={'width': 1280, 'height': 800},
        locale='nl-NL',
        # ---- Save browser session (cookies + storage) so login persists ----
        storage_state='lintberg_session.json' if os.path.exists('lintberg_session.json') else None,
    )

    page = context.new_page()

    # ---- Check if already logged in via saved session ----
    if os.path.exists('lintberg_session.json'):
        print("Found saved session. Verifying login...")
        page.goto(f'{BASE_URL}/dashboard/', wait_until='networkidle', timeout=30000)
        time.sleep(2)

        if '/dashboard' in page.url or '/jobs' in page.url:
            print("Session valid! Skipping login.")
            return browser, context, page
        else:
            print("Session expired. Logging in again...")

    # ---- Fresh login ----
    print(f"Navigating to {BASE_URL}/login/ ...")
    page.goto(f'{BASE_URL}/login/', wait_until='networkidle', timeout=30000)
    time.sleep(2)

    # Fill email
    print("Filling in email...")
    try:
        page.wait_for_selector('input[name="un"], input[type="email"], input[name="email"]', timeout=10000)
        page.fill('input[name="un"], input[type="email"], input[name="email"]', EMAIL)
    except PlaywrightTimeoutError:
        print("ERROR: Could not find email input field.")
        raise

    # Fill password
    print("Filling in password...")
    try:
        page.wait_for_selector('input[name="pw"], input[type="password"], input[name="password"]', timeout=10000)
        page.fill('input[name="pw"], input[type="password"], input[name="password"]', PASSWORD)
    except PlaywrightTimeoutError:
        print("ERROR: Could not find password input field.")
        raise

    time.sleep(1)

    # Click login button
    print("Clicking login button...")
    try:
        page.click(
            'button[type="submit"], input[type="submit"], '
            'button:has-text("Login"), button:has-text("Inloggen"), '
            'button:has-text("Log in"), .login-btn, #login-btn'
        )
    except PlaywrightTimeoutError:
        print("ERROR: Could not find login button.")
        raise

    print("Waiting for login to complete...")
    try:
        page.wait_for_url(f'{BASE_URL}/dashboard/**', timeout=15000)
        print("Login successful! Redirected to dashboard.")
    except PlaywrightTimeoutError:
        print("WARNING: Did not redirect to dashboard.")
        print(f"Current URL: {page.url}")

    time.sleep(2)

    # ---- Save session for next run ----
    context.storage_state(path='lintberg_session.json')
    print("Session saved to lintberg_session.json")

    return browser, context, page


# ---- Step 2: Apply filters on jobs page ----
def apply_filters(page):
    print("\n[FILTER] Applying category and region filters...")

    # ---- Select Categories ----
    print("[FILTER] Selecting categories...")
    try:
        category_select = page.locator('select[name="categories-mobile"], select[name="categories"]')
        options = category_select.first.locator('option').all()
        selected_values = []

        for option in options:
            option_text = option.text_content().strip()
            for target in TARGET_CATEGORIES:
                if target.lower() in option_text.lower():
                    val = option.get_attribute('value')
                    if val:
                        selected_values.append(val)
                        print(f"[FILTER] Found category match: '{option_text}' (value={val})")

        if selected_values:
            category_select.first.evaluate(
                """(select, values) => {
                    for (let option of select.options) {
                        option.selected = values.includes(option.value);
                    }
                    select.dispatchEvent(new Event('change', { bubbles: true }));
                }""",
                selected_values
            )
            print(f"[FILTER] Selected {len(selected_values)} categories.")
        else:
            print("[FILTER] WARNING: No matching categories found.")

    except Exception as e:
        print(f"[FILTER] ERROR selecting categories: {e}")

    time.sleep(1)

    # ---- Select Region ----
    print("[FILTER] Selecting region: Netherlands...")
    try:
        region_select = page.locator('select[name="regions-mobile"], select[name="regions"]')
        options = region_select.first.locator('option').all()
        region_value = None

        for option in options:
            option_text = option.text_content().strip()
            if TARGET_REGION.lower() in option_text.lower():
                region_value = option.get_attribute('value')
                print(f"[FILTER] Found region match: '{option_text}' (value={region_value})")
                break

        if region_value:
            region_select.first.evaluate(
                """(select, value) => {
                    for (let option of select.options) {
                        option.selected = option.value === value;
                    }
                    select.dispatchEvent(new Event('change', { bubbles: true }));
                }""",
                region_value
            )
            print(f"[FILTER] Selected region: {TARGET_REGION}")
        else:
            print(f"[FILTER] WARNING: Region '{TARGET_REGION}' not found.")

    except Exception as e:
        print(f"[FILTER] ERROR selecting region: {e}")

    time.sleep(1)

    # ---- Click Filter button ----
    print("[FILTER] Clicking Filter button...")
    try:
        page.click('#btn-filter-mobile, a#btn-filter-mobile, a.button#btn-filter-mobile')
        page.wait_for_load_state('networkidle', timeout=15000)
        time.sleep(3)
        print("[FILTER] Filters applied successfully.")
    except PlaywrightTimeoutError:
        print("[FILTER] WARNING: Filter button click timed out.")


# ---- Step 3: Navigate to jobs page, apply filters, and scrape ALL pages ----
def scrape_job_list(page):
    print(f"\nNavigating to jobs page: {BASE_URL}/jobs/")
    page.goto(f'{BASE_URL}/jobs/', wait_until='networkidle', timeout=30000)
    time.sleep(3)

    apply_filters(page)

    all_jobs_html = []
    current_page = 1

    while True:
        print(f"\n[PAGINATION] Scraping page {current_page}...")
        html = page.content()
        all_jobs_html.append(html)

        # Check for next page button
        next_btn = page.locator('a[rel="next"], .pagination .next, a:has-text("Volgende"), a:has-text("Next")')
        if next_btn.count() > 0:
            print(f"[PAGINATION] Moving to page {current_page + 1}...")
            next_btn.first.click()
            page.wait_for_load_state('networkidle', timeout=15000)
            time.sleep(2)
            current_page += 1
        else:
            print(f"[PAGINATION] No more pages. Total pages scraped: {current_page}")
            break

    return all_jobs_html


# ---- Step 4: Parse job list from HTML pages ----
def parse_job_list(html_pages):
    all_jobs = []

    for html in html_pages:
        soup = BeautifulSoup(html, 'html.parser')
        job_items = soup.find_all('li', class_='job-abstract')

        for item in job_items:
            h3 = item.find('h3')
            h4 = item.find('h4')
            h5 = item.find('h5')

            title_tag = h3.find('a') if h3 else None
            title = title_tag.get_text(strip=True) if title_tag else ''

            href = title_tag['href'] if title_tag and title_tag.has_attr('href') else ''
            link = BASE_URL + href if href.startswith('/') else href

            # ---- Extract job_id from URL ----
            # e.g. hoofd-bedrijfsbureau-19678.html → 19678
            job_id_match = re.search(r'-(\d+)\.html$', link)
            job_id = job_id_match.group(1) if job_id_match else ''

            span_tag = h3.find('span') if h3 else None
            validity_text = span_tag.get_text(strip=True) if span_tag else ''

            company = h4.get_text(strip=True) if h4 else None

            salary_tag = h5.find('span', class_='salary') if h5 else None
            salary = salary_tag.get_text(strip=True) if salary_tag else None
            if salary_tag:
                salary_tag.decompose()

            location = h5.get_text(strip=True) if h5 else ''
            new_job = validity_text.lower() == 'new'
            location_text = location.lower()
            is_remote = True if 'remote' in location_text or 'hybrid' in location_text or 'hybride' in location_text else None

            all_jobs.append({
                "job_id": job_id,
                "title": title,
                "company": company,
                "location": location,
                "link": link,
                "validity_text": validity_text,
                "snippet": f"{title} - {company} - {location}",
                "description": "",
                "salary": salary,
                "job_types": None,
                "remote": is_remote,
                "benefits": None,
                "company_rating": None,
                "review_count": None,
                "new_job": new_job,
                "source": "lintberg.com",
                "date": datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                
            })

    print(f"Found {len(all_jobs)} jobs total across all pages")
    return all_jobs


# ---- Step 5: Scrape individual job detail page ----
def scrape_job_detail(page, job_url):
    page.goto(job_url, wait_until='networkidle', timeout=30000)
    time.sleep(2)
    return page.content()


# ---- Step 6: Parse job detail HTML ----
def parse_job_detail(html):
    soup = BeautifulSoup(html, 'html.parser')

    if soup.find('body', class_='non-member'):
        print("  WARNING: Still hitting login wall for this job")
        return {}

    description_tag = (
        soup.find('div', id='job-description') or
        soup.find('section', id='job-description') or
        soup.find('div', class_='job-description') or
        soup.find('article', class_='job')
    )
    description = description_tag.get_text(separator=' ', strip=True) if description_tag else ''

    job_types_tag = soup.find('span', class_='job-type') or soup.find('div', class_='job-type')
    job_types = job_types_tag.get_text(strip=True) if job_types_tag else None

    benefits_tag = soup.find('ul', class_='benefits') or soup.find('div', class_='benefits')
    benefits = benefits_tag.get_text(separator=', ', strip=True) if benefits_tag else None

    # ---- Check job status from warnings section ----
    # status = 'active'  # Default status
    # warnings_section = soup.find('section', id='warnings')
    
    # if warnings_section:
    #     # Look for the <em> tag containing closure message
    #     em_tag = warnings_section.find('em')
    #     if em_tag:
    #         em_text = em_tag.get_text(strip=True).lower()
    #         # Check if it contains "closed" or "gesloten" (Dutch)
    #         if 'closed' in em_text or 'gesloten' in em_text:
    #             status = 'expired'
    #             print(f"  Job marked as expired: {em_text}")

    # return {
    #     'description': description,
    #     'job_types': job_types,
    #     'benefits': benefits,
    #     'status': status,
    # }

    return {
        'description': description,
        'job_types': job_types,
        'benefits': benefits,
        'status': 'active',
    }




# ---- MAIN ----
def scrape_lintberg_jobs():
    """
    Main function to scrape Lintberg jobs.
    Returns a list of job dictionaries.
    """
    with sync_playwright() as playwright:
        browser, context, page = login_and_get_context(playwright)

        try:
            # ---- Load existing jobs to check for duplicates ----
            existing_ids, existing_jobs = load_existing_job_ids(OUTPUT_FILE)
            
            # Create a map of job_id -> job for easy lookup
            # existing_jobs_map = {job['job_id']: job for job in existing_jobs if job.get('job_id')}
            
            # ---- Scrape job listing with filters ----
            html_pages = scrape_job_list(page)
            all_jobs = parse_job_list(html_pages)

            # ---- Separate new jobs and jobs to check for status updates ----
            # new_jobs = []
            # jobs_to_check_status = []
            
            # for job in all_jobs:
            #     if job['job_id'] in existing_ids:
            #         jobs_to_check_status.append(job)
            #     else:
            #         new_jobs.append(job)

            # print(f"\n[DEDUP] Total found: {len(all_jobs)} | New: {len(new_jobs)} | Existing (status check): {len(jobs_to_check_status)}")

            # # ---- Check status for existing jobs ----
            # status_updated_count = 0
            # if jobs_to_check_status:
            #     print(f"\n[STATUS CHECK] Checking status for {len(jobs_to_check_status)} existing jobs...")
            #     for i, job in enumerate(jobs_to_check_status):
            #         if job['link']:
            #             print(f"Checking status {i+1}/{len(jobs_to_check_status)}: {job['title']} (ID: {job['job_id']})")
            #             detail_html = scrape_job_detail(page, job['link'])
            #             details = parse_job_detail(detail_html)
                        
            #             if details and 'status' in details:
            #                 existing_job = existing_jobs_map.get(job['job_id'])
            #                 old_status = existing_job.get('status', 'active')
            #                 new_status = details['status']
                            
            #                 # Update status if changed
            #                 if old_status != new_status:
            #                     existing_job['status'] = new_status
            #                     status_updated_count += 1
            #                     print(f"  STATUS CHANGED: {old_status} → {new_status}")
            #                 else:
            #                     print(f"  Status unchanged: {new_status}")

            # # ---- Ensure all existing jobs have a status field ----
            # for job in existing_jobs:
            #     if 'status' not in job or job['status'] is None:
            #         job['status'] = 'active'

            # ---- Separate new jobs from existing ----
            new_jobs = [job for job in all_jobs if job['job_id'] not in existing_ids]

            print(f"\n[DEDUP] Total found: {len(all_jobs)} | New: {len(new_jobs)} | Skipped (existing): {len(all_jobs) - len(new_jobs)}")

            # ---- Ensure all existing jobs have a status field ----
            for job in existing_jobs:
                if 'status' not in job or job['status'] is None:
                    job['status'] = 'active'

            # ---- Scrape details for new jobs only ----
            if new_jobs:
                print(f"\n[NEW JOBS] Scraping details for {len(new_jobs)} new jobs...")
                for i, job in enumerate(new_jobs):
                    if job['link']:
                        print(f"Scraping detail {i+1}/{len(new_jobs)}: {job['title']} (ID: {job['job_id']})")
                        detail_html = scrape_job_detail(page, job['link'])
                        details = parse_job_detail(detail_html)
                        if not details:
                            print(f"  SKIPPING: Login wall detected")
                        else:
                            job.update(details)
                    
                    # Ensure new jobs also have a status field (default to active)
                    if 'status' not in job:
                        job['status'] = 'active'

            # ---- Merge new jobs with existing and save ----
            all_combined = existing_jobs + new_jobs
            with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
                json.dump(all_combined, f, indent=2, ensure_ascii=False)

            print(f"\n✅ Done!")
            print(f"   - New jobs added: {len(new_jobs)}")
            # print(f"   - Status updates: {status_updated_count}")
            print(f"   - Total jobs in file: {len(all_combined)}")
            
            if new_jobs:
                print("\nSample first new job:")
                print(json.dumps(new_jobs[0], indent=2, ensure_ascii=False))
            
            return all_combined

        finally:
            # ---- Save session before closing ----
            context.storage_state(path='lintberg_session.json')
            print("\nSession saved. Closing browser...")
            browser.close()




if __name__ == '__main__':
    scrape_lintberg_jobs()