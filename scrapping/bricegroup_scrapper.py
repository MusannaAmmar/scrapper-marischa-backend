from bs4 import BeautifulSoup
import requests
import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, parse_qs
from dotenv import load_dotenv

load_dotenv()


class BriceGroupScrapper:
    ZENROWS_API = "https://api.zenrows.com/v1/"
    ZENROWS_API_KEY = os.getenv("ZENROWS")

    def __init__(self, output_file="json_files/bricegroup_jobs.json"):
        self.output_file = output_file
        self.url = "https://www.brice.se/en/job-openings/"
        self.jobs = self._load_existing_jobs()

    def _load_existing_jobs(self):
        if os.path.exists(self.output_file):
            with open(self.output_file, "r", encoding="utf-8") as f:
                jobs = json.load(f)
            print(f"Loaded {len(jobs)} existing jobs from {self.output_file}")
            return jobs
        return []

    def _save_jobs(self):
        with open(self.output_file, "w", encoding="utf-8") as f:
            json.dump(self.jobs, f, indent=2, ensure_ascii=False)

    def fetch_via_zenrows(self, url):
        params = {
            "url": url,
            "apikey": self.ZENROWS_API_KEY,
            "js_render": "true",
            "premium_proxy": "true",
        }

        try:
            response = requests.get(self.ZENROWS_API, params=params, timeout=90)
            if response.status_code == 200:
                return response.text
            print(f"ZenRows error: {response.status_code} for URL: {url}")
            return None
        except Exception as e:
            print(f"ZenRows request failed for URL: {url} with error: {e}")
            return None

    @staticmethod
    def _extract_job_id(link):
        # Preferred: query param id
        qs = parse_qs(urlparse(link).query)
        if "id" in qs and qs["id"]:
            return qs["id"][0]

        # Fallback
        m = re.search(r"id=(\d+)", link)
        return m.group(1) if m else None

    @staticmethod
    def _extract_location(details_soup):
        icon = details_soup.select_one(
            "svg.fa-map-marker-alt, i.fa-map-marker-alt, i.fal.fa-map-marker-alt"
        )
        if not icon:
            return ""

        parent = icon.find_parent(["div", "li", "p", "span"])
        if not parent:
            return ""

        parts = [
            s.strip().strip('"')
            for s in parent.stripped_strings
            if s.strip().strip('"')
        ]
        return parts[-1] if parts else ""

    @staticmethod
    def _extract_description(details_soup):
        p_tags = details_soup.find_all("p")
        if len(p_tags) <= 2:
            return ""

        # Your requirement: skip first and last p
        middle_p_tags = p_tags[1:-1]

        lines = []
        for p in middle_p_tags:
            text = p.get_text(" ", strip=True)
            if not text:
                continue

            # Stop before footer/contact blocks
            if "Fill in the contact form below" in text:
                break
            if "Data Protection Policy" in text:
                break
            if "Brice Group AB" in text:
                break

            lines.append(text)

        return "\n".join(lines).strip()

    def parse_job_listings(self):
        print("\nFetching Brice Group job listings...")
        print(f"Source URL: {self.url}")

        html = self.fetch_via_zenrows(self.url)
        if not html:
            print("No listing HTML returned.")
            return []

        soup = BeautifulSoup(html, "html.parser")
        first_div = soup.find("div", id="ponty-replace")
        if not first_div:
            print("Could not find listing container #ponty-replace.")
            return []

        hrefs = []
        for a in first_div.find_all("a", href=True):
            href = a["href"].strip()
            if not href:
                continue
            full_link = urljoin("https://www.brice.se", href)
            if "id=" in full_link:
                hrefs.append(full_link)

        # Deduplicate links while preserving order
        hrefs = list(dict.fromkeys(hrefs))
        print(f"Found {len(hrefs)} unique detail links")

        existing_index = {
            str(job.get("job_id")): idx
            for idx, job in enumerate(self.jobs)
            if job.get("job_id")
        }
        seen_ids = set()

        new_count = 0
        updated_count = 0
        skipped_existing_count = 0
        failed_count = 0

        for i, link in enumerate(hrefs, start=1):
            job_id = self._extract_job_id(link)
            if not job_id:
                failed_count += 1
                print(f"  [{i}/{len(hrefs)}] Skipped: no job_id in link {link}")
                continue

            seen_ids.add(job_id)

            if job_id in existing_index:
                skipped_existing_count += 1
                print(f"  [{i}/{len(hrefs)}] Skipped existing job_id={job_id}")
                continue

            print(f"  [{i}/{len(hrefs)}] Processing job_id={job_id}")
            details_page = self.fetch_via_zenrows(link)
            if not details_page:
                failed_count += 1
                print(f"    [warn] Failed to fetch detail page for {job_id}")
                continue

            details_soup = BeautifulSoup(details_page, "html.parser")
            h1 = details_soup.find("h1")
            title = h1.get_text(strip=True) if h1 else ""

            location = self._extract_location(details_soup)
            description = self._extract_description(details_soup)

            job = {
                "title": title,
                "job_id": job_id,
                "link": link,
                "apply_link": link,
                "location": location,
                "description": description,
                "status": "active",
                "source": "Brice Group",
                "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            }

            self.jobs.append(job)
            existing_index[job_id] = len(self.jobs) - 1
            new_count += 1
            print(f"    + New: {title[:80]}")

        # Mark old Brice jobs not seen this run as expired
        expired_count = 0
        for j in self.jobs:
            if str(j.get("source", "")).lower() == "brice group":
                jid = str(j.get("job_id") or "")
                if jid and jid not in seen_ids:
                    j["status"] = "expired"
                    expired_count += 1

        self._save_jobs()
        print("\n" + "=" * 60)
        print("Brice Group scraping done")
        print(f"New jobs       : {new_count}")
        print(f"Updated jobs   : {updated_count}")
        print(f"Skipped existing: {skipped_existing_count}")
        print(f"Expired marked : {expired_count}")
        print(f"Failed/skipped : {failed_count}")
        print(f"Total stored   : {len(self.jobs)}")
        print("=" * 60)
        return self.jobs

    def run(self):
        return self.parse_job_listings()


if __name__ == "__main__":
    scraper = BriceGroupScrapper()
    data = scraper.run()
    print(f"Saved {len(data)} jobs to {scraper.output_file}")