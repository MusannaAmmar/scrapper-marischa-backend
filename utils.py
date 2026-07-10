# https://www.linkedin.com/jobs/search/?currentJobId=4374733450&geoId=102890719&keywords=Managing%20Director&trk=d_flagship3_salary_explorer


from database.pinecone import EmbeddingService


embedding_service = EmbeddingService()





def get_query_cv_profiles(query: str, top_k: int = 15) -> list[dict]:
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
            'file_path':      metadata.get('file_path', ''),
        })

    return profiles



