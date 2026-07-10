import os
from datetime import datetime, timezone
from dotenv import load_dotenv
from utils import embedding_service

load_dotenv()

# ---- Only jobs scraped today (UTC date) will be included ----
def get_today_utc() -> datetime.date:
    return datetime.now(timezone.utc).date()

# ONE_WEEK_AGO = datetime.now(timezone.utc) - timedelta(weeks=1)




# def is_job_within_one_week(date_str: str) -> bool:
#     """
#     Returns True if the job was scraped within the last 1 week.
#     Expects date_str in format 'YYYY-MM-DD'.
#     """
#     if not date_str:
#         return False
#     try:
#         scraped_date = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
#         return scraped_date >= ONE_WEEK_AGO
#     except ValueError:
#         return False

def is_job_scraped_today(date_str: str) -> bool:
    """
    Returns True if the job was scraped today (UTC date).
    Expects date_str in format 'YYYY-MM-DD'.
    """
    if not date_str:
        return False
    try:
        scraped_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        print(f"    [debug] Job scraped_date={scraped_date}, today={get_today_utc()}")
        return scraped_date == get_today_utc()
    except ValueError:
        return False

def query_linkedin_jobs(query: str, top_k: int = 20) -> list[dict]:
    """
    Query the linkedin-jobs namespace in Pinecone.
    Returns jobs scraped today only.
    Filters out expired jobs.
    One vector per job — no duplicate chunks.
    """
    print(f"🔍 Querying LinkedIn jobs...")

    results = embedding_service.search_similar(
        query_text=query,
        top_k=top_k,
        namespace="linkedin-jobs",
    )

    matches = results if isinstance(results, list) else results.get('matches', [])
    print(f"  Found {len(matches)} LinkedIn match(es) before filters")

    jobs = []
    skipped_date = 0
    skipped_expired = 0
    
    for match in matches:
        metadata = match.get('metadata', {})

        # Skip jobs not scraped today
        if not is_job_scraped_today(metadata.get('date', '')):
            skipped_date += 1
            continue

        # Skip expired jobs
        if metadata.get('status', '').lower() == 'expired':
            skipped_expired += 1
            continue

        jobs.append({
            'score':               match.get('score', 0.0),
            'job_id':              metadata.get('job_id', ''),
            'title':               metadata.get('title', ''),
            'company':             metadata.get('company', ''),
            'location':            metadata.get('location', ''),
            'link':                metadata.get('link', ''),
            'salary':              metadata.get('salary', ''),
            'job_types':           metadata.get('job_types', ''),
            'remote':              metadata.get('remote', ''),
            'benefits':            metadata.get('benefits', ''),
            'snippet':             metadata.get('snippet', ''),
            'description_preview': metadata.get('description_preview', ''),
            'date':                metadata.get('date', ''),
            'status':              metadata.get('status', ''),
            'source':              'linkedin',

        })

    print(f"  ✓ Returning {len(jobs)} LinkedIn jobs (skipped {skipped_date} old + {skipped_expired} expired)")
    return jobs


def query_indeed_jobs(query: str, top_k: int = 20) -> list[dict]:
    """
    Query the indeed-jobs namespace in Pinecone.
    Returns jobs scraped today only.
    Filters out expired jobs.
    One vector per job — no duplicate chunks.
    """
    print(f"🔍 Querying Indeed jobs...")

    results = embedding_service.search_similar(
        query_text=query,
        top_k=top_k,
        namespace="indeed-jobs",
    )

    matches = results if isinstance(results, list) else results.get('matches', [])
    print(f"  Found {len(matches)} Indeed match(es) before filters")

    jobs = []
    skipped_date = 0
    skipped_expired = 0
    
    for match in matches:
        metadata = match.get('metadata', {})

        # Skip jobs not scraped today
        if not is_job_scraped_today(metadata.get('date', '')):
            skipped_date += 1
            continue

        # Skip expired jobs
        if metadata.get('status', '').lower() == 'expired':
            skipped_expired += 1
            continue

        jobs.append({
            'score':               match.get('score', 0.0),
            'job_id':              metadata.get('job_id', ''),
            'title':               metadata.get('title', ''),
            'company':             metadata.get('company', ''),
            'location':            metadata.get('location', ''),
            'link':                metadata.get('link', ''),
            'salary':              metadata.get('salary', ''),
            'job_types':           metadata.get('job_types', ''),
            'remote':              metadata.get('remote', ''),
            'benefits':            metadata.get('benefits', ''),
            'company_rating':      metadata.get('company_rating', ''),
            'review_count':        metadata.get('review_count', ''),
            'new_job':             metadata.get('new_job', ''),
            'snippet':             metadata.get('snippet', ''),
            'description_preview': metadata.get('description_preview', ''),
            'status':              metadata.get('status', ''),
            'date':                metadata.get('date', ''),
            'source':              metadata.get('source', 'indeed'),
        })

    print(f"  ✓ Returning {len(jobs)} Indeed jobs (skipped {skipped_date} old + {skipped_expired} expired)")
    return jobs


def query_lintberg_jobs(query: str, top_k: int = 20) -> list[dict]:
    """
    Query the lintberg-jobs namespace in Pinecone.
    Returns jobs scraped today only.
    Filters out expired jobs.
    One vector per job — no duplicate chunks.
    """
    print(f"🔍 Querying Lintberg jobs...")

    results = embedding_service.search_similar(
        query_text=query,
        top_k=top_k,
        namespace="lintberg-jobs",
    )

    matches = results if isinstance(results, list) else results.get('matches', [])
    print(f"  Found {len(matches)} Lintberg match(es) before filters")

    jobs = []
    skipped_date = 0
    skipped_expired = 0
    
    for match in matches:
        metadata = match.get('metadata', {})

        # Skip jobs not scraped today
        if not is_job_scraped_today(metadata.get('date', '')):
            skipped_date += 1
            continue

        # Skip expired jobs
        if metadata.get('status', '').lower() == 'expired':
            skipped_expired += 1
            continue

        jobs.append({
            'score':               match.get('score', 0.0),
            'job_id':              metadata.get('job_id', ''),
            'title':               metadata.get('title', ''),
            'company':             metadata.get('company', ''),
            'location':            metadata.get('location', ''),
            'link':                metadata.get('link', ''),
            'validity':            metadata.get('validity', ''),
            'validity_text':       metadata.get('validity_text', ''),
            'salary':              metadata.get('salary', ''),
            'job_types':           metadata.get('job_types', ''),
            'remote':              metadata.get('remote', ''),
            'benefits':            metadata.get('benefits', ''),
            'snippet':             metadata.get('snippet', ''),
            'description_preview': metadata.get('description_preview', ''),
            'date':                metadata.get('date', ''),
            'source':              metadata.get('source', 'lintberg'),
            'status':              metadata.get('status', ''),
        })

    print(f"  ✓ Returning {len(jobs)} Lintberg jobs (skipped {skipped_date} old + {skipped_expired} expired)")
    return jobs




def query_cv_profiles(query: str, top_k: int = 15) -> list[dict]:
    """
    Query the cv-profiles namespace in Pinecone.
    Returns full CV text (stored as text_preview in metadata).
    One vector per candidate — no chunking.
    No date filter — CVs don't expire.
    """
    print(f"🔍 Querying CV profiles...")

    results = embedding_service.search_similar(
        query_text=query,
        top_k=top_k,
        namespace="cv-profiles",
    )

    matches = results if isinstance(results, list) else results.get('matches', [])
    print(f"  ✓ Found {len(matches)} CV profile(s)")

    profiles = []
    for match in matches:
        metadata = match.get('metadata', {})
        profiles.append({
            'candidate_id':   metadata.get('candidate_id', ''),    # needed for batch request custom_id
            'candidate_name': metadata.get('candidate_name', ''),
            'text_preview':   metadata.get('text_preview', ''),    # full CV text (up to 8000 chars)
            'search_query':   metadata.get('search_query', ''),    # pre-built search query
        })

    return profiles





# ─────────────────────────────────────────────────────────────────
# GENERIC HELPER for new-source namespaces
# All sources in upsert_vectors_two share the same metadata schema.
# ─────────────────────────────────────────────────────────────────

