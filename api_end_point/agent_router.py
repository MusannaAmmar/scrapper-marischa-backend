from fastapi import APIRouter, UploadFile, File, Form, Depends
from starlette import status
from starlette.responses import JSONResponse
from cvloader.cv_loader import FileLoader
from cvloader.cv_embedder import create_and_upsert_cv_embeddings
import tempfile
import os
import uuid
import asyncio
from auth.auth_utils import get_current_user

from dotenv import load_dotenv
from utils import embedding_service
from utils import get_query_cv_profiles

from datetime import datetime, timezone


load_dotenv()

router = APIRouter()



@router.post("/upload_cv", status_code=status.HTTP_200_OK)
async def upload_cv(
    file: UploadFile = File(...),
    candidate_name: str = Form(...),
    current_user=Depends(get_current_user)
):
    """
    Upload a CV file (PDF or DOCX) to extract text and store embeddings in the database.

    Form Data:
        - file: The CV file (.pdf or .docx)
        - candidate_id: (optional) Unique ID for the candidate. Auto-generated if not provided.
        - candidate_name: (optional) Name of the candidate.

    Returns:
        JSON object with success status and candidate info.
    """
    # Validate file type
    filename = file.filename or ""
    ext = os.path.splitext(filename)[1].lower()

    if ext not in ['.pdf', '.docx']:
        return JSONResponse(
            content={
                'success': False,
                'error': f"Unsupported file type '{ext}'. Only .pdf and .docx are supported."
            },
            status_code=status.HTTP_400_BAD_REQUEST
        )

    # Auto-generate candidate_id if not provided
    candidate_id = f"cv_{uuid.uuid4().hex[:8]}"

    # Save uploaded file to a temp file (FileLoader needs a file path)
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            tmp.write(await file.read())
            tmp_path = tmp.name

        # Step 1: Extract text using FileLoader
        loader = FileLoader(tmp_path)
        cv_text = loader.text

        if not cv_text or not cv_text.strip():
            return JSONResponse(
                content={
                    'success': False,
                    'error': "No text could be extracted from the uploaded file."
                },
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY
            )

        # Step 2: Create embeddings and upsert to database
        cv_file_paths = [
            {
                'file_path': tmp_path,
                'candidate_name': candidate_name,
                'candidate_id': candidate_id,
            }
        ]
        create_and_upsert_cv_embeddings(cv_file_paths)

        return JSONResponse(
            content={
                'success': True,
                'message': f"CV uploaded and embedded successfully.",
                'candidate_id': candidate_id,
                'candidate_name': candidate_name,
                'file_name': filename,
                'text_length': len(cv_text),
                'uploaded_by': {
                    'user_id': current_user.id,
                    'username': current_user.username,
                    'email': current_user.email
                }

            },
            status_code=status.HTTP_200_OK
        )

    except ValueError as e:
        return JSONResponse(
            content={'success': False, 'error': str(e)},
            status_code=status.HTTP_400_BAD_REQUEST
        )
    except Exception as e:
        return JSONResponse(
            content={'success': False, 'error': str(e)},
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    finally:
        # Clean up temp file
        if 'tmp_path' in locals() and os.path.exists(tmp_path):
            os.remove(tmp_path)





@router.delete("/delete_cv/{candidate_id}", status_code=status.HTTP_200_OK)
async def delete_cv(candidate_id: str, current_user=Depends(get_current_user)):
    """
    Delete a CV from the database using candidate_id.
    """
    try:
        delete_cv=embedding_service.delete_vectors(filter={"candidate_id": candidate_id}, namespace="cv-profiles")


        return JSONResponse(
            content={
                'success': True,
                'message': f"CV with candidate_id '{candidate_id}' deleted successfully.",
                'deleted_by': {
                    'user_id': current_user.id,
                    'username': current_user.username,
                    'email': current_user.email
                }
            },
            status_code=status.HTTP_200_OK
        )

    except Exception as e:
        return JSONResponse(
            content={'success': False, 'error': str(e)},
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )



@router.get("/get_user_cv", status_code=status.HTTP_200_OK)
async def get_user_cv(current_user=Depends(get_current_user)):
    """
    Retrieve the CV profile of the current authenticated user.
    """
    try:
        # Assuming you have a way to link CVs to users, you would query your database here
        # For example, if using Pinecone, you would search for vectors with metadata matching the current user's ID

        # Placeholder for retrieval logic
        cv_profiles = get_query_cv_profiles(query='profile')

        return JSONResponse(
            content={
                'success': True,
                'cv_profiles': cv_profiles,
                'requested_by': {
                    'user_id': current_user.id,
                    'username': current_user.username,
                    'email': current_user.email
                }
            },
            status_code=status.HTTP_200_OK
        )

    except Exception as e:
        return JSONResponse(
            content={'success': False, 'error': str(e)},
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@router.get("/current_user", status_code=status.HTTP_200_OK)
async def get_current_user_info(current_user=Depends(get_current_user)):
    """
    Endpoint to retrieve current authenticated user's information.
    """
    try:
        return JSONResponse(
            content={
                'success': True,
                'user': {
                    'user_id': current_user.id,
                    'username': current_user.username,
                    'email': current_user.email
                }
            },
            status_code=status.HTTP_200_OK
        )
    except Exception as e:
        return JSONResponse(
            content={'success': False, 'error': str(e)},
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@router.get("/get_results", status_code=status.HTTP_200_OK)
async def get_results(
    current_user=Depends(get_current_user),
    page: int = 1,
    page_size: int = 9,
):
    """
    Retrieve saved job match results for the current authenticated user.
    Supports pagination via `page` and `page_size` query parameters.
    Only returns jobs with status != 'expired'.
    Sorted by newest date first.
    """
    try:
        matches = embedding_service.search_similar(
            query_text='job matching results',
            top_k=10000,
            namespace='results',
        )

        def parse_date(value):
            if not value:
                return datetime.min.replace(tzinfo=timezone.utc)

            try:
                return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except Exception:
                pass

            for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d"):
                try:
                    return datetime.strptime(str(value), fmt).replace(tzinfo=timezone.utc)
                except Exception:
                    continue

            return datetime.min.replace(tzinfo=timezone.utc)

        results = []
        for match in matches:
            metadata = match.get('metadata', {})

            if metadata.get('status', 'active') == 'expired':
                continue

            results.append({
                "vector_id": match.get('id'),
                "match_score": metadata.get('match_score'),
                "candidate_name": metadata.get('candidate_name'),
                "reasoning": metadata.get('reasoning'),
                "sector": metadata.get('sector'),
                "matched_criteria": metadata.get('matched_criteria', []),
                "job": {
                    "job_id": metadata.get('job_id'),
                    "title": metadata.get('job_title') or metadata.get('title'),
                    "company": metadata.get('company'),
                    "location": metadata.get('location'),
                    "salary": metadata.get('salary'),
                    "job_types": metadata.get('job_types'),
                    "remote": metadata.get('remote'),
                    "benefits": metadata.get('benefits'),
                    "source": metadata.get('source'),
                    "link": metadata.get('link'),
                    "snippet": metadata.get('snippet'),
                    "status": metadata.get('status'),
                    "description_preview": metadata.get('description_preview'),
                    "date": metadata.get('date'),
                }
            })

        # Sort by newest date first, then by match_score descending
        results.sort(
            key=lambda x: (
                parse_date(x.get("job", {}).get("date")),
                x.get("match_score") or 0
            ),
            reverse=True
        )

        total_results = len(results)
        total_pages   = max(1, -(-total_results // page_size))

        page = max(1, min(page, total_pages))

        start             = (page - 1) * page_size
        end               = start + page_size
        paginated_results = results[start:end]

        return JSONResponse(
            content={
                'success': True,
                'total': total_results,
                'page': page,
                'page_size': page_size,
                'total_pages': total_pages,
                'has_next': page < total_pages,
                'has_previous': page > 1,
                'results': paginated_results,
                'requested_by': {
                    'user_id': current_user.id,
                    'username': current_user.username,
                    'email': current_user.email
                }
            },
            status_code=status.HTTP_200_OK
        )

    except Exception as e:
        return JSONResponse(
            content={'success': False, 'error': str(e)},
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@router.delete("/delete_result/{job_id}", status_code=status.HTTP_200_OK)
async def delete_result(job_id: str, current_user=Depends(get_current_user)):
    """
    Delete a job match result from the 'results' namespace and save job_id to 'deleted-jobs' namespace.
    """
    try:
        # Step 1: Delete from results namespace using metadata filter
        embedding_service.delete_vectors(
            filter={"job_id": job_id},
            namespace="results"
        )

        # Step 2: Save job_id to deleted-jobs namespace
        # Generate a dummy embedding for the job_id text
        dummy_embedding = embedding_service.generate_embedding(f"deleted job {job_id}")
        
        if dummy_embedding is None:
            dummy_embedding = [0.0] * 1536  # Fallback to zero vector
        
        embedding_service.upsert_vectors(
            vectors=[{
                'id': f"deleted_{job_id}_{int(datetime.now(timezone.utc).timestamp())}",
                'embedding': dummy_embedding,
                'metadata': {
                    'job_id': job_id,
                }
            }],
            namespace='deleted-jobs'
        )

        return JSONResponse(
            content={
                'success': True,
                'message': f"Result with job_id '{job_id}' deleted and archived.",
                'job_id': job_id
            },
            status_code=status.HTTP_200_OK
        )

    except Exception as e:
        return JSONResponse(
            content={'success': False, 'error': str(e)},
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )





