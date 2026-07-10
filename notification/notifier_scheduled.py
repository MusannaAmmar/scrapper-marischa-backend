import os
import json
from datetime import datetime, timezone
from pydantic import BaseModel
from pinecone import Pinecone
from dotenv import load_dotenv

# ---- Import all workflow components ----
from scrapping.linkedin_scrapper import LinkedinScraper
from scrapping.indeed_scrapper import IndeedScraper
from scrapping.lintberg_scrapper import scrape_lintberg_jobs
from database.upsert_vectors import (
    create_and_upsert_linkedin_embeddings,
    create_and_upsert_indeed_embeddings,
    create_and_upsert_lintberg_embeddings,
)
from analyzer.agent import JobMatchingAgent
from database.pinecone import EmbeddingService
from notification.email_notification import send_notification_email
from utils import embedding_service

load_dotenv()

RECIPIENT_EMAIL      = os.getenv("RECIPIENT_EMAIL")
LINKEDIN_SEARCH_URL  = os.getenv("LINKEDIN_SEARCH_URL")
INDEED_SEARCH_URL    = os.getenv("INDEED_SEARCH_URL")

# ---- Pinecone client ----
pc    = Pinecone(api_key=os.getenv("PINECONE_API_KEY"))
index = pc.Index(os.getenv("PINECONE_INDEX_NAME", "scrapped-data"))

# ---- Global scheduler state ----
scheduler_state = {
    "is_running":         False,
    "scheduled_time":     None,
    "recipient_email":    None,
    "linkedin_search_url": None,
    "indeed_search_url":  None,
    "last_run_at":        None,
    "last_run_status":    None,
    "logs":               [],
}

# ---- Fields to skip in email ----
SKIP_FIELDS = {
    "remote", "salary", "saved_by_email", "saved_by_user_id", "matched_criteria",
    "saved_by_username", "snippet", "saved_at", "benefits", "job_types", "company_rating",
    "review_count", "validity_text",'ID','status','date_posted_text','id','vector_id'
}





def get_source_namespace(source: str) -> str:
    """Map job source to its Pinecone namespace."""
    source_map = {
        "linkedin":   "linkedin-jobs",
        "indeed":     "indeed-jobs",
        "lintberg":   "lintberg-jobs",
    }
    source = (source or "").lower()
    for key, namespace in source_map.items():
        if key in source:
            return namespace
    return None



# ────────────────────────────────────────────
# Periodic Cleanup: Sync expired jobs to results
# ────────────────────────────────────────────


def run_expired_jobs_cleanup():
    """
    Periodic cleanup task:
    - Fetches all jobs from results namespace
    - Cross-references each job's status in its source namespace using job_id
    - If source status is 'expired', updates the result metadata status to 'expired'
    - Only updates status field, nothing else
    """
    log("=" * 50)
    log("🧹 CLEANUP: Starting expired jobs cleanup...")

    try:
        # Step 1: Fetch all jobs from results namespace
        results = embedding_service.search_similar(
            "job match candidate",
            top_k=10000,
            namespace="results"
        )
        matches = results if isinstance(results, list) else results.get('matches', [])

        if not matches:
            log("[CLEANUP] No jobs found in results namespace.")
            return

        log(f"[CLEANUP] Found {len(matches)} job(s) in results namespace to check.")

        expired_count         = 0
        already_expired_count = 0
        not_found_count       = 0
        active_count          = 0

        for match in matches:
            metadata         = match.get('metadata', {})
            result_vector_id = match.get('id', '')
            job_id           = metadata.get('job_id', '')
            source           = metadata.get('source', '')
            current_status   = metadata.get('status', 'active')

            # Skip if already marked expired in results
            if current_status == 'expired':
                already_expired_count += 1
                continue

            if not job_id:
                log(f"[CLEANUP] Skipping vector {result_vector_id} — no job_id in metadata.")
                continue

            # Step 2: Determine source namespace from job source
            source_namespace = get_source_namespace(source)
            if not source_namespace:
                log(f"[CLEANUP] Unknown source '{source}' for job_id: {job_id} — skipping.")
                continue

            # Step 3: Build source vector ID
            # Vector IDs follow pattern: linkedin_job_{job_id}, indeed_job_{job_id}, etc.
            source_prefix_map = {
                "linkedin-jobs":   "linkedin_job",
                "indeed-jobs":     "indeed_job",
                "lintberg-jobs":   "lintberg_job",
            }
            prefix           = source_prefix_map.get(source_namespace, "job")
            source_vector_id = f"{prefix}_{job_id}"

            try:
                fetch_response = index.fetch(
                    ids=[source_vector_id],
                    namespace=source_namespace
                )

                # Pinecone returns an object not a dict
                source_vectors = getattr(fetch_response, 'vectors', None) or {}

                if not source_vectors or source_vector_id not in source_vectors:
                    log(f"[CLEANUP] Job {job_id} (vector: {source_vector_id}) not found in {source_namespace} — skipping.")
                    not_found_count += 1
                    continue  # Do NOT mark expired just because vector not found

                source_vector   = source_vectors[source_vector_id]
                source_metadata = getattr(source_vector, 'metadata', None) or {}
                source_status   = source_metadata.get('status', 'active')

            except Exception as fetch_err:
                log(f"[CLEANUP] Could not fetch job_id {job_id} from {source_namespace}: {fetch_err}")
                continue

            # Step 4: Only update if source says expired but result still says active
            if source_status == 'expired' and current_status != 'expired':
                try:
                    index.update(
                        id=result_vector_id,
                        set_metadata={"status": "expired"},
                        namespace="results"
                    )
                    log(f"[CLEANUP] ✓ Marked job_id {job_id} as expired in results.")
                    expired_count += 1

                except Exception as update_err:
                    log(f"[CLEANUP] Failed to update job_id {job_id}: {update_err}")
            else:
                active_count += 1

        log(
            f"[CLEANUP SUMMARY] "
            f"Total checked: {len(matches)} | "
            f"Newly expired: {expired_count} | "
            f"Already expired: {already_expired_count} | "
            f"Not found in source: {not_found_count} | "
            f"Still active: {active_count}"
        )
        log("✅ Cleanup completed.")

    except Exception as e:
        log(f"❌ Cleanup failed: {e}")


# ────────────────────────────────────────────
# Helper: Logging
# ────────────────────────────────────────────

