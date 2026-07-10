from fastapi import APIRouter,Depends, HTTPException
from auth.auth_utils import get_current_user
from utils import embedding_service
from starlette import status
from fastapi.responses import JSONResponse
from datetime import datetime, timezone

router = APIRouter()


@router.get("/dashboard_stats", status_code=status.HTTP_200_OK)
async def get_dashboard_stats(current_user=Depends(get_current_user)):
    """
    Returns dashboard statistics for the current user:
    - Average match score from results namespace (active jobs only)
    - Total matched jobs count (active jobs only)
    - CV analyzed count (static)
    - Active scrapes count (static)
    """
    try:
        dummy_vector = [0.0] * embedding_service.embedding_dimension

        response = embedding_service.index.query(
            vector=dummy_vector,
            top_k=10000,
            namespace='results',
            include_metadata=True,
        )

        matches = response.matches if hasattr(response, 'matches') else []

        print(f"[DASHBOARD] Total matches found: {len(matches)}")

        # Extract all match scores (active jobs only)
        match_scores = []
        active_count = 0
        for match in matches:
            metadata = match.metadata if hasattr(match, 'metadata') else {}
            meta = metadata if isinstance(metadata, dict) else dict(metadata)

            # Skip expired jobs
            status_val = meta.get('status', '').strip().lower()
            if status_val == 'expired':
                continue

            active_count += 1
            score = meta.get('match_score')
            if score is not None:
                try:
                    match_scores.append(float(score))
                except (ValueError, TypeError):
                    continue

        total_jobs = active_count
        average_match_score = round(sum(match_scores) / len(match_scores), 2) if match_scores else 0

        print(f"[DASHBOARD] Active jobs: {active_count}, Expired jobs skipped: {len(matches) - active_count}")

        return JSONResponse(
            content={
                'success': True,
                'stats': {
                    'average_match_score': average_match_score,
                    'total_matched_jobs': total_jobs,
                    'cv_analyzed': 1,
                    'active_scrapes': 3,
                }
            },
            status_code=status.HTTP_200_OK
        )

    except Exception as e:
        return JSONResponse(
            content={'success': False, 'error': str(e)},
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )



@router.get("/dashboard_graph", status_code=status.HTTP_200_OK)
async def get_dashboard_graph(current_user=Depends(get_current_user)):
    """
    Returns monthly job match distribution for dashboard graph (active jobs only).
    - Groups results by month name based on 'date' field in metadata
    - Returns count of jobs per month
    - Returns count of jobs per source (indeed, linkedin, lintberg, etc.)
    - Expired jobs are excluded from all counts
    """
    try:
        dummy_vector = [0.0] * embedding_service.embedding_dimension

        response = embedding_service.index.query(
            vector=dummy_vector,
            top_k=10000,
            namespace='results',
            include_metadata=True,
        )

        matches = response.matches if hasattr(response, 'matches') else []

        print(f"[DASHBOARD GRAPH] Total matches found: {len(matches)}")

        month_map = {
            1: "January", 2: "February", 3: "March",
            4: "April", 5: "May", 6: "June",
            7: "July", 8: "August", 9: "September",
            10: "October", 11: "November", 12: "December"
        }

        month_counts = {}  # { "March 2026": {"indeed": 0, "linkedin": 0, "lintberg": 0} }
        source_counts = {"indeed": 0, "linkedin": 0, "lintberg": 0}  # Pre-initialize known sources with 0
        skipped_expired = 0

        for match in matches:
            metadata = match.metadata if hasattr(match, 'metadata') else {}
            meta = metadata if isinstance(metadata, dict) else dict(metadata)

            # Skip expired jobs
            status_val = meta.get('status', '').strip().lower()
            if status_val == 'expired':
                skipped_expired += 1
                continue

            # --- Source extraction (shared for both month and source count) ---
            source = meta.get('source', '').strip().lower() if meta.get('source') else 'unknown'

            # --- Monthly count logic ---
            date_str = meta.get('date', '')

            if date_str:
                try:
                    date_str = str(date_str).strip()

                    if '-' in date_str and date_str.count('-') == 2:
                        parsed_date = datetime.strptime(date_str[:10], '%Y-%m-%d')

                    elif '/' in date_str:
                        parts = date_str.split('/')
                        if len(parts[2]) == 4:
                            parsed_date = datetime.strptime(date_str[:10], '%m/%d/%Y')
                        else:
                            parsed_date = None

                    else:
                        parsed_date = None

                    if parsed_date:
                        month_name = month_map[parsed_date.month]
                        year = parsed_date.year
                        key = f"{month_name} {year}"

                        # Initialize month with all known sources at 0
                        if key not in month_counts:
                            month_counts[key] = {"indeed": 0, "linkedin": 0, "lintberg": 0}

                        # Increment source count for this month
                        if source in month_counts[key]:
                            month_counts[key][source] += 1
                        else:
                            month_counts[key][source] = 1

                except (ValueError, KeyError) as e:
                    print(f"[DASHBOARD GRAPH] Could not parse date: {date_str} — {e}")

            # --- Source count logic ---
            if source and source != 'unknown':
                source_counts[source] = source_counts.get(source, 0) + 1

        print(f"[DASHBOARD GRAPH] Skipped expired jobs: {skipped_expired}, Active jobs counted: {len(matches) - skipped_expired}")

        # Sort months chronologically
        def sort_key(k):
            month_name, year = k.rsplit(' ', 1)
            month_num = list(month_map.values()).index(month_name) + 1
            return (int(year), month_num)

        sorted_months = sorted(month_counts.keys(), key=sort_key)

        graph_data = [
            {
                "month": month,
                "indeed": month_counts[month].get("indeed", 0),
                "linkedin": month_counts[month].get("linkedin", 0),
                "lintberg": month_counts[month].get("lintberg", 0),
            }
            for month in sorted_months
        ]

        # Build source data list
        source_data = [
            {
                "source": source,
                "total_jobs": count
            }
            for source, count in sorted(source_counts.items(), key=lambda x: x[1], reverse=True)
        ]

        return JSONResponse(
            content={
                'success': True,
                'total_records': len(matches) - skipped_expired,
                'graph_data': graph_data,
                'source_data': source_data,
            },
            status_code=status.HTTP_200_OK
        )

    except Exception as e:
        return JSONResponse(
            content={'success': False, 'error': str(e)},
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

        