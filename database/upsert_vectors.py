
import os
import json
from dotenv import load_dotenv
from database.pinecone import EmbeddingService
from datetime import datetime, timezone

load_dotenv()


def load_jobs_from_json(file_path):
    """Load job data from JSON file"""
    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def get_existing_job_ids_from_pinecone(embedding_service, namespace, top_k=10000):
    """Fetch all existing job IDs from Pinecone namespace to avoid re-upserting duplicates."""
    try:
        results = embedding_service.search_similar("job", top_k=top_k, namespace=namespace)
        existing_ids = set()
        for match in results:
            job_id = match.get('metadata', {}).get('job_id')
            if job_id:
                existing_ids.add(job_id)
        print(f"[DB CHECK] Found {len(existing_ids)} existing job IDs in namespace '{namespace}'")
        return existing_ids
    except Exception as e:
        print(f"[DB CHECK] WARNING: Could not fetch from Pinecone namespace '{namespace}': {e}")
        return set()


# ── NEW: Fetch existing jobs with their statuses ──
def get_existing_jobs_with_status_from_pinecone(embedding_service, namespace, top_k=10000):
    """
    Fetch all existing jobs from Pinecone namespace.
    Returns a dict: { job_id: status }
    """
    try:
        results = embedding_service.search_similar("job", top_k=top_k, namespace=namespace)
        existing_jobs = {}
        for match in results:
            metadata = match.get('metadata', {})
            job_id   = metadata.get('job_id')
            status   = metadata.get('status', 'active')
            if job_id:
                existing_jobs[job_id] = status
        print(f"[DB CHECK] Found {len(existing_jobs)} existing jobs in namespace '{namespace}'")
        return existing_jobs
    except Exception as e:
        print(f"[DB CHECK] WARNING: Could not fetch from Pinecone namespace '{namespace}': {e}")
        return {}


# ── NEW: Fetch existing jobs with their statuses ──
def prepare_job_text(job, max_chars=6000):
    """
    Prepare a SINGLE text representation of a job for embedding.
    Priority fields come first so the most important info is never truncated.
    Description is truncated to fill remaining space — NO chunking.
    """
    # High-signal fields first (always included in full)
    parts = []
    if job.get('title'):
        parts.append(f"Title: {job['title']}")
    if job.get('company'):
        parts.append(f"Company: {job['company']}")
    if job.get('location'):
        parts.append(f"Location: {job['location']}")
    if job.get('job_types'):
        parts.append(f"Job Type: {job['job_types']}")
    if job.get('salary'):
        parts.append(f"Salary: {job['salary']}")
    if job.get('remote'):
        parts.append(f"Remote: {job['remote']}")
    if job.get('benefits'):
        parts.append(f"Benefits: {job['benefits']}")
    if job.get('snippet'):
        parts.append(f"Summary: {job['snippet']}")

    base_text = "\n".join(parts)

    # Fill remaining space with description (truncate, never chunk)
    remaining = max_chars - len(base_text)
    if job.get('description') and remaining > 200:
        desc = job['description'][:remaining]
        parts.append(f"Description: {desc}")

    return "\n".join(parts)


def batch_upsert_vectors(embedding_service, vectors, namespace, batch_size=100):
    """Upsert vectors in batches to stay within Pinecone's 2MB request limit."""
    total = len(vectors)
    upserted = 0
    for i in range(0, total, batch_size):
        batch = vectors[i:i + batch_size]
        embedding_service.upsert_vectors(batch, namespace=namespace)
        upserted += len(batch)
        print(f"  Upserted {upserted}/{total} vectors...")
    return upserted


# ─────────────────────────────────────────────
# SHARED CORE: prepare vectors for any source
# ─────────────────────────────────────────────


def sanitize_metadata(metadata: dict) -> dict:
    """
    Pinecone only accepts str, int, float, bool, or list[str] as metadata values.
    Convert None → '' and any other unsupported types → str.
    """
    sanitized = {}
    for key, value in metadata.items():
        if value is None:
            sanitized[key] = ''
        elif isinstance(value, (str, int, float, bool)):
            sanitized[key] = value
        elif isinstance(value, list):
            # Ensure all list items are strings
            sanitized[key] = [str(v) for v in value]
        else:
            sanitized[key] = str(value)
    return sanitized


