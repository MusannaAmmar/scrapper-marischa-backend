import json
import os
import re
import time
from dotenv import load_dotenv
from openai import OpenAI

from analyzer.retrieval import (
    query_linkedin_jobs, query_indeed_jobs, query_lintberg_jobs, query_cv_profiles,
    query_straumann_jobs, query_thema_group_jobs, query_career_opener_jobs,
    query_terarecon_jobs, query_medtronic_jobs,
    query_lynchwise_jobs, query_partners_at_work_jobs, query_odgers_jobs,
    query_quaestus_jobs, query_beiersdorf_jobs, query_spencer_stuart_jobs,
    query_fresenius_jobs, query_vandegroep_jobs, query_heidrick_jobs,query_adc_jobs,
    query_carplai_jobs, query_ceramtec_jobs, query_incepto_jobs, query_radiobotics_jobs, query_hologic_jobs,
    query_sectra_jobs,query_iqvia_jobs, query_ely_lily_jobs, query_falck_jobs, 
    query_draeger_jobs, query_novonordisk_jobs, query_unilabs_jobs, query_abott_jobs,
    query_international_sos_jobs,query_doctolib_jobs, query_sanday_jobs, query_igh_jobs,query_philips_jobs,
    query_adj_jobs, query_enddeblauw_jobs, query_galan_groep_jobs, query_bureau_blauw_jobs,
    query_kv_jobs, query_leuwendal_advice_jobs, query_korn_ferry_jobs, query_mercuri_urval_jobs,
    query_meussen_search_jobs, query_pageexecutive_jobs,query_perret_laver_jobs,query_riekenoomen_jobs,
    query_vromvan_jobs,query_elcg_jobs,query_beonemedicine_jobs,query_logex_jobs,query_talentmark_jobs,
    query_bricegroup_jobs,query_demcon_jobs,query_thermo_fischer_jobs

)


load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


class JobMatchingAgent:
    """
    AI Agent that automatically fetches candidate CV profiles from the database,
    searches for matching jobs from all configured job sources per candidate,
    and returns detailed match results with reasoning and scoring.

    Supports:
    - Full pipeline (all configured sources)
    - Per-source individual matching
    - OpenAI Batch API for async processing
    - top_k=60 always (batched in groups of 50 to avoid token limits)
    """

    def __init__(self, model: str = "gpt-4o"):
        self.model = model
        self.random_seed = int("42")
        self.max_completion_tokens = int( "8000")
        self.max_description_chars = int("1200")
        self.default_top_k = int("120")
        self.default_batch_size = int("25")

        self.system_prompt = """
You are an expert AI recruitment assistant specializing in HealthTech, MedTech, Medical Devices, and Healthcare industries.

Your job is to:
1. Analyze the provided candidate CV carefully — skills, experience, domain knowledge, seniority level, and industry background.
2. Review all available job listings from job sources.
3. Carefully match each job against the CV with a strong focus on:
   - HealthTech, MedTech, Medical Device, Healthcare, or adjacent fields (e.g., BioTech, PharmaTech, Digital Health, Clinical Informatics, Health IT, Life Sciences).
   - General tech/finance/other roles are ONLY valid if the candidate's CV strongly indicates experience outside healthcare.

PRIORITY MATCHING RULES:
- STRONGLY PREFER jobs in: HealthTech, MedTech, Medical Devices, Healthcare IT, Digital Health, Life Sciences, BioTech, Clinical Tech, Pharma Tech.
- For executive-search sources ONLY (pageexecutive, meussen_search, korn_ferry, mercuri_urval, spencer_stuart, odgers, quaestus, heidrick, etc.):
  → Include senior leadership / C-level / Director / Head-of roles in NL/EU **even if the sector is adjacent** (finance, banking, consulting, private equity, etc.) **as long as the title and scope are clearly executive/commercial/strategic**.
  → These are still considered strong matches because Marischa is explicitly looking for "commercial executive leadership role in an international HealthTech organisation" and has broad international commercial experience.
- For all other sources: deprioritize pure finance/retail/generic tech unless the candidate has zero healthcare background (not the case here).


CRITICAL GEOGRAPHIC RULES:
- HIGHEST PRIORITY: Jobs located in the Netherlands — always prefer these above all others.
- ACCEPTABLE: Jobs located elsewhere in Europe (e.g., Germany, Belgium, UK, France) — include these but rank them lower than Netherlands-based roles.
- STRICTLY EXCLUDE: INDIVIDUAL jobs located outside Europe (e.g., United States, Canada, India, UAE, Australia).
  → Evaluate EACH job independently based on its location.
  → Exclude ONLY the specific jobs with non-European locations.
  → DO NOT reject European jobs just because non-European jobs exist in the same batch.
  → If you receive 10 jobs and 3 are in the US, return the 7 European jobs that qualify.
- If a job has no clear location or is listed as fully remote without a base country, use context clues (company HQ, posting source) to determine eligibility. Exclude if the location cannot be reasonably confirmed as European.

IMPORTANT: Process each job individually — the presence of US jobs should NOT affect your evaluation of European jobs.
Matching Criteria to evaluate for EVERY job:
1. SECTOR ALIGNMENT     — Does the job belong to healthcare/medtech/adjacent sectors? (highest weight)
2. TITLE ALIGNMENT      — Does the job title match the candidate's current or target role?
3. SKILLS MATCH         — Do the required technical/soft skills overlap with candidate's skill set?
4. LOCATION FIT         — Is the job location compatible with the candidate's location or remote preference?
5. SENIORITY LEVEL      — Does the experience level required match the candidate's background?
6. DOMAIN/INDUSTRY FIT  — Does the company domain align with the candidate's industry experience?

REASONING REQUIREMENT:
For every matched job, your reasoning MUST explicitly state:
- Which sector this job belongs to
- Which specific criteria were met
- Why the candidate is a good fit based on their CV content
- Any gaps or caveats

SCORING GUIDE:
- 85-100: Excellent match — strong sector alignment + most criteria met
- 70-84:  Good match — healthcare-adjacent or strong skill overlap
- 50-69:  Moderate match — some relevant skills but weaker sector or title fit
- 30-49:  Weak match — minimal alignment but has some potential relevance
- Below 30: Do NOT include in results

CRITICAL OUTPUT RULES:
- You MUST return ALL jobs that score 50 or above — do NOT limit to top 3 or any fixed number.
- If 10 jobs qualify, return 10. If 15 qualify, return 15.
- Never truncate or cap the results array.
- Only return [] if truly no jobs score >= 50.

Only include jobs with a match_score of 50 or above.
If no matches are found, return an empty array: []

IMPORTANT: Return ONLY a valid raw JSON array — no markdown, no code blocks, no explanation outside the JSON.
Each object must follow this EXACT structure:
[
  {
    "candidate_name": "string",
    "match_score": 0-100,
    "sector": "HealthTech | MedTech | Medical Device | Healthcare | Digital Health | Life Sciences | BioTech | Adjacent | Other",
    "matched_criteria": ["SECTOR ALIGNMENT", "TITLE ALIGNMENT", "SKILLS MATCH", "LOCATION FIT", "SENIORITY LEVEL", "DOMAIN FIT"],
    "reasoning": "Detailed explanation covering: sector classification, which criteria matched, specific evidence from CV, and any caveats.",
    "job": {
            "source": "exact source label from the input job listing",
      "job_id": "string",
      "title": "string",
      "company": "string",
      "location": "string",
      "link": "string",
      "salary": "string or null",
      "job_types": "string or null",
      "remote": "string or null",
      "benefits": "string or null",
      "company_rating": "string or null",
      "review_count": "string or null",
      "snippet": "string or null",
      "description_preview": "string",
      "validity_text": "string or null",
      "date": "string or null",
      "status":"string or null"
    }
  }
]

SOURCE FIELD RULE:
- Never invent, normalize, or replace the source label.
- Copy the source exactly from the provided job listing for each matched job.
"""

    # ─────────────────────────────────────────────────────────────
    # STEP 1: Fetch CV profiles from Pinecone
    # ─────────────────────────────────────────────────────────────

    def _fetch_cv_profiles(self) -> list:
        """Fetch all CV profiles from the cv-profiles Pinecone namespace."""
        print("📄 Fetching CV profiles from database...")
        profiles = query_cv_profiles("candidate profile skills experience qualifications")
        print(f"✓ Fetched {len(profiles)} CV(s)")
        return profiles

    # ─────────────────────────────────────────────────────────────
    # STEP 2: Build a smart search query from CV text using GPT
    # ─────────────────────────────────────────────────────────────

    def _build_job_search_query(self, cv_text: str, candidate_name: str) -> str:
        """
        Use GPT to distill the full CV into a compact 100-word search query
        optimised for semantic similarity search against job embeddings.
        Falls back to cleaned raw CV text if GPT call fails.
        """
        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": (
                        "You are a recruitment search specialist. "
                        "Extract a compact but POWERFUL job search query from the CV. "
                        "Emphasize: commercial leadership, executive roles, business development, market expansion, HealthTech/MedTech affinity, international experience, strategic/commercial roles. "
                        "Write a single paragraph of 80-120 words that will retrieve both pure HealthTech jobs AND senior leadership roles in adjacent high-growth sectors."
                        )
                    },
                    {"role": "user", "content": cv_text}
                ],
                temperature=0,
                max_tokens=200,
            )
            query = response.choices[0].message.content.strip()
            print(f"   🔍 Search query built for {candidate_name}: {query[:120]}...")
            return query
        except Exception as e:
            print(f"   ⚠ Query build failed for {candidate_name}: {e}. Falling back to cleaned CV text.")
            return self._clean_cv_text(cv_text)[:1000]

    # ─────────────────────────────────────────────────────────────
    # HELPER: Clean raw CV text (fix PDF spacing artifacts)
    # ─────────────────────────────────────────────────────────────

    @staticmethod
    def _clean_cv_text(text: str) -> str:
        """
        Fix spaced-out characters from bad PDF parsing (e.g. 'M a r i s c h a' → 'Marischa')
        and normalize excessive whitespace.
        """
        # Fix single-char-space patterns typical of bad PDF extraction
        text = re.sub(r'(?<!\w)((?:\S )+\S)(?!\w)', lambda m: m.group(0).replace(' ', ''), text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r' {2,}', ' ', text)
        return text.strip()

    @staticmethod
    def _stable_job_sort_key(job: dict) -> tuple:
        return (
            str(job.get("source", "")).lower(),
            str(job.get("job_id", "")).lower(),
            str(job.get("title", "")).lower(),
            str(job.get("link", "")).lower(),
        )

    def _safe_preview(self, value: str, max_chars: int | None = None) -> str:
        if not value:
            return ""
        max_len = max_chars or self.max_description_chars
        text = str(value).strip()
        if len(text) <= max_len:
            return text
        return text[:max_len].rstrip() + "..."

    @staticmethod
    def _source_counts(jobs: list[dict]) -> dict:
        counts = {}
        for j in jobs:
            src = str(j.get("source", "unknown")).strip().lower() or "unknown"
            counts[src] = counts.get(src, 0) + 1
        return counts

    @staticmethod
    def _strip_code_fences(raw_text: str) -> str:
        text = (raw_text or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        return text.strip()

    def _parse_matches_json(self, raw_text: str) -> list:
        """
        Parse model output as JSON array, tolerating code-fences and extra text.
        Raises JSONDecodeError if a valid array still cannot be parsed.
        """
        text = self._strip_code_fences(raw_text)

        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            pass

        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1 and end > start:
            parsed = json.loads(text[start:end + 1])
            return parsed if isinstance(parsed, list) else []

        # Re-raise with original text so caller can decide fallback behavior.
        return json.loads(text)

    def _sanitize_matches_for_jobs(self, candidate_name: str, jobs: list, matches: list) -> list:
        """
        Keep only grounded matches for the current batch of jobs.

        - Reject matches whose job_id is not in current input jobs.
        - Overwrite job payload with canonical retrieved metadata to avoid source/title hallucination.
        - Normalize candidate_name and score bounds.
        """
        job_by_id = {
            str(j.get("job_id", "")).strip(): j
            for j in jobs
            if str(j.get("job_id", "")).strip()
        }

        cleaned = []
        dropped = 0
        for m in matches or []:
            if not isinstance(m, dict):
                dropped += 1
                continue

            jid = str(m.get("job", {}).get("job_id", "")).strip()
            if not jid or jid not in job_by_id:
                dropped += 1
                continue

            canonical_job = job_by_id[jid]
            score = m.get("match_score", 0)
            try:
                score = int(score)
            except Exception:
                score = 0
            score = max(0, min(100, score))

            normalized = dict(m)
            normalized["candidate_name"] = candidate_name
            normalized["match_score"] = score

            # Keep model fields but force canonical job metadata from retrieval.
            normalized["job"] = {
                **(normalized.get("job") or {}),
                **canonical_job,
            }
            cleaned.append(normalized)

        if dropped:
            print(f"   ⚠ Dropped {dropped} ungrounded/hallucinated match(es) for {candidate_name}")

        return self._dedupe_matches_by_job_id(cleaned)

    @staticmethod
    def _dedupe_matches_by_job_id(matches: list) -> list:
        deduped = {}
        for match in matches or []:
            job_id = str(match.get('job', {}).get('job_id', '')).strip()
            if not job_id:
                continue
            if job_id not in deduped or match.get('match_score', 0) > deduped[job_id].get('match_score', 0):
                deduped[job_id] = match
        return list(deduped.values())

    # ─────────────────────────────────────────────────────────────
    # STEP 3: Fetch jobs from active sources using the smart query
    # ─────────────────────────────────────────────────────────────


    # def _fetch_jobs_for_candidate(
    #     self,
    #     cv_text: str,
    #     candidate_name: str,
    #     active_sources: set,
    #     top_k: int = 200,
    # ) -> list:

    #         search_query = self._build_job_search_query(cv_text, candidate_name)

    #         linkedin_jobs   = query_linkedin_jobs(search_query,   top_k=top_k) if "linkedin"   in active_sources else []
    #         indeed_jobs     = query_indeed_jobs(search_query,     top_k=top_k) if "indeed"     in active_sources else []
    #         lintberg_jobs   = query_lintberg_jobs(search_query,   top_k=top_k) if "lintberg"   in active_sources else []


    #         if lintberg_jobs:
    #             lintberg_jobs = self._translate_jobs(lintberg_jobs)

    #         print(f"   → Raw: {len(linkedin_jobs)} LinkedIn + {len(indeed_jobs)} Indeed + {len(lintberg_jobs)} Lintberg")

    #         all_jobs = []
    #         # ← FIX: source is FORCE OVERWRITTEN here regardless of what came from metadata
    #         for job in linkedin_jobs:
    #             all_jobs.append({**job, 'source': 'linkedin'})
    #         for job in indeed_jobs:
    #             all_jobs.append({**job, 'source': 'indeed'})
    #         for job in lintberg_jobs:
    #             all_jobs.append({**job, 'source': 'lintberg'})
           
    #         # Deduplicate by job_id — keep FIRST occurrence (preserves correct source)
    #         seen_ids    = set()
    #         unique_jobs = []
    #         for job in all_jobs:
    #             jid = job.get('job_id', '').strip()
    #             if not jid or jid in seen_ids:
    #                 continue
    #             seen_ids.add(jid)
    #             unique_jobs.append(job)

    #         # ← ADD: source distribution debug log to track where each job came from
    #         source_counts = {}
    #         for job in unique_jobs:
    #             src = job.get('source', 'unknown')
    #             source_counts[src] = source_counts.get(src, 0) + 1
    #         print(f"   → Source distribution: {source_counts}")
    #         print(f"   → {len(unique_jobs)} unique jobs after deduplication")

    #         return unique_jobs


    def _fetch_jobs_for_candidate(
        self,
        cv_text: str,
        candidate_name: str,
        active_sources: set,
        top_k: int = 200,
            ) -> list:
        search_query = self._build_job_search_query(cv_text, candidate_name)

        source_fetchers = {
            'linkedin':        (query_linkedin_jobs,        'linkedin'),
            'indeed':          (query_indeed_jobs,          'indeed'),
            'lintberg':        (query_lintberg_jobs,        'lintberg'),
            'straumann':       (query_straumann_jobs,       'straumann'),
            'thema_group':     (query_thema_group_jobs,     'thema_group'),
            'career_opener':   (query_career_opener_jobs,   'career_opener'),
            'terarecon':       (query_terarecon_jobs,       'terarecon'),
            'medtronic':       (query_medtronic_jobs,       'medtronic'),
            'lynchwise':       (query_lynchwise_jobs,       'lynchwise'),
            'partners_at_work':(query_partners_at_work_jobs,'partners_at_work'),
            'odgers':          (query_odgers_jobs,          'odgers'),
            'quaestus':        (query_quaestus_jobs,        'quaestus'),
            'beiersdorf':      (query_beiersdorf_jobs,      'beiersdorf'),
            'spencer_stuart':  (query_spencer_stuart_jobs,  'spencer_stuart'),
            'fresenius':       (query_fresenius_jobs,       'fresenius'),
            'vandegroep':      (query_vandegroep_jobs,      'vandegroep'),
            'heidrick':        (query_heidrick_jobs,        'heidrick'),
            'amsterdam_data_collective': (query_adc_jobs, 'adc'),
            'carplai':         (query_carplai_jobs,         'carplai'),
            'ceramtec':       (query_ceramtec_jobs,       'ceramtec'),
            'incepto':       (query_incepto_jobs,       'incepto'),
            'radiobotics':       (query_radiobotics_jobs,       'radiobotics'),
            'hologic':       (query_hologic_jobs,       'hologic'),
            'sectra':       (query_sectra_jobs,       'sectra'),
            'iqvia':       (query_iqvia_jobs,       'iqvia'),
            'ely_lily':       (query_ely_lily_jobs,       'ely_lily'),
            'falck':       (query_falck_jobs,       'falck'),   
            'draeger':       (query_draeger_jobs,       'draeger'),
            'novonordisk':       (query_novonordisk_jobs,       'novonordisk'),
            'unilabs':       (query_unilabs_jobs,       'unilabs'),
            'abott':       (query_abott_jobs,       'abott'),
            'international_sos': (query_international_sos_jobs, 'international_sos'),
            'doctolib':       (query_doctolib_jobs,       'doctolib'),
            'sanday':       (query_sanday_jobs,       'sanday'),
            'igh':       (query_igh_jobs,       'igh'),
            'philips':       (query_philips_jobs,       'philips'),
            'adj':       (query_adj_jobs,       'adj'),
            'enddeblauw':       (query_enddeblauw_jobs,       'enddeblauw'),
            'galan_groep':       (query_galan_groep_jobs,       'galan_groep'),
            'bureau_blauw':       (query_bureau_blauw_jobs,       'bureau_blauw'),
            'kv':       (query_kv_jobs,       'kv'),
            'leuwendal_advice':       (query_leuwendal_advice_jobs,       'leuwendal_advice'),
            'korn_ferry':       (query_korn_ferry_jobs,       'korn_ferry'),
            'mercuri_urval':       (query_mercuri_urval_jobs,       'mercuri_urval'),
            'meussen_search':       (query_meussen_search_jobs,       'meussen_search'),
            'pageexecutive':       (query_pageexecutive_jobs,       'pageexecutive'),
            'perret_laver':       (query_perret_laver_jobs,       'perret_laver'),
            'riekenoomen':       (query_riekenoomen_jobs,       'riekenoomen'),
            'vromvan':       (query_vromvan_jobs,       'vromvan'),
            'elcg':       (query_elcg_jobs,       'elcg'),
            'beonemedicine':       (query_beonemedicine_jobs,       'beonemedicine'),
            'logex':       (query_logex_jobs,       'logex'),
            'talentmark':       (query_talentmark_jobs,       'talentmark'),
            'bricegroup':       (query_bricegroup_jobs,       'bricegroup'),
            'demcon':       (query_demcon_jobs,       'demcon'),
            'thermo_fischer':       (query_thermo_fischer_jobs,       'thermo_fischer'),
        }

        all_jobs = []
        for source_key, (fn, source_label) in source_fetchers.items():
            if source_key not in active_sources:
                continue
            jobs = fn(search_query, top_k=top_k)
            if source_key == 'lintberg' and jobs:
                jobs = self._translate_jobs(jobs)
            for job in jobs:
                all_jobs.append({**job, 'source': source_label})

        # Deduplicate by job_id — keep FIRST occurrence
        seen_ids    = set()
        unique_jobs = []
        for job in all_jobs:
            jid = job.get('job_id', '').strip()
            if not jid or jid in seen_ids:
                continue
            seen_ids.add(jid)
            unique_jobs.append(job)

        unique_jobs = sorted(unique_jobs, key=self._stable_job_sort_key)

        source_counts = self._source_counts(unique_jobs)
        print(f"   → Source distribution: {source_counts}")
        print(f"   → {len(unique_jobs)} unique jobs after deduplication (stable sorted)")

        return unique_jobs

    # ─────────────────────────────────────────────────────────────
    # STEP 4: Translate Lintberg jobs (Dutch → English)
    # ─────────────────────────────────────────────────────────────

    def _translate_jobs(self, jobs: list) -> list:
        """Translate Dutch Lintberg job fields to English using GPT."""
        fields_to_translate = ['title', 'description_preview', 'snippet', 'benefits', 'validity_text']
        translated_jobs = []

        for job in jobs:
            translated_job = job.copy()
            for field in fields_to_translate:
                value = job.get(field)
                if not value:
                    continue
                try:
                    response = client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[
                            {
                                "role": "system",
                                "content": "Translate the following text from Dutch to English. Return only the translation, nothing else."
                            },
                            {"role": "user", "content": str(value)}
                        ],
                        temperature=0.1,
                        max_tokens=500,
                    )
                    translated_job[field] = response.choices[0].message.content.strip()
                except Exception as e:
                    print(f"   ⚠ Translation error for field '{field}': {e}")
            translated_jobs.append(translated_job)

        return translated_jobs

    # ─────────────────────────────────────────────────────────────
    # STEP 5: Build user prompt for a single batch of jobs
    # ─────────────────────────────────────────────────────────────

