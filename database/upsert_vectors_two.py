import os
from dotenv import load_dotenv
from database.pinecone import EmbeddingService
from database.upsert_vectors import (
    load_jobs_from_json,
    get_existing_jobs_with_status_from_pinecone,
    _build_vectors,
    _update_job_statuses,
    batch_upsert_vectors,
    sanitize_metadata,
    prepare_job_text,
)

load_dotenv()


# ─────────────────────────────────────────────────────────────────
# Config: map each JSON file → (namespace, vector_id_prefix)
# Add new scrapers here — no code changes needed elsewhere.
# ─────────────────────────────────────────────────────────────────
SOURCE_CONFIG = {
    'json_files/strauman_jobs.json':           ('straumann-jobs',       'straumann'),
    'json_files/thema_group_jobs.json':        ('thema-group-jobs',     'thema_group'),
    'json_files/career_opener_jobs.json':      ('career-opener-jobs',   'career_opener'),
    'json_files/terarecon_jobs.json':          ('terarecon-jobs',       'terarecon'),
    'json_files/medtronics_jobs.json':         ('medtronics-jobs',      'medtronic'),
    'json_files/lyncwise_jobs.json':           ('lynchwise-jobs',       'lynchwise'),
    'json_files/partners_at_work_jobs.json':   ('partners-at-work-jobs','partners_at_work'),
    'json_files/odgers_brendston_jobs.json':   ('odgers-jobs',          'odgers'),
    'json_files/quaestus_jobs.json':           ('quaestus-jobs',        'quaestus'),
    'json_files/beiersdorf_jobs.json':         ('beiersdorf-jobs',      'beiersdorf'),
    'json_files/spencer_stuart_jobs.json':     ('spencer-stuart-jobs',  'spencer_stuart'),
    'json_files/fresenius_jobs.json':          ('fresenius-jobs',       'fresenius'),
    'json_files/vandegroep_jobs.json':         ('vandegroep-jobs',      'vandegroep'),
    'json_files/heidrick_jobs.json':           ('heidrick-jobs',        'heidrick'),
    'json_files/adc_jobs.json':                ('adc-jobs',             'adc'),
    'json_files/carplai_jobs.json':            ('carplai-jobs',         'carplai'),
    'json_files/ceramtec_jobs.json':            ('ceramtec-jobs',         'ceramtec'),
    'json_files/incepto_jobs.json':            ('incepto-jobs',         'incepto'),
    'json_files/radiobotics_jobs.json':        ('radiobotics-jobs',     'radiobotics'),
    'json_files/hologic_jobs.json':            ('hologic-jobs',         'hologic'),
    'json_files/sectra_jobs.json':            ('sectra-jobs',         'sectra'),
    'json_files/iqvia_jobs.json':             ('iqvia-jobs',          'iqvia'),
    'json_files/ely_lily_jobs.json':         ('ely_lily-jobs',       'ely_lily'),
    'json_files/falck_jobs.json':             ('falck-jobs',          'falck'),
    'json_files/draeger_jobs.json':            ('draeger-jobs',         'draeger'),
    'json_files/novonordisk_jobs.json':        ('novonordisk-jobs',     'novonordisk'),
    'json_files/unilabs_jobs.json':            ('unilabs-jobs',         'unilabs'),
    'json_files/abott_jobs.json':             ('abott-jobs',          'abott'),
    'json_files/international_sos_jobs.json':       ('international-sos-jobs', 'international_sos'),
    'json_files/philips_jobs.json':            ('philips-jobs',         'philips'), 
    'json_files/sanday_jobs.json':             ('sanday-jobs',          'sanday'),
    'json_files/doctolib_jobs.json':          ('doctolib-jobs',        'doctolib'),
    'json_files/igh_jobs.json':              ('igh-jobs',            'igh'),
    'json_files/adj_jobs.json':             ('adj-jobs',            'adj'),
    'json_files/enddeblauw_jobs.json':     ('enddeblauw-jobs',     'enddeblauw'),
    'json_files/galan_groep_jobs.json':     ('galan-groep-jobs',     'galan_groep'),
    'json_files/bureau_blauw_jobs.json':     ('bureau-blauw-jobs',     'bureau_blauw'),
    'json_files/kv_jobs.json':             ('kv-jobs',             'kv'),
    'json_files/leuwendal_advice_jobs.json':     ('leuwendal-advice-jobs',     'leuwendal_advice'),
    'json_files/korn_ferry_jobs.json':     ('korn-ferry-jobs',     'korn_ferry'),
    'json_files/mercuriurval_jobs.json':     ('mercuri-urval-jobs',     'mercuri_urval'),
    'json_files/meussen_jobs.json':     ('meussen-search-jobs',     'meussen_search'),
    'json_files/pageexecutive_jobs.json':     ('pageexecutive-jobs',     'pageexecutive'),
    'json_files/riekenoomen_jobs.json':     ('riekenoomen-jobs',     'riekenoomen'),
    'json_files/vromvan_jobs.json':     ('vromvan-jobs',     'vromvan'),
    'json_files/perret_laver_jobs.json':     ('perret-laver-jobs',     'perret_laver'),
    'json_files/elcg_jobs.json':     ('elcg-jobs',     'elcg'),
    'json_files/logex_jobs.json':     ('logex-jobs',     'logex'),
    'json_files/talentmark_jobs.json':     ('talentmark-jobs',     'talentmark'),
    'json_files/beonemedicine_jobs.json':     ('beonemedicine-jobs',     'beonemedicine'),
    'json_files/bricegroup_jobs.json':     ('bricegroup-jobs',     'bricegroup'),
    'json_files/demcon_jobs.json':     ('demcon-jobs',     'demcon'),
    'json_files/thermo_fischer_jobs.json':     ('thermo-fischer-jobs',     'thermo_fischer'),
}


def _extra_metadata(job):
    """
    Extra metadata fields common to the new scrapers
    (straumann, thema group, career openers).
    """
    return {
        'job_id':   job.get('job_id', ''),
        'city':         job.get('city', ''),
        'country':      job.get('country', ''),
        'job_type':     job.get('job_type', ''),
        'posted_date':  job.get('posted_date', ''),
        'category':     job.get('category', ''),
        'department':   job.get('department', ''),
        'source':       job.get('source', ''),
    }


def upsert_jobs_from_file(json_file_path, namespace, source_prefix):
    """
    Single reusable function:
      1. Load jobs from JSON
      2. Deduplicate against Pinecone (new / status-changed / unchanged)
      3. Embed and upsert new jobs
      4. Update status-only changes without re-embedding
    """
    label = source_prefix.upper()

    print(f"\n{'='*60}")
    print(f"[{label}] Loading jobs from {json_file_path}...")

    if not os.path.exists(json_file_path):
        print(f"[{label}] File not found: {json_file_path}. Skipping.")
        return

    jobs = load_jobs_from_json(json_file_path)
    print(f"[{label}] Loaded {len(jobs)} jobs")

    embedding_service = EmbeddingService()
    existing_jobs = get_existing_jobs_with_status_from_pinecone(embedding_service, namespace)

    new_jobs           = []
    status_update_jobs = []
    skipped            = 0

    for job in jobs:
        job_id      = job.get('job_id')
        json_status = job.get('status', 'active')

        if job_id not in existing_jobs:
            new_jobs.append(job)
        elif existing_jobs[job_id] != json_status:
            status_update_jobs.append(job)
        else:
            skipped += 1

    print(
        f"[{label}] Total: {len(jobs)} | "
        f"New: {len(new_jobs)} | "
        f"Status changed: {len(status_update_jobs)} | "
        f"Unchanged (skipped): {skipped}"
    )

    if new_jobs:
        vectors = _build_vectors(new_jobs, source_prefix, embedding_service,_extra_metadata)
        print(f"[{label}] Upserting {len(vectors)} new vectors → namespace '{namespace}'...")
        upserted = batch_upsert_vectors(embedding_service, vectors, namespace)
        print(f"[{label}] ✓ Upserted {upserted} new job embeddings")

    if status_update_jobs:
        _update_job_statuses(status_update_jobs, source_prefix, namespace)

    if not new_jobs and not status_update_jobs:
        print(f"[{label}] No new or updated jobs to process.")

    stats = embedding_service.get_index_stats(namespace=namespace)
    count = stats.get('namespaces', {}).get(namespace, {}).get('vector_count', 'N/A')
    print(f"[{label}] Total vectors in namespace '{namespace}': {count}")


def run_all():
    """Process every configured source file."""
    for json_file, (namespace, prefix) in SOURCE_CONFIG.items():
        upsert_jobs_from_file(json_file, namespace, prefix)


if __name__ == '__main__':
    run_all()