def _build_vectors(jobs, source_prefix, embedding_service, extra_metadata_fn=None):
    """
    Generate ONE embedding per job and build the vector list.
    """
    texts = [prepare_job_text(job) for job in jobs]

    print(f"  Generating {len(texts)} embeddings (1 per job, no chunking)...")
    embeddings = embedding_service.generate_embeddings_batch(texts)

    vectors = []

    for job, embedding in zip(jobs, embeddings):
        if embedding is None:
            print(f"  Warning: Failed embedding for job {job.get('job_id', 'unknown')}")
            continue

        job_id = job.get('job_id', '')

        metadata = {
            'job_id':               job_id,
            'title':                job.get('title', ''),
            'company':              job.get('company', ''),
            'location':             job.get('location', ''),
            'link':                 job.get('link', ''),
            'validity_text':        job.get('validity_text', ''),
            'salary':               job.get('salary', ''),
            'job_types':            job.get('job_types', ''),
            'remote':               job.get('remote', ''),
            'benefits':             job.get('benefits', ''),
            'snippet':              job.get('snippet', ''),
            'description_preview':  (job.get('description') or '')[:900],
            'date':                 job.get('date', ''),
            'status':               job.get('status', ''),

        }

        if extra_metadata_fn:
            metadata.update(extra_metadata_fn(job))

        # ---- Sanitize: remove None / unsupported types before upsert ----
        metadata = sanitize_metadata(metadata)

        vectors.append({
            'id':        f"{source_prefix}_{job_id}",
            'embedding': embedding,
            'metadata':  metadata,
        })

    return vectors