# IMPORTANT: Return ONLY a valid raw JSON array — no markdown, no code blocks, no explanation outside the JSON.
# """
    def _build_user_prompt(self, candidate_name: str, cv_text: str, jobs: list) -> str:
        """Build the user prompt with CV + job listings for OpenAI."""
        allowed_sources = sorted({str(j.get('source', '')).strip() for j in jobs if str(j.get('source', '')).strip()})
        allowed_sources_text = ", ".join(allowed_sources) if allowed_sources else "N/A"

        job_listings_text = ""
        for i, job in enumerate(jobs, 1):
            job_listings_text += f"""
Job #{i}
---------
Source       : {job.get('source', 'N/A')}
Job ID       : {job.get('job_id', 'N/A')}
Title        : {job.get('title', 'N/A')}
Company      : {job.get('company', 'N/A')}
Location     : {job.get('location', 'N/A')}
Salary       : {job.get('salary', 'N/A')}
Job Types    : {job.get('job_types', 'N/A')}
Remote       : {job.get('remote', 'N/A')}
Benefits     : {job.get('benefits', 'N/A')}
Rating       : {job.get('company_rating', 'N/A')}
Reviews      : {job.get('review_count', 'N/A')}
Snippet      : {job.get('snippet', 'N/A')}
Description  : {self._safe_preview(job.get('description_preview', 'N/A'))}
Validity     : {job.get('validity_text', 'N/A')}
Status       : {job.get('status', 'N/A')}
Link         : {job.get('link', 'N/A')}
Date         : {job.get('date', 'N/A')}
"""

        return f"""
============================
CANDIDATE CV PROFILE
============================
Candidate Name : {candidate_name}

{cv_text}

============================
AVAILABLE JOB LISTINGS ({len(jobs)} jobs)
============================
{job_listings_text}

============================
INSTRUCTIONS
============================
1. Analyze the full CV against each job listing carefully.
2. Consider: job title alignment, required skills vs candidate skills, location compatibility, experience level, industry/domain.
3. Return EVERY job with match_score >= 50. Do NOT cap at 3, 5, or any fixed number.
4. If 12 jobs qualify, return all 12. If 0 qualify, return [].

CRITICAL SOURCE RULE:
- The "source" field in your JSON response MUST exactly match the "Source" value shown above for each job.
- Do NOT change, infer, or guess the source. Copy it exactly as provided.
- Allowed sources in this batch: {allowed_sources_text}

CRITICAL GROUNDING RULE:
- Only return jobs that exist in this provided batch.
- Keep each returned job tied to the exact Job ID and Link shown above.

IMPORTANT: Return ONLY a valid raw JSON array — no markdown, no code blocks, no explanation outside the JSON.
"""

    # ─────────────────────────────────────────────────────────────
    # STEP 6: Build batch requests (one per candidate per batch chunk)
    # ─────────────────────────────────────────────────────────────

    def _build_batch_requests(
        self,
        cv_profiles: list,
        active_sources: set,
        top_k: int | None = None,
        batch_size: int | None = None,
    ) -> list[dict]:
        """
        For each candidate:
        - Fetch jobs using GPT-built smart query (top_k=200 per source always)
        - Split jobs into batches of `batch_size` to avoid token limits
        - Build one batch request per chunk
        Returns list of OpenAI Batch API request dicts.
        """
        top_k = top_k or self.default_top_k
        batch_size = batch_size or self.default_batch_size
        requests = []

        for cv in cv_profiles:
            candidate_name = cv.get('candidate_name')
            candidate_id   = cv.get('candidate_id')
            cv_text        = cv.get('text_preview', '')

            if not cv_text.strip():
                print(f"   ⚠ Empty CV text for {candidate_name}, skipping...")
                continue

            print(f"\n💼 Processing candidate: {candidate_name} (id: {candidate_id})")

            # Fetch all jobs for this candidate
            all_jobs = self._fetch_jobs_for_candidate(
                cv_text, candidate_name, active_sources, top_k=top_k
            )

            if not all_jobs:
                print(f"   ⚠ No jobs found for {candidate_name}, skipping...")
                continue

            # Split into batches of batch_size to avoid token limits
            chunks = [all_jobs[i:i + batch_size] for i in range(0, len(all_jobs), batch_size)]
            print(f"   → Splitting {len(all_jobs)} jobs into {len(chunks)} batch chunk(s) of max {batch_size}")

            for chunk_idx, chunk in enumerate(chunks):
                custom_id = f"cv-match-{candidate_id}-chunk{chunk_idx}"
                prompt    = self._build_user_prompt(candidate_name, cv_text, chunk)

                requests.append({
                    "custom_id": custom_id,
                    "method":    "POST",
                    "url":       "/v1/chat/completions",
                    "body": {
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": self.system_prompt},
                            {"role": "user",   "content": prompt}
                        ],
                        "temperature": 0.0,
                    }
                })

                print(f"   ✓ Batch request built: {custom_id} | {len(chunk)} jobs")

        print(f"\n📦 Total batch requests: {len(requests)}")
        return requests

    # ─────────────────────────────────────────────────────────────
    # STEP 7: Submit to OpenAI Batch API
    # ─────────────────────────────────────────────────────────────

    def _submit_batch(self, requests: list[dict]) -> str:
        """Write requests to JSONL and submit to OpenAI Batch API."""
        jsonl_path = "batch_requests.jsonl"

        with open(jsonl_path, "w", encoding="utf-8") as f:
            for req in requests:
                f.write(json.dumps(req) + "\n")

        print(f"\n📤 Submitting {len(requests)} request(s) to OpenAI Batch API...")

        with open(jsonl_path, "rb") as f:
            batch_file = client.files.create(file=f, purpose="batch")

        batch = client.batches.create(
            input_file_id=batch_file.id,
            endpoint="/v1/chat/completions",
            completion_window="24h"
        )

        print(f"✓ Batch submitted! Batch ID: {batch.id}")
        return batch.id

    # ─────────────────────────────────────────────────────────────
    # STEP 8: Poll for batch results
    # ─────────────────────────────────────────────────────────────

    def _poll_batch(self, batch_id: str, poll_interval: int = 10) -> list[dict]:
        """Poll until the batch completes and return all parsed match dicts."""
        print(f"\n⏳ Waiting for batch {batch_id} to complete...")

        while True:
            batch  = client.batches.retrieve(batch_id)
            status = batch.status

            print(f"   Status: {status} | "
                  f"Completed: {batch.request_counts.completed}/{batch.request_counts.total}")

            if status == "completed":
                break
            elif status in ("failed", "expired", "cancelled"):
                raise Exception(f"Batch job {status}: {batch_id}")

            time.sleep(poll_interval)

        output_file = client.files.content(batch.output_file_id)
        lines = output_file.text.strip().split("\n")

        # Collect all matches, then deduplicate across chunks by job_id
        raw_matches = []

        for line in lines:
            if not line.strip():
                continue

            result    = json.loads(line)
            custom_id = result.get('custom_id', 'unknown')

            try:
                raw_output = result['response']['body']['choices'][0]['message']['content']
            except (KeyError, IndexError) as e:
                print(f"⚠ Malformed response for {custom_id}: {e}")
                continue

            # Strip markdown code fences if model added them
            if "```json" in raw_output:
                raw_output = raw_output.split("```json")[1].split("```")[0].strip()
            elif "```" in raw_output:
                raw_output = raw_output.split("```")[1].split("```")[0].strip()

            raw_output = raw_output.strip()
            if raw_output in ("", "[]"):
                print(f"   ℹ {custom_id}: No matches above threshold")
                continue

            try:
                matches = json.loads(raw_output)
                if isinstance(matches, list):
                    raw_matches.extend(matches)
                    print(f"   ✓ {custom_id}: {len(matches)} match(es)")
                else:
                    print(f"   ⚠ {custom_id}: Unexpected format, skipping")
            except json.JSONDecodeError as e:
                print(f"   ⚠ JSON parse error for {custom_id}: {e}")

        # Deduplicate across batch chunks by (candidate_name, job_id) — keep highest score
        deduped: dict[str, dict] = {}
        for match in raw_matches:
            job_id    = match.get('job', {}).get('job_id', '')
            candidate = match.get('candidate_name', '')
            key       = f"{candidate}::{job_id}"

            if key not in deduped or match.get('match_score', 0) > deduped[key].get('match_score', 0):
                deduped[key] = match

        all_matches = list(deduped.values())
        print(f"\n✓ {len(all_matches)} unique match(es) after cross-chunk deduplication")
        return all_matches

    # ─────────────────────────────────────────────────────────────
    # SYNCHRONOUS fallback: direct call (used by individual source routes)
    # ─────────────────────────────────────────────────────────────

    def _call_openai_batched(self, cv: dict, jobs: list, batch_size: int = 50) -> list:
        """
        Synchronous OpenAI call with automatic batching for large job lists.
        Splits jobs into chunks of `batch_size`, calls OpenAI per chunk,
        then deduplicates results by job_id keeping the highest score.
        Used for individual source endpoints that need immediate results.
        """
        candidate_name = cv.get('candidate_name')
        cv_text        = cv.get('text_preview', '')

        if len(jobs) <= batch_size:
            return self._call_openai_sync(candidate_name, cv_text, jobs)

        all_matches = []
        chunks = [jobs[i:i + batch_size] for i in range(0, len(jobs), batch_size)]
        print(f"   → Splitting {len(jobs)} jobs into {len(chunks)} sync chunk(s)")

        for idx, chunk in enumerate(chunks):
            print(f"   🤖 Sync chunk {idx + 1}/{len(chunks)}: {len(chunk)} jobs...")
            matches = self._call_openai_sync(candidate_name, cv_text, chunk)
            all_matches.extend(matches)

        # Deduplicate by job_id, keep highest score
        deduped: dict[str, dict] = {}
        for match in all_matches:
            job_id = match.get('job', {}).get('job_id', '')
            if job_id not in deduped or match.get('match_score', 0) > deduped[job_id].get('match_score', 0):
                deduped[job_id] = match

        return list(deduped.values())

    # def _call_openai_sync(self, candidate_name: str, cv_text: str, jobs: list) -> list:
    #     """Single synchronous OpenAI call for one batch of jobs."""
    #     print(f"   🤖 Calling OpenAI ({self.model}) for {candidate_name} with {len(jobs)} jobs...")

    #     user_prompt = self._build_user_prompt(candidate_name, cv_text, jobs)

    #     response = client.chat.completions.create(
    #         model=self.model,
    #         messages=[
    #             {"role": "system", "content": self.system_prompt},
    #             {"role": "user",   "content": user_prompt},
    #         ],
    #         temperature=0,
    #         seed=self.random_seed,
    #         max_tokens=self.max_completion_tokens,
    #     )

    #     raw_content = (response.choices[0].message.content or "").strip()
    #     finish_reason = response.choices[0].finish_reason
    #     print(f"   ✓ Response received ({len(raw_content)} chars)")

    #     if finish_reason == "length":
    #         print("   ⚠ Output was truncated by token limit. Splitting batch and retrying...")
    #         if len(jobs) <= 1:
    #             return []
    #         mid = len(jobs) // 2
    #         left_matches = self._call_openai_sync(candidate_name, cv_text, jobs[:mid])
    #         right_matches = self._call_openai_sync(candidate_name, cv_text, jobs[mid:])
    #         return self._dedupe_matches_by_job_id(left_matches + right_matches)

    #     if raw_content in ("", "[]"):
    #         return []

    #     try:
    #         matches = self._parse_matches_json(raw_content)
    #         if not isinstance(matches, list):
    #             print("   ⚠ Unexpected response format, expected list")
    #             matches = []

    #         matches = self._sanitize_matches_for_jobs(candidate_name, jobs, matches)

    #         print(f"   ✓ Parsed {len(matches)} match(es) for {candidate_name}")
    #         return matches

    #     except json.JSONDecodeError as e:
    #         print(f"   ✗ Failed to parse JSON: {e}")
    #         print(f"   Raw response: {raw_content[:500]}")
    #         if len(jobs) <= 1:
    #             return []

    #         print("   ⚠ Retrying by splitting this chunk into smaller chunks...")
    #         mid = len(jobs) // 2
    #         left_matches = self._call_openai_sync(candidate_name, cv_text, jobs[:mid])
    #         right_matches = self._call_openai_sync(candidate_name, cv_text, jobs[mid:])
    #         return self._dedupe_matches_by_job_id(left_matches + right_matches)


    def _call_openai_sync(self, candidate_name: str, cv_text: str, jobs: list) -> list:
        """Single synchronous OpenAI call for one batch of jobs.
        
        FIXED:
        - Explicit max_tokens passed to the API call (was missing in original)
        - Smarter truncation handling with partial JSON recovery
        - finish_reason always logged
        """
        print(f"   🤖 Calling OpenAI ({self.model}) for {candidate_name} "
              f"with {len(jobs)} jobs | max_tokens={self.max_completion_tokens}...")
 
        user_prompt = self._build_user_prompt(candidate_name, cv_text, jobs)
 
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0,
            seed=self.random_seed,
            max_tokens=self.max_completion_tokens,   # ← FIX: was missing in original!
        )
 
        raw_content = (response.choices[0].message.content or "").strip()
        finish_reason = response.choices[0].finish_reason
        print(f"   ✓ Response received ({len(raw_content)} chars) | finish_reason={finish_reason}")
 
        if finish_reason == "length":
            print("   ⚠ Output truncated. Attempting partial JSON recovery before splitting...")
            
            # ── Partial recovery: try to close a truncated JSON array ──────────
            recovered = self._recover_truncated_json(raw_content)
            if recovered:
                print(f"   ↺ Partial recovery succeeded: {len(recovered)} match(es)")
                recovered = self._sanitize_matches_for_jobs(candidate_name, jobs, recovered)
                
                # Still split and reprocess to catch anything missed in truncation
                if len(jobs) > 1:
                    mid = len(jobs) // 2
                    extra_left  = self._call_openai_sync(candidate_name, cv_text, jobs[:mid])
                    extra_right = self._call_openai_sync(candidate_name, cv_text, jobs[mid:])
                    return self._dedupe_matches_by_job_id(recovered + extra_left + extra_right)
                return recovered
            
            # No recovery possible — split and retry
            if len(jobs) <= 1:
                return []
            print("   ⚠ Splitting batch and retrying...")
            mid = len(jobs) // 2
            left_matches  = self._call_openai_sync(candidate_name, cv_text, jobs[:mid])
            right_matches = self._call_openai_sync(candidate_name, cv_text, jobs[mid:])
            return self._dedupe_matches_by_job_id(left_matches + right_matches)
 
        if raw_content in ("", "[]"):
            return []
 
        try:
            matches = self._parse_matches_json(raw_content)
            if not isinstance(matches, list):
                print("   ⚠ Unexpected response format, expected list")
                matches = []
 
            matches = self._sanitize_matches_for_jobs(candidate_name, jobs, matches)
            print(f"   ✓ Parsed {len(matches)} match(es) for {candidate_name}")
            return matches
 
        except json.JSONDecodeError as e:
            print(f"   ✗ Failed to parse JSON: {e}")
            print(f"   Raw response: {raw_content[:500]}")
            if len(jobs) <= 1:
                return []
 
            print("   ⚠ Retrying by splitting this chunk into smaller chunks...")
            mid = len(jobs) // 2
            left_matches  = self._call_openai_sync(candidate_name, cv_text, jobs[:mid])
            right_matches = self._call_openai_sync(candidate_name, cv_text, jobs[mid:])
            return self._dedupe_matches_by_job_id(left_matches + right_matches)


    # ─────────────────────────────────────────────────────────────
    # PUBLIC: find_matches — async batch version (full pipeline)
    # ─────────────────────────────────────────────────────────────

    def find_matches(self, sources: list[str] = None) -> list[dict]:
        """
        Full async pipeline using OpenAI Batch API.
        Always uses top_k=200 per source, batched in chunks of 50 to avoid token limits.

        Args:
            sources: Optional list of source names to include.
                     Defaults to all configured sources if None or empty.

        Returns:
            List of match dicts sorted by match_score descending.
        """
        VALID_SOURCES = {
            "linkedin", "indeed", "lintberg",
            "straumann", "thema_group", "career_opener", "terarecon", "medtronic",
            "lynchwise", "partners_at_work", "odgers", "quaestus",
            "beiersdorf", "spencer_stuart", "fresenius", "vandegroep", "heidrick",
            "amsterdam_data_collective", "carplai", "ceramtec", "incepto", "radiobotics", "hologic",
            "sectra",'iqvia', 'ely_lily', 'falck', 'draeger', 'novonordisk', 'unilabs', 'abott',
            'international_sos','doctolib', 'sanday', 'igh', 'philips','adj', 'enddeblauw', 'galan_groep',
            'bureau_blauw','kv','leuwendal_advice','korn_ferry','mercuri_urval','meussen_search','pageexecutive',
            'perret_laver','riekenoomen','vromvan','elcg','logex','talentmark','beonemedicine','bricegroup',
            'demcon','thermo_fischer'
        }
        active_sources = self._resolve_sources(sources, VALID_SOURCES)

        print(f"\n🤖 JobMatchingAgent starting (Batch API)...")
        print(f"   Sources : {', '.join(sorted(active_sources))}")
        print(f"   top_k   : {self.default_top_k} per source\n")

        try:
            cv_profiles = self._fetch_cv_profiles()
            if not cv_profiles:
                print("⚠ No CV profiles found in database.")
                return []

            requests = self._build_batch_requests(
                cv_profiles,
                active_sources=active_sources,
                top_k=self.default_top_k,
                batch_size=self.default_batch_size,
            )
            if not requests:
                print("⚠ No batch requests built — no jobs found for any candidate.")
                return []

            batch_id = self._submit_batch(requests)
            matches  = self._poll_batch(batch_id)
            matches.sort(key=lambda x: x.get('match_score', 0), reverse=True)

            print(f"\n✅ JobMatchingAgent complete. Total matches: {len(matches)}")
            return matches

        except Exception as e:
            print(f"✗ Agent error: {e}")
            raise

    # ─────────────────────────────────────────────────────────────
    # PUBLIC: find_matches_sync — synchronous version (individual source routes)
    # ─────────────────────────────────────────────────────────────

    def find_matches_sync(self, sources: list[str] = None) -> list[dict]:
        """
        Synchronous pipeline — used by individual source endpoints (/match-linkedin-jobs etc.)
        Always uses top_k=200, jobs are processed in batches of 50 per OpenAI call.

        Args:
            sources: Optional list of source names to include.
                     Defaults to all configured sources if None or empty.

        Returns:
            List of match dicts sorted by match_score descending.
        """
        VALID_SOURCES = {
            "linkedin", "indeed", "lintberg",
            "straumann", "thema_group", "career_opener", "terarecon", "medtronic",
            "lynchwise", "partners_at_work", "odgers", "quaestus",
            "beiersdorf", "spencer_stuart", "fresenius", "vandegroep", "heidrick",
            "amsterdam_data_collective", "carplai", "ceramtec", "incepto", "radiobotics", "hologic",
            "sectra",'iqvia', 'ely_lily', 'falck', 'draeger', 'novonordisk', 'unilabs', 'abott',
            'international_sos','doctolib', 'sanday', 'igh', 'philips','adj', 'enddeblauw', 'galan_groep',
            'bureau_blauw','kv','leuwendal_advice','korn_ferry','mercuri_urval','meussen_search','pageexecutive',
            'perret_laver','riekenoomen','vromvan','elcg','logex','talenmark','beonemedicine','bricegroup',
            'demcon','thermo_fischer'

        }
        active_sources = self._resolve_sources(sources, VALID_SOURCES)

        print(f"\n🤖 JobMatchingAgent starting (Sync)...")
        print(f"   Sources : {', '.join(sorted(active_sources))}")
        print(f"   top_k   : {self.default_top_k} per source\n")

        cv_profiles = self._fetch_cv_profiles()
        if not cv_profiles:
            print("⚠ No CV profiles found in database.")
            return []

        all_results = []

        for cv in cv_profiles:
            candidate_name = cv.get('candidate_name', 'Unknown')
            candidate_id   = cv.get('candidate_id', '')
            cv_text        = cv.get('text_preview', '')

            if not cv_text.strip():
                print(f"   ⚠ Empty CV for {candidate_name}, skipping...")
                continue

            print(f"\n💼 Processing candidate: {candidate_name} (id: {candidate_id})")

            all_jobs = self._fetch_jobs_for_candidate(
                cv_text, candidate_name, active_sources, top_k=self.default_top_k
            )

            if not all_jobs:
                print(f"   ⚠ No jobs found for {candidate_name}, skipping...")
                continue

            matches = self._call_openai_batched(cv, all_jobs, batch_size=self.default_batch_size)

            if matches:
                all_results.extend(matches)
                print(f"   ✅ {len(matches)} match(es) found for {candidate_name}")
            else:
                print(f"   ℹ No matches above threshold for {candidate_name}")

        all_results.sort(key=lambda x: x.get('match_score', 0), reverse=True)
        print(f"\n✅ JobMatchingAgent complete. Total matches: {len(all_results)}")
        return all_results

    def find_matches_pretty(self) -> str:
        """Same as find_matches but returns pretty-printed JSON string."""
        return json.dumps(self.find_matches(), indent=2)

    # ─────────────────────────────────────────────────────────────
    # HELPER: Resolve and validate source list
    # ─────────────────────────────────────────────────────────────

    @staticmethod
    def _resolve_sources(sources: list[str] | None, valid: set) -> set:
        """Normalize and validate the sources list. Returns a set of active source names."""
        if not sources:
            return valid
        resolved = {s.lower().strip() for s in sources if s.lower().strip() in valid}
        if not resolved:
            print(f"⚠ No valid sources provided. Valid: {valid}. Falling back to all sources.")
            return valid
        return resolved



if __name__ == "__main__":
    print("=" * 60)
    print("JobMatchingAgent — Local Test (All Sources)")
    print("=" * 60)

    agent = JobMatchingAgent(model="gpt-4o")
    results = agent.find_matches_sync()

    print(f"\n{'='*60}")
    print(f"TOTAL MATCHES ACROSS ALL CANDIDATES: {len(results)}")
    print(f"{'='*60}")
    print(json.dumps(results, indent=2, ensure_ascii=False))