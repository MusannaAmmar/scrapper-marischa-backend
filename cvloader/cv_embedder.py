import os
import json
from dotenv import load_dotenv
from cvloader.cv_loader import FileLoader
from utils import embedding_service
import re


load_dotenv()


def clean_cv_text(text: str) -> str:
    """Fix spaced-out characters from bad PDF parsing and normalize whitespace."""
    
    # Fix spaced-out words: "M a r i s c h a" → "Marischa"
    # Pattern: single chars separated by spaces (PDF artifact)
    text = re.sub(r'(?<!\w)((?:\S )+\S)(?!\w)', lambda m: m.group(0).replace(' ', ''), text)
    
    # Normalize excessive whitespace and newlines
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r' {2,}', ' ', text)
    
    return text.strip()



def build_search_query(text: str, candidate_name: str) -> str:
    """Extract the most meaningful lines for use as a Pinecone search query."""
    lines = text.split('\n')
    meaningful = []
    for line in lines:
        line = line.strip()
        # Skip short lines, page numbers, contact info lines
        if len(line) < 20:
            continue
        if re.match(r'^[\d\s\|\+\@\.]+$', line):  # skip pure contact lines
            continue
        meaningful.append(line)
        if len(' '.join(meaningful)) > 800:
            break
    
    query = f"{candidate_name} {' '.join(meaningful)}"
    return query[:1000]


def create_and_upsert_cv_embeddings(cv_file_paths: list[dict]):
    """
    Load CV files (PDF or DOCX), create embeddings, and upsert to Pinecone.
    Each CV is stored as a single vector (no chunking).

    Args:
        cv_file_paths: List of dicts with keys:
            - 'file_path' (str): Path to the CV file (.pdf or .docx)
            - 'candidate_id' (str): Unique ID for the candidate
            - 'candidate_name' (str, optional): Name of the candidate
    """
    print(f"Processing {len(cv_file_paths)} CV file(s)...")

    loaded_cvs = []
    for cv in cv_file_paths:
        file_path = cv.get('file_path')
        candidate_id = cv.get('candidate_id')
        candidate_name = cv.get('candidate_name', '')
        file_ext = os.path.splitext(file_path)[1].lower()

        print(f"  Loading CV: {os.path.basename(file_path)} ...")
        try:
            loader = FileLoader(file_path)
            cv_text = loader.text
            cv_text = clean_cv_text(cv_text)
            if not cv_text or not cv_text.strip():
                print(f"  ⚠ Warning: No text extracted from {file_path}, skipping...")
                continue

            loaded_cvs.append({
                'candidate_id': candidate_id,
                'candidate_name': candidate_name,
                'file_path': file_path,
                'file_name': os.path.basename(file_path),
                'file_type': file_ext.replace('.', ''),
                'text': cv_text
            })
            print(f"  ✓ Extracted {len(cv_text)} characters from {os.path.basename(file_path)}")

        except Exception as e:
            print(f"  ✗ Failed to load {file_path}: {e}")
            continue

    if not loaded_cvs:
        print("No CVs were successfully loaded. Aborting.")
        return

    # ---- Generate one embedding per CV (no chunking) ----
    texts = [cv['text'] for cv in loaded_cvs]
    print(f"\nGenerating embeddings for {len(texts)} CV(s)...")
    embeddings = embedding_service.generate_embeddings_batch(texts)

    vectors = []
    for cv, embedding in zip(loaded_cvs, embeddings):
        if embedding is None:
            print(f"  ⚠ Warning: Failed to generate embedding for {cv['file_name']}, skipping...")
            continue

        # metadata = {
        #     'candidate_id': cv['candidate_id'],
        #     'candidate_name': cv['candidate_name'],
        #     'file_name': cv['file_name'],
        #     'file_type': cv['file_type'],
        #     'file_path': cv['file_path'][:500],
        #     'text_preview': cv['text'],     # first 1000 chars for preview
        #     'text_length': str(len(cv['text'])),
        #     'source': 'cv'
        # }

        metadata = {
            'candidate_id': cv['candidate_id'],
            'candidate_name': cv['candidate_name'],
            'file_name': cv['file_name'],
            'file_type': cv['file_type'],
            'file_path': cv['file_path'][:500],
            'text_preview': cv['text'][:8000],       # cap at 8000 chars
            'search_query': build_search_query(cv['text'], cv['candidate_name']),  # clean query
            'text_length': str(len(cv['text'])),
            'source': 'cv'
            }

        vectors.append({
            'id': f"cv_{cv['candidate_id']}",     # no chunk index — one vector per CV
            'embedding': embedding,
            'metadata': metadata
        })

        print(f"  ✓ Prepared vector for {cv['file_name']} ({len(cv['text'])} chars)")

    print(f"\nUpserting {len(vectors)} CV vector(s) to Pinecone (namespace: cv-profiles)...")
    embedding_service.upsert_vectors(vectors, namespace="cv-profiles")
    print("✓ Successfully upserted CV embeddings!")

    stats = embedding_service.get_index_stats()
    print(f"\nIndex statistics:")
    print(f"Total vectors: {stats.get('total_vector_count', 'N/A')}")
    print(f"Namespaces: {stats.get('namespaces', {})}")


if __name__ == "__main__":
    cv_files = [
        {
            'file_path': r"C:\Users\buzz\Desktop\C.V. Marischa van Zantvoort.pdf",
            'candidate_id': 'cv_002',
            'candidate_name': 'Musanna'
        }
    ]
    create_and_upsert_cv_embeddings(cv_files)