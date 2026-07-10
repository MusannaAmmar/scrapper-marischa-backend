import os
import json
import re
import requests
from urllib.parse import urlencode
from dotenv import load_dotenv
from datetime import datetime, timezone

load_dotenv()



class ThermoFischerScraper:
    ZENROWS_API_KEY = os.getenv("ZENROWS")
    ZENROWS_API = "https://api.zenrows.com/v1/"

    def __init__(self, output_file="json_files/thermo_fischer_jobs.json"):
        self.url = "https://jobs.thermofisher.com/global/en/c/operations-jobs"
        self.output_file = output_file
        self.source_name = "Thermo Fisher Scientific"
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
    def _extract_ddo_json(html):
        # phApp.ddo contains eagerLoadRefineSearch jobs payload.
        marker = "phApp.ddo = "
        start = html.find(marker)
        if start == -1:
            return None
        start += len(marker)

        # DDO blob ends before phApp.experimentData assignment.
        end_marker = "; phApp.experimentData"
        end = html.find(end_marker, start)
        if end == -1:
            return None

        raw = html[start:end].strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _extract_link_config_from_html(html):
        base_match = re.search(r'"baseUrl":"([^"]+)"', html)
        route_match = re.search(r'"forwardApply":"([^"]+)"', html)

        if not base_match:
            return None, None

        base_url = base_match.group(1)
        if not base_url.endswith("/"):
            base_url += "/"

        apply_route = route_match.group(1) if route_match else "hvhapply"
        return base_url, apply_route

    @staticmethod
    def _is_target_job(job):
        category = str(job.get("category", "")).strip().lower()

        location_texts = [
            str(job.get("country", "")),
            str(job.get("location", "")),
            str(job.get("cityStateCountry", "")),
        ]

        for loc in job.get("multi_location", []) or []:
            location_texts.append(str(loc))

        for loc_obj in job.get("multi_location_array", []) or []:
            if isinstance(loc_obj, dict):
                location_texts.append(str(loc_obj.get("location", "")))

        location_blob = " | ".join(location_texts).lower()
        is_netherlands = "netherlands" in location_blob

        return category == "operations" and is_netherlands

    @staticmethod
    def _extract_job_id(job):
        # IDs look like: R-01338949
        job_id = str(job.get("jobId", "")).strip()
        return job_id if re.match(r"^R-\d+$", job_id) else None

    def _extract_filtered_jobs(self, html):
        ddo = self._extract_ddo_json(html)
        if not ddo:
            print("Could not parse phApp.ddo JSON payload.")
            return []

        jobs = (
            ddo.get("eagerLoadRefineSearch", {})
            .get("data", {})
            .get("jobs", [])
        )

        if not isinstance(jobs, list):
            return []

        filtered = []
        for job in jobs:
            if not isinstance(job, dict):
                continue
            if self._is_target_job(job):
                filtered.append(job)
        return filtered

    def _extract_jobs_from_html(self, html):
        ddo = self._extract_ddo_json(html)
        if not ddo:
            return []

        jobs = (
            ddo.get("eagerLoadRefineSearch", {})
            .get("data", {})
            .get("jobs", [])
        )
        return jobs if isinstance(jobs, list) else []

    def _build_page_url(self, start_index):
        params = {
            "from": start_index,
            "s": 1,
        }
        return f"{self.url}?{urlencode(params)}"

    def _fetch_all_operation_jobs(self, page_size=10, max_pages=150):
        all_jobs = []
        seen_job_ids = set()

        for page in range(max_pages):
            start_index = page * page_size
            page_url = self._build_page_url(start_index)
            html = self.fetch_via_zenrows(page_url)
            if not html:
                print(f"  [warn] Empty HTML for page start={start_index}")
                break

            jobs = self._extract_jobs_from_html(html)
            if not jobs:
                break

            added_this_page = 0
            for job in jobs:
                if not isinstance(job, dict):
                    continue
                job_id = str(job.get("jobId", "")).strip()
                if not job_id:
                    continue
                if job_id in seen_job_ids:
                    continue
                seen_job_ids.add(job_id)
                all_jobs.append(job)
                added_this_page += 1

            print(
                f"  [page {page + 1}] start={start_index} "
                f"raw={len(jobs)} added={added_this_page} total_unique={len(all_jobs)}"
            )

            # Last page usually has fewer than page_size items.
            if len(jobs) < page_size:
                break

        return all_jobs

    def parse_job_listings(self):
        print("\nFetching Thermo Fisher job listings...")
        print(f"Source URL: {self.url}")

        html = self.fetch_via_zenrows(self.url)
        if not html:
            return []

        base_url, apply_route = self._extract_link_config_from_html(html)
        if not base_url:
            print("Could not find baseUrl in page JSON.")
            return self.jobs

        all_jobs = self._fetch_all_operation_jobs(page_size=10, max_pages=150)
        if not all_jobs:
            print("No jobs found from Operations pagination.")
            return self.jobs

        filtered_jobs = [job for job in all_jobs if self._is_target_job(job)]
        if not filtered_jobs:
            print("No Operations jobs found for Netherlands.")
            return self.jobs

        print(
            f"Found {len(filtered_jobs)} filtered job(s) (Operations + Netherlands) "
            f"from {len(all_jobs)} unique Operations job(s)"
        )

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

        for i, row in enumerate(filtered_jobs, start=1):
            job_id = self._extract_job_id(row)
            seq_no = str(row.get("jobSeqNo", "")).strip()
            if not job_id or not seq_no:
                failed_count += 1
                print(f"  [{i}/{len(filtered_jobs)}] Skipped: missing job_id or jobSeqNo")
                continue

            seen_ids.add(job_id)

            if job_id in existing_index:
                skipped_existing_count += 1
                print(f"  [{i}/{len(filtered_jobs)}] Skipped existing job_id={job_id}")
                continue

            link = f"{base_url}{apply_route}?jobSeqNo={seq_no}"

            job = {
                "title": str(row.get("title", "")).strip(),
                "job_id": job_id,
                "link": link,
                "location": str(row.get("location", "")).strip(),
                "description": str(row.get("descriptionTeaser", "")).strip(),
                "status": "active",
                "source": self.source_name,
                "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            }

            self.jobs.append(job)
            existing_index[job_id] = len(self.jobs) - 1
            new_count += 1
            print(f"  [{i}/{len(filtered_jobs)}] + New: {job_id} | {job['title'][:70]}")

        expired_count = 0
        for job in self.jobs:
            if str(job.get("source", "")).lower() == self.source_name.lower():
                jid = str(job.get("job_id") or "")
                if jid and jid not in seen_ids:
                    job["status"] = "expired"
                    expired_count += 1

        self._save_jobs()

        print("\n" + "=" * 60)
        print("Thermo Fisher scraping done")
        print("Filter applied  : category=Operations, country=Netherlands")
        print(f"New jobs        : {new_count}")
        print(f"Updated jobs    : {updated_count}")
        print(f"Skipped existing: {skipped_existing_count}")
        print(f"Expired marked  : {expired_count}")
        print(f"Failed/skipped  : {failed_count}")
        print(f"Total stored    : {len(self.jobs)}")
        print("=" * 60)

        return self.jobs

    def run(self):
        return self.parse_job_listings()


if __name__ == "__main__":
    scraper = ThermoFischerScraper()
    data = scraper.run()
    print(f"Saved {len(data)} jobs to {scraper.output_file}")