def log(message: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry     = f"[{timestamp}] {message}"
    print(entry)
    scheduler_state["logs"].append(entry)
    if len(scheduler_state["logs"]) > 500:
        scheduler_state["logs"] = scheduler_state["logs"][-500:]


# ────────────────────────────────────────────
# Helper: Sanitize metadata for Pinecone
# ────────────────────────────────────────────

def sanitize_metadata(metadata: dict) -> dict:
    """Pinecone only accepts str, int, float, bool, list[str]."""
    sanitized = {}
    for key, value in metadata.items():
        if value is None:
            sanitized[key] = ''
        elif isinstance(value, (str, int, float, bool)):
            sanitized[key] = value
        elif isinstance(value, list):
            sanitized[key] = [str(v) for v in value]
        else:
            sanitized[key] = str(value)
    return sanitized


# ────────────────────────────────────────────
# Helper: Fetch deleted job IDs
# ────────────────────────────────────────────

def fetch_deleted_job_ids() -> set:
    """Fetch job_ids of jobs that were deleted by users."""
    try:
        results = embedding_service.search_similar(
            "deleted job",
            top_k=10000,
            namespace="deleted-jobs"
        )
        matches = results if isinstance(results, list) else results.get('matches', [])
        ids = {m.get('metadata', {}).get('job_id') for m in matches if m.get('metadata', {}).get('job_id')}
        log(f"[DELETED-IDS] Found {len(ids)} deleted job IDs")
        return ids
    except Exception as e:
        log(f"[DELETED-IDS] Warning: {e}")
        return set()


# ────────────────────────────────────────────
# Helper: Fetch existing result job IDs
# ────────────────────────────────────────────

def fetch_existing_result_job_ids() -> set:
    """Fetch job_ids already saved in the results namespace."""
    try:
        results = embedding_service.search_similar(
            "job match result",
            top_k=10000,
            namespace="results"
        )
        matches = results if isinstance(results, list) else results.get('matches', [])
        ids = {m.get('metadata', {}).get('job_id') for m in matches if m.get('metadata', {}).get('job_id')}
        log(f"[EXISTING-RESULTS] Found {len(ids)} existing result job IDs")
        return ids
    except Exception as e:
        log(f"[EXISTING-RESULTS] Warning: {e}")
        return set()


# ────────────────────────────────────────────
# Helper: Fetch already sent job IDs
# ────────────────────────────────────────────

def fetch_already_sent_job_ids() -> set:
    """Fetch vector_ids that were already sent via email."""
    try:
        results = embedding_service.search_similar(
            "sent job notification",
            top_k=10000,
            namespace="sended-jobs"
        )
        matches = results if isinstance(results, list) else results.get('matches', [])
        ids = {m.get('metadata', {}).get('vector_id') for m in matches if m.get('metadata', {}).get('vector_id')}
        log(f"[SENT-IDS] Found {len(ids)} already sent vector IDs")
        return ids
    except Exception as e:
        log(f"[SENT-IDS] Warning: {e}")
        return set()


# ────────────────────────────────────────────
# Helper: Fetch latest jobs from results namespace
# ────────────────────────────────────────────

def fetch_latest_jobs_from_results(top_k: int = 50) -> list:
    """Fetch latest matched jobs from the results namespace."""
    try:
        results = embedding_service.search_similar(
            "job match candidate",
            top_k=top_k,
            namespace="results"
        )
        matches = results if isinstance(results, list) else results.get('matches', [])
        jobs = []
        for match in matches:
            metadata  = match.get('metadata', {})
            vector_id = match.get('id', '')
            jobs.append({**metadata, 'vector_id': vector_id})
        log(f"[FETCH-RESULTS] Fetched {len(jobs)} jobs from results namespace")
        return jobs
    except Exception as e:
        log(f"[FETCH-RESULTS] Warning: {e}")
        return []


# ────────────────────────────────────────────
# Helper: Save sent jobs to sended-jobs namespace
# ────────────────────────────────────────────

def save_sent_jobs_to_db(jobs: list):
    """Save sent job vector_ids to sended-jobs namespace to prevent re-sending."""
    try:
        texts = [
            f"{j.get('title', '')} {j.get('company', '')} {j.get('candidate_name', '')}"
            for j in jobs
        ]
        embeddings = embedding_service.generate_embeddings_batch(texts)
        vectors    = []

        for job, embedding in zip(jobs, embeddings):
            if embedding is None:
                continue
            vector_id = job.get('vector_id', '')
            vectors.append({
                "id":        f"sent_{vector_id}",
                "embedding": embedding,
                "metadata":  sanitize_metadata({
                    "vector_id":     vector_id,
                    "job_id":        job.get('job_id', ''),
                    "title":         job.get('title', ''),
                    "company":       job.get('company', ''),
                    "candidate_name":job.get('candidate_name', ''),
                    "sent_at":       datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }),
            })

        if vectors:
            embedding_service.upsert_vectors(vectors, namespace="sended-jobs")
            log(f"✓ Saved {len(vectors)} sent job(s) to sended-jobs namespace")

    except Exception as e:
        log(f"[SAVE-SENT] Warning: {e}")


# ────────────────────────────────────────────
# Helper: Format email body
# ────────────────────────────────────────────

def format_jobs_email(jobs: list) -> str:
    """Format matched jobs into a readable email body."""
    lines = ["Here are the latest job listings matched for your candidates:\n"]

    for i, job in enumerate(jobs, 1):
        lines.append("=" * 60)
        lines.append(f"\nJob #{i}")
        lines.append("-" * 40)

        for key, value in sorted(job.items()):
            if key in SKIP_FIELDS or not value:
                continue
            label = key.replace("_", " ").title()
            lines.append(f"{label}: {value}")

        lines.append("")

    lines.append("=" * 60)
    lines.append("\nThis is an automated notification from Job Scraping Agent")
    return "\n".join(lines)


def format_no_jobs_email() -> str:
    """Format email body when no new jobs are found."""
    return (
        "No new job matches were found in this run.\n\n"
        "The agent searched all sources "
        "but found no new matches.\n\n"
        "This is an automated notification from Job Scraping Agent"
    )


# ────────────────────────────────────────────
# Step 1: Run all scrapers
# ────────────────────────────────────────────

def run_all_scrapers(linkedin_search_url: str, indeed_search_url: str):
    log("=" * 50)
    log("STEP 1: Running all scrapers...")

    # LinkedIn
    try:
        log("Running LinkedIn scraper...")
        scraper = LinkedinScraper()
        scraper.run(linkedin_search_url)
        log("✓ LinkedIn scraper completed.")
    except Exception as e:
        log(f"✗ LinkedIn scraper failed: {e}")

    # Indeed
    try:
        log("Running Indeed scraper...")
        scraper = IndeedScraper()
        scraper.run(indeed_search_url)
        log("✓ Indeed scraper completed.")
    except Exception as e:
        log(f"✗ Indeed scraper failed: {e}")

    # Lintberg
    try:
        log("Running Lintberg scraper...")
        scrape_lintberg_jobs()
        log("✓ Lintberg scraper completed.")
    except Exception as e:
        log(f"✗ Lintberg scraper failed: {e}")



# ────────────────────────────────────────────
# Step 2: Upsert vectors to Pinecone
# ────────────────────────────────────────────

def run_all_upserts():
    log("=" * 50)
    log("STEP 2: Upserting vectors to Pinecone...")

    try:
        create_and_upsert_linkedin_embeddings()
        log("✓ LinkedIn vectors upserted.")
    except Exception as e:
        log(f"✗ LinkedIn upsert failed: {e}")

    try:
        create_and_upsert_indeed_embeddings()
        log("✓ Indeed vectors upserted.")
    except Exception as e:
        log(f"✗ Indeed upsert failed: {e}")

    try:
        create_and_upsert_lintberg_embeddings()
        log("✓ Lintberg vectors upserted.")
    except Exception as e:
        log(f"✗ Lintberg upsert failed: {e}")

 

# ────────────────────────────────────────────
# Step 3: Run agent and save results
# ────────────────────────────────────────────

def run_agent_and_save_results() -> list:
    """
    Runs the AI matching agent, filters out deleted/existing jobs,
    saves NEW matches to results namespace.
    Returns list of vector_ids saved in THIS run only.
    """
    log("=" * 50)
    log("STEP 3: Running AI job matching agent...")

    try:
        agent   = JobMatchingAgent()
        matches = agent.find_matches()

        if not matches:
            log("⚠ No matches found by agent.")
            return []

        log(f"✓ Agent found {len(matches)} match(es).")

        deleted_job_ids  = fetch_deleted_job_ids()
        existing_job_ids = fetch_existing_result_job_ids()

        new_matches      = []
        skipped_deleted  = 0
        skipped_existing = 0

        for match in matches:
            job_id = str(match.get("job", {}).get("job_id", ""))

            if job_id and job_id in deleted_job_ids:
                log(f"[DELETED-FILTER] Skipping deleted job_id: {job_id}")
                skipped_deleted += 1
                continue

            if job_id and job_id in existing_job_ids:
                log(f"[DEDUP] Skipping already saved job_id: {job_id}")
                skipped_existing += 1
                continue

            new_matches.append(match)

        log(
            f"[FILTER SUMMARY] Total: {len(matches)} | "
            f"Skipped (deleted): {skipped_deleted} | "
            f"Skipped (existing): {skipped_existing} | "
            f"New to save: {len(new_matches)}"
        )

        if not new_matches:
            log("[FILTER] No new results to save.")
            return []

        log(f"Saving {len(new_matches)} new result(s) to 'results' namespace...")

        texts = []
        for match in new_matches:
            job  = match.get("job", {})
            text = " ".join(filter(None, [
                job.get("title", ""),
                job.get("company", ""),
                job.get("location", ""),
                job.get("description_preview", ""),
                match.get("reasoning", ""),
            ]))
            texts.append(text)

        embeddings       = embedding_service.generate_embeddings_batch(texts)
        vectors          = []
        saved_vector_ids = []  # ← only IDs saved in THIS run

        for i, (match, embedding) in enumerate(zip(new_matches, embeddings)):
            if embedding is None:
                log(f"[EMBEDDING] Skipping match {i} — embedding failed.")
                continue

            job       = match.get("job", {})
            job_id    = job.get('job_id', i)
            vector_id = f"result_{job_id}_{int(datetime.now().timestamp())}"

            raw_metadata = {
                **job,
                "candidate_name":   match.get("candidate_name", ""),
                "match_score":      match.get("match_score", 0),
                "sector":           match.get("sector", ""),
                "matched_criteria": match.get("matched_criteria", []),
                "reasoning":        match.get("reasoning", ""),
            }

            vectors.append({
                "id":        vector_id,
                "embedding": embedding,
                "metadata":  sanitize_metadata(raw_metadata),
            })
            saved_vector_ids.append(vector_id)

        if vectors:
            embedding_service.upsert_vectors(vectors, namespace="results")
            log(f"✓ Saved {len(vectors)} new result(s) to 'results' namespace.")

        # ← return ONLY this run's vector ids
        return saved_vector_ids

    except Exception as e:
        log(f"✗ Agent failed: {e}")
        return []


# ────────────────────────────────────────────
# Full Workflow Runner
# ────────────────────────────────────────────

def run_full_workflow(
    recipient_email: str = RECIPIENT_EMAIL,
    linkedin_url:    str = LINKEDIN_SEARCH_URL,
    indeed_url:      str = INDEED_SEARCH_URL,
):
    """
    Runs the entire pipeline:
    1. Scrape all sources (LinkedIn, Indeed, Lintberg)
    2. Upsert vectors to Pinecone
    3. Run AI agent & save NEW results only
    4. Fetch ONLY this run's new jobs
    5. Send ONE notification email
    6. Save sent jobs to sended-jobs namespace
    """
    scheduler_state["last_run_at"]     = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    scheduler_state["last_run_status"] = "running"
    log("🚀 Full workflow started...")

    try:
        # Step 1: Scrape all sources
        run_all_scrapers(linkedin_url, indeed_url)

        # Step 2: Upsert vectors
        run_all_upserts()

        # Step 3: Agent matching — returns ONLY this run's saved vector ids
        saved_vector_ids = run_agent_and_save_results()
        
        # ── NEW: Run cleanup to sync expired statuses before fetching ──
        run_expired_jobs_cleanup()


        # Step 4: Fetch ONLY jobs saved in THIS run
        log("=" * 50)
        log("STEP 4 & 5: Preparing and sending notification email...")

        if saved_vector_ids:
            all_results = fetch_latest_jobs_from_results(top_k=200)
            # Filter to only jobs saved in this run
            new_jobs = [j for j in all_results if j.get("vector_id") in set(saved_vector_ids)]
            log(f"✓ {len(new_jobs)} new job(s) to notify from this run.")
        else:
            new_jobs = []
            log("No new jobs saved in this run.")

        # Step 5: Send ONE email
        if new_jobs:
            subject = "Job Notification - Latest Listings"
            message = format_jobs_email(new_jobs)
        else:
            subject = "Job Notification - No New Jobs Found"
            message = format_no_jobs_email()

        success = send_notification_email(
            recipient_email=recipient_email,
            subject=subject,
            message=message,
        )

        # Step 6: Save sent jobs to prevent re-sending
        if success and new_jobs:
            save_sent_jobs_to_db(new_jobs)
            log(f"✓ Marked {len(new_jobs)} job(s) as sent.")

        scheduler_state["last_run_status"] = "success" if success else "email_failed"
        log(f"✅ Full workflow completed. Status: {scheduler_state['last_run_status']}")

    except Exception as e:
        scheduler_state["last_run_status"] = "failed"
        log(f"❌ Workflow failed: {e}")
        raise