def _update_job_statuses(jobs, source_prefix, namespace):
    """
    For jobs that already exist in Pinecone but their status has changed,
    update ONLY the status and expired_at metadata fields.
    Uses index.update() — no re-embedding needed.
    """
    from database.pinecone import EmbeddingService
    from utils import embedding_service as emb_svc
    from pinecone import Pinecone

    pc    = Pinecone(api_key=os.getenv("PINECONE_API_KEY"))
    index = pc.Index(os.getenv("PINECONE_INDEX_NAME", "scrapped-data"))

    updated = 0
    failed  = 0

    for job in jobs:
        job_id      = job.get('job_id', '')
        new_status  = job.get('status', 'expired')
        vector_id   = f"{source_prefix}_{job_id}"

        try:
            update_fields = {"status": new_status}

            # If job is now expired, record when it expired
            if new_status == 'expired':
                update_fields["expired_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

            index.update(
                id=vector_id,
                set_metadata=update_fields,
                namespace=namespace,
            )
            print(f"  [STATUS UPDATE] {vector_id} → {new_status}")
            updated += 1

        except Exception as e:
            print(f"  [STATUS UPDATE] Failed for {vector_id}: {e}")
            failed += 1

    print(f"[STATUS UPDATE] Done — Updated: {updated} | Failed: {failed}")




def create_and_upsert_linkedin_embeddings():
    json_file_path = 'json_files/parsed_jobs_file.json'
    namespace = "linkedin-jobs"

    print(f"\n{'='*60}")
    print(f"[LINKEDIN] Loading jobs from {json_file_path}...")

    if not os.path.exists(json_file_path):
        print(f"[ERROR] File not found: {json_file_path}. Skipping LinkedIn...")
        return

    jobs = load_jobs_from_json(json_file_path)
    print(f"[LINKEDIN] Loaded {len(jobs)} jobs")

    embedding_service = EmbeddingService()

    # ── CHANGED: fetch job_id + status instead of just job_id ──
    existing_jobs = get_existing_jobs_with_status_from_pinecone(embedding_service, namespace)

    new_jobs            = []
    status_update_jobs  = []
    skipped             = 0

    for job in jobs:
        job_id      = job.get('job_id')
        json_status = job.get('status', 'active')

        if job_id not in existing_jobs:
            # Brand new job — upsert normally
            new_jobs.append(job)
        else:
            db_status = existing_jobs[job_id]
            if db_status != json_status:
                # Job exists but status has changed (e.g., active → expired)
                status_update_jobs.append(job)
            else:
                # Same job_id, same status — skip
                skipped += 1

    print(
        f"[DEDUP] Total: {len(jobs)} | "
        f"New: {len(new_jobs)} | "
        f"Status changed (will update): {len(status_update_jobs)} | "
        f"Unchanged (skipped): {skipped}"
    )

    def linkedin_extra(job):
        return {'validity': job.get('validity', '')}

    # ── Upsert brand new jobs ──
    if new_jobs:
        vectors = _build_vectors(new_jobs, 'linkedin_job', embedding_service, linkedin_extra)
        print(f"[LINKEDIN] Upserting {len(vectors)} new vectors (namespace: {namespace})...")
        batch_upsert_vectors(embedding_service, vectors, namespace)
        print(f"✓ [LINKEDIN] Upserted {len(vectors)} new job embeddings!")

    # ── Update status-changed jobs using index.update() ──
    if status_update_jobs:
        _update_job_statuses(status_update_jobs, 'linkedin_job', namespace)

    if not new_jobs and not status_update_jobs:
        print("[DEDUP] No new or updated jobs to process.")

    stats = embedding_service.get_index_stats(namespace=namespace)
    count = stats.get('namespaces', {}).get(namespace, {}).get('vector_count', 'N/A')
    print(f"[LINKEDIN] Total vectors in namespace: {count}")


# ─────────────────────────────────────────────
# INDEED
# ─────────────────────────────────────────────

def create_and_upsert_indeed_embeddings():
    json_file_path = 'json_files/indeed_parsed_jobs.json'
    namespace = "indeed-jobs"

    print(f"\n{'='*60}")
    print(f"[INDEED] Loading jobs from {json_file_path}...")

    if not os.path.exists(json_file_path):
        print(f"[ERROR] File not found: {json_file_path}. Skipping Indeed...")
        return

    jobs = load_jobs_from_json(json_file_path)
    print(f"[INDEED] Loaded {len(jobs)} jobs")

    embedding_service = EmbeddingService()

    # ── CHANGED: fetch job_id + status ──
    existing_jobs = get_existing_jobs_with_status_from_pinecone(embedding_service, namespace)

    new_jobs            = []
    status_update_jobs  = []
    skipped             = 0

    for job in jobs:
        job_id      = job.get('job_id')
        json_status = job.get('status', 'active')

        if job_id not in existing_jobs:
            new_jobs.append(job)
        else:
            db_status = existing_jobs[job_id]
            if db_status != json_status:
                status_update_jobs.append(job)
            else:
                skipped += 1

    print(
        f"[DEDUP] Total: {len(jobs)} | "
        f"New: {len(new_jobs)} | "
        f"Status changed (will update): {len(status_update_jobs)} | "
        f"Unchanged (skipped): {skipped}"
    )

    def indeed_extra(job):
        return {
            'company_rating': str(job.get('company_rating') or ''),
            'review_count':   str(job.get('review_count') or ''),
            'new_job':        str(job.get('new_job') or ''),
            'source':         job.get('source') or 'indeed',
        }

    if new_jobs:
        vectors = _build_vectors(new_jobs, 'indeed_job', embedding_service, indeed_extra)
        print(f"[INDEED] Upserting {len(vectors)} new vectors (namespace: {namespace})...")
        batch_upsert_vectors(embedding_service, vectors, namespace)
        print(f"✓ [INDEED] Upserted {len(vectors)} new job embeddings!")

    if status_update_jobs:
        _update_job_statuses(status_update_jobs, 'indeed_job', namespace)

    if not new_jobs and not status_update_jobs:
        print("[DEDUP] No new or updated jobs to process.")

    stats = embedding_service.get_index_stats(namespace=namespace)
    count = stats.get('namespaces', {}).get(namespace, {}).get('vector_count', 'N/A')
    print(f"[INDEED] Total vectors in namespace: {count}")


# ─────────────────────────────────────────────
# LINTBERG
# ─────────────────────────────────────────────

def create_and_upsert_lintberg_embeddings():
    json_file_path = 'json_files/lintberg_jobs.json'
    namespace = "lintberg-jobs"

    print(f"\n{'='*60}")
    print(f"[LINTBERG] Loading jobs from {json_file_path}...")

    if not os.path.exists(json_file_path):
        print(f"[ERROR] File not found: {json_file_path}. Skipping Lintberg...")
        return

    jobs = load_jobs_from_json(json_file_path)
    print(f"[LINTBERG] Loaded {len(jobs)} jobs")

    embedding_service = EmbeddingService()

    # ── CHANGED: fetch job_id + status ──
    existing_jobs = get_existing_jobs_with_status_from_pinecone(embedding_service, namespace)

    new_jobs            = []
    status_update_jobs  = []
    skipped             = 0

    for job in jobs:
        job_id      = job.get('job_id')
        json_status = job.get('status', 'active')

        if job_id not in existing_jobs:
            new_jobs.append(job)
        else:
            db_status = existing_jobs[job_id]
            if db_status != json_status:
                status_update_jobs.append(job)
            else:
                skipped += 1

    print(
        f"[DEDUP] Total: {len(jobs)} | "
        f"New: {len(new_jobs)} | "
        f"Status changed (will update): {len(status_update_jobs)} | "
        f"Unchanged (skipped): {skipped}"
    )

    def lintberg_extra(job):
        return {
            'validity':  job.get('validity', ''),
            'new_job':   job.get('new_job', False),
            'source':    job.get('source') or 'lintberg',
        }

    if new_jobs:
        vectors = _build_vectors(new_jobs, 'lintberg_job', embedding_service, lintberg_extra)
        print(f"[LINTBERG] Upserting {len(vectors)} new vectors (namespace: {namespace})...")
        batch_upsert_vectors(embedding_service, vectors, namespace)
        print(f"✓ [LINTBERG] Upserted {len(vectors)} new job embeddings!")

    if status_update_jobs:
        _update_job_statuses(status_update_jobs, 'lintberg_job', namespace)

    if not new_jobs and not status_update_jobs:
        print("[DEDUP] No new or updated jobs to process.")

    stats = embedding_service.get_index_stats(namespace=namespace)
    count = stats.get('namespaces', {}).get(namespace, {}).get('vector_count', 'N/A')
    print(f"[LINTBERG] Total vectors in namespace: {count}")


if __name__ == "__main__":
    create_and_upsert_linkedin_embeddings()
    create_and_upsert_indeed_embeddings()
    create_and_upsert_lintberg_embeddings()
    