def _query_new_source_jobs(namespace: str, source_label: str, query: str, top_k: int = 20) -> list[dict]:
    """
    Generic retrieval for all new-source namespaces.
    Applies "scraped today" date filter and expired status filter.
    """
    print(f"🔍 Querying {source_label} jobs...")

    results = embedding_service.search_similar(
        query_text=query,
        top_k=top_k,
        namespace=namespace,
    )

    matches = results if isinstance(results, list) else results.get('matches', [])
    print(f"  Found {len(matches)} {source_label} match(es) before filters")

    jobs = []
    skipped_date = 0
    skipped_expired = 0

    for match in matches:
        metadata = match.get('metadata', {})

        if not is_job_scraped_today(metadata.get('date', '')):
            skipped_date += 1
            continue

        if metadata.get('status', '').lower() == 'expired':
            skipped_expired += 1
            continue

        jobs.append({
            'score':        match.get('score', 0.0),
            'job_id':       metadata.get('job_id', ''),
            'title':        metadata.get('title', ''),
            'company':      metadata.get('company', ''),
            'location':     metadata.get('location', ''),
            'city':         metadata.get('city', ''),
            'country':      metadata.get('country', ''),
            'link':         metadata.get('link', ''),
            'job_type':     metadata.get('job_type', ''),
            'posted_date':  metadata.get('posted_date', ''),
            'salary':       metadata.get('salary', ''),
            'category':     metadata.get('category', ''),
            'department':   metadata.get('department', ''),
            'description_preview': metadata.get('description_preview', ''),
            'status':       metadata.get('status', ''),
            'source':       metadata.get('source', source_label.lower()),
            'date':         metadata.get('date', ''),
        })

    print(f"  ✓ Returning {len(jobs)} {source_label} jobs (skipped {skipped_date} old + {skipped_expired} expired)")
    return jobs


# ─────────────────────────────────────────────────────────────────
# Individual query functions — one per new namespace
# ─────────────────────────────────────────────────────────────────

def query_straumann_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('straumann-jobs', 'Straumann', query, top_k)

def query_thema_group_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('thema-group-jobs', 'Thema Group', query, top_k)

def query_career_opener_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('career-opener-jobs', 'Career Opener', query, top_k)

def query_terarecon_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('terarecon-jobs', 'TeraRecon', query, top_k)

def query_medtronic_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('medtronic-jobs', 'Medtronic', query, top_k)

def query_lynchwise_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('lynchwise-jobs', 'Lynchwise', query, top_k)

def query_partners_at_work_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('partners-at-work-jobs', 'Partners at Work', query, top_k)

def query_odgers_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('odgers-jobs', 'Odgers Berndtson', query, top_k)

def query_quaestus_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('quaestus-jobs', 'Quaestus', query, top_k)

def query_beiersdorf_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('beiersdorf-jobs', 'Beiersdorf', query, top_k)

def query_spencer_stuart_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('spencer-stuart-jobs', 'Spencer Stuart', query, top_k)

def query_fresenius_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('fresenius-jobs', 'Fresenius', query, top_k)

def query_vandegroep_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('vandegroep-jobs', 'Van de Groep', query, top_k)

def query_heidrick_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('heidrick-jobs', 'Heidrick & Struggles', query, top_k)


def query_adc_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('adc-jobs', 'Amsterdam Data Collective', query, top_k)


def query_carplai_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('carplai-jobs', 'Carplai', query, top_k)


def query_hologic_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('hologic-jobs', 'Hologic', query, top_k)


def query_ceramtec_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('ceramtec-jobs', 'Ceramtec', query, top_k)


def query_incepto_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('incepto-jobs', 'Incepto', query, top_k)

def query_radiobotics_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('radiobotics-jobs', 'Radiobotics', query, top_k)

def query_sectra_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('sectra-jobs', 'Sectra', query, top_k)

def query_iqvia_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('iqvia-jobs', 'IQVIA', query, top_k)

def query_ely_lily_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('ely_lily-jobs', 'Eli Lilly', query, top_k)

def query_falck_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('falck-jobs', 'Falck', query, top_k)

def query_draeger_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('draeger-jobs', 'Dräger', query, top_k)

def query_novonordisk_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('novonordisk-jobs', 'Novo Nordisk', query, top_k)

def query_unilabs_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('unilabs-jobs', 'Unilabs', query, top_k)

def query_abott_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('abott-jobs', 'Abbott', query, top_k)

def query_international_sos_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('international-sos-jobs', 'International SOS', query, top_k)

def query_philips_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('philips-jobs', 'Philips', query, top_k)

def query_sanday_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('sanday-jobs', 'Sanday', query, top_k)

def query_doctolib_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('doctolib-jobs', 'Doctolib', query, top_k)

def query_igh_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('igh-jobs', 'IGH', query, top_k)

def query_adj_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('adj-jobs', 'ADJ', query, top_k)

def query_enddeblauw_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('enddeblauw-jobs', 'End de Blauw', query, top_k)

def query_galan_groep_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('galan-groep-jobs', 'Galan Groep', query, top_k)  

def query_bureau_blauw_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('bureau-blauw-jobs', 'Bureau Blauw', query, top_k)

def query_kv_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('kv-jobs', 'K+V', query, top_k)

def query_leuwendal_advice_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('leuwendal-advice-jobs', 'Leuwendal Advice', query, top_k)

def query_korn_ferry_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('korn-ferry-jobs', 'Korn Ferry', query, top_k)

def query_mercuri_urval_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('mercuri-urval-jobs', 'Mercuri Urval', query, top_k)


def query_meussen_search_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('meussen-search-jobs', 'Meussen Search', query, top_k)


def query_pageexecutive_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('pageexecutive-jobs', 'Page Executive', query, top_k)


def query_riekenoomen_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('riekenoomen-jobs', 'Rieken & Oomen', query, top_k)


def query_vromvan_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('vromvan-jobs', 'Vrom van Dijk', query, top_k)

def query_perret_laver_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('perret-laver-jobs', 'Perret Laver', query, top_k)


def query_elcg_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('elcg-jobs', 'ELCG', query, top_k)


def query_logex_jobs(query: str, top_k: int = 20) -> list[dict]:
    return _query_new_source_jobs('logex-jobs', 'Logex', query, top_k)

def query_talentmark_jobs(query:str, top_k:int=20)-> list[dict]:
    return _query_new_source_jobs('talentmark-jobs','Talentmark', query, top_k)

def query_beonemedicine_jobs(query:str, top_k:int=20)-> list[dict]:
    return _query_new_source_jobs('beonemedicine-jobs','BeOneMedicine', query, top_k)


def query_bricegroup_jobs(query:str, top_k:int=20)-> list[dict]:
    return _query_new_source_jobs('bricegroup-jobs','Brice Group', query, top_k)

def query_demcon_jobs(query:str, top_k:int=20)-> list[dict]:
    return _query_new_source_jobs('demcon-jobs','Demcon', query, top_k)

def query_thermo_fischer_jobs(query:str, top_k:int=20)-> list[dict]:
    return _query_new_source_jobs('thermo-fischer-jobs','Thermo Fischer', query, top_k)



if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Debug retrieval: load CVs and query jobs across all configured sources."
    )
    parser.add_argument(
        "--cv-query",
        default="candidate profile skills experience qualifications",
        help="Semantic query used to fetch CV profiles from cv-profiles namespace.",
    )
    parser.add_argument("--cv-top-k", type=int, default=5, help="How many CV profiles to fetch.")
    parser.add_argument("--jobs-top-k", type=int, default=200, help="Top-k per source before filters.")
    parser.add_argument("--candidate-id", default="", help="Optional candidate_id filter.")
    parser.add_argument("--candidate-name", default="", help="Optional candidate_name filter.")
    parser.add_argument("--show-jobs", type=int, default=5, help="How many jobs per source to print.")

    args = parser.parse_args()

    source_functions = [
        ("Straumann", query_straumann_jobs),
        ("Thema Group", query_thema_group_jobs),
        ("Career Opener", query_career_opener_jobs),
        ("TeraRecon", query_terarecon_jobs),
        ("Medtronic", query_medtronic_jobs),
        ("Lynchwise", query_lynchwise_jobs),
        ("Partners at Work", query_partners_at_work_jobs),
        ("Odgers Berndtson", query_odgers_jobs),
        ("Quaestus", query_quaestus_jobs),
        ("Beiersdorf", query_beiersdorf_jobs),
        ("Spencer Stuart", query_spencer_stuart_jobs),
        ("Fresenius", query_fresenius_jobs),
        ("Van de Groep", query_vandegroep_jobs),
        ("Heidrick", query_heidrick_jobs),
        ("Amsterdam Data Collective", query_adc_jobs),
        ("Carplai", query_carplai_jobs),
        ("Hologic", query_hologic_jobs),
        ("Ceramtec", query_ceramtec_jobs),
        ("Incepto", query_incepto_jobs),
        ("Radiobotics", query_radiobotics_jobs),
        ("Sectra", query_sectra_jobs),
        ("IQVIA", query_iqvia_jobs),
        ("Eli Lilly", query_ely_lily_jobs),
        ("Falck", query_falck_jobs),
        ("Drager", query_draeger_jobs),
        ("Novo Nordisk", query_novonordisk_jobs),
        ("Unilabs", query_unilabs_jobs),
        ("Abbott", query_abott_jobs),
        ("International SOS", query_international_sos_jobs),
        ("Philips", query_philips_jobs),
        ("Sanday", query_sanday_jobs),
        ("Doctolib", query_doctolib_jobs),
        ("IGH", query_igh_jobs),
        ("ADJ", query_adj_jobs),
        ("End de Blauw", query_enddeblauw_jobs),
        ("Galan Groep", query_galan_groep_jobs),
        ("Bureau Blauw", query_bureau_blauw_jobs),
        ("K+V", query_kv_jobs),
        ("Leuwendal Advice", query_leuwendal_advice_jobs),
        ("Korn Ferry", query_korn_ferry_jobs),
        ("Mercuri Urval", query_mercuri_urval_jobs),
        ("Meussen Search", query_meussen_search_jobs),
        ("Page Executive", query_pageexecutive_jobs),
        ("Rieken & Oomen", query_riekenoomen_jobs),
        ("Vrom van Dijk", query_vromvan_jobs),
        ("Perret Laver", query_perret_laver_jobs),
        ("ELCG", query_elcg_jobs),
        ("Logex", query_logex_jobs),
        ("Talentmark", query_talentmark_jobs),
        ("BeOneMedicine", query_beonemedicine_jobs),
        ("Brice Group", query_bricegroup_jobs),
        ("LinkedIn", query_linkedin_jobs),
        ("Indeed", query_indeed_jobs),
        ("Lintberg", query_lintberg_jobs),
        ("Demcon", query_demcon_jobs),
        ("Thermo Fischer", query_thermo_fischer_jobs),
    ]

    print("=" * 80)
    print("Retrieval Debug Runner")
    print(f"UTC now: {datetime.now(timezone.utc).isoformat()}")
    print(f"Module TODAY_UTC_DATE: {get_today_utc()}")
    print("=" * 80)

    profiles = query_cv_profiles(args.cv_query, top_k=args.cv_top_k)
    if not profiles:
        print("No CV profiles found.")
        sys.exit(0)

    filtered_profiles = []
    for profile in profiles:
        if args.candidate_id and str(profile.get("candidate_id", "")).strip() != args.candidate_id.strip():
            continue
        if args.candidate_name and args.candidate_name.lower().strip() not in str(profile.get("candidate_name", "")).lower():
            continue
        filtered_profiles.append(profile)

    if not filtered_profiles:
        print("No CV profiles matched the provided filters.")
        sys.exit(0)

    print(f"Running retrieval for {len(filtered_profiles)} CV profile(s)...")

    for idx, cv in enumerate(filtered_profiles, 1):
        candidate_id = cv.get("candidate_id", "")
        candidate_name = cv.get("candidate_name", "Unknown")
        cv_text = (cv.get("text_preview") or "").strip()
        search_query = (cv.get("search_query") or "").strip()
        if not search_query:
            search_query = cv_text[:1000]

        if not search_query:
            print(f"\n[{idx}] {candidate_name} ({candidate_id})")
            print("  Skipped: empty search_query and empty CV text.")
            continue

        print("\n" + "-" * 80)
        print(f"[{idx}] Candidate: {candidate_name} ({candidate_id})")
        print(f"Query preview: {search_query[:160].replace(chr(10), ' ')}")
        print("-" * 80)

        source_results = []
        total_jobs = 0
        unique_ids = set()

        for label, fn in source_functions:
            try:
                jobs = fn(search_query, top_k=args.jobs_top_k)
            except Exception as e:
                print(f"  [ERROR] {label}: {e}")
                jobs = []

            source_results.append((label, jobs))
            total_jobs += len(jobs)
            for job in jobs:
                job_id = str(job.get("job_id", "")).strip()
                if job_id:
                    unique_ids.add(job_id)

        print(f"Summary: sources={len(source_functions)}")
        print(f"Total returned={total_jobs} | Unique job_id={len(unique_ids)}")

        def print_sample(source_name, jobs):
            print(f"\n  {source_name} sample ({min(len(jobs), args.show_jobs)}/{len(jobs)}):")
            for job in jobs[:args.show_jobs]:
                print(
                    "   - "
                    f"job_id={job.get('job_id', '')} | "
                    f"title={job.get('title', '')} | "
                    f"company={job.get('company', '')} | "
                    f"location={job.get('location', '')} | "
                    f"date={job.get('date', '')} | "
                    f"status={job.get('status', '')}"
                )

        for source_name, jobs in source_results:
            print_sample(source_name, jobs)

    print("\nDone.")


