# Create an instance of EmbeddingService and call the method
import os
import pinecone
from pinecone import ServerlessSpec
from dotenv import load_dotenv
from openai import OpenAI
from datetime import datetime

load_dotenv()

class EmbeddingService:
    def __init__(self):
        # Initialize Pinecone
        pinecone_api_key = os.getenv('PINECONE_API_KEY')
        pinecone_env = os.getenv('PINECONE_ENVIRONMENT')
        openai_api_key = os.getenv('OPENAI_API_KEY')
        index_name = os.getenv('PINECONE_INDEX_NAME', 'scrapped-data')

        if not pinecone_api_key or not pinecone_env:
            raise ValueError("Please set PINECONE_API_KEY and PINECONE_ENVIRONMENT in .env file")

        if not openai_api_key:
            raise ValueError("Please set OPENAI_API_KEY in .env file")

        # Initialize OpenAI client
        self.openai_client = OpenAI(api_key=openai_api_key)
        self.embedding_model = os.getenv('OPENAI_EMBEDDING_MODEL', 'text-embedding-3-small')
        
        # text-embedding-3-small has 1536 dimensions by default
        self.embedding_dimension = 1536
        
        print(f"Using OpenAI embedding model: {self.embedding_model}")

        # Create Pinecone instance
        self.pc = pinecone.Pinecone(api_key=pinecone_api_key)

        # Check if index exists
        if index_name not in self.pc.list_indexes().names():
            print(f"Creating Pinecone index: {index_name}")
            self.pc.create_index(
                name=index_name,
                dimension=self.embedding_dimension,  # 1536 for text-embedding-3-small
                metric='cosine',
                spec=ServerlessSpec(
                    cloud='aws',
                    region=pinecone_env
                )
            )
        else:
            print(f"Using existing Pinecone index: {index_name}")

        self.index = self.pc.Index(index_name)

    def generate_embedding(self, text):
        """Generate embedding using OpenAI API"""
        if not text or not isinstance(text, str) or len(text.strip()) == 0:
            return None
        
        try:
            response = self.openai_client.embeddings.create(
                model=self.embedding_model,
                input=text.strip()
            )
            return response.data[0].embedding
        except Exception as e:
            print(f"Error generating embedding: {str(e)}")
            return None

    def generate_embeddings_batch(self, texts):
        """Generate embeddings for a batch of texts using OpenAI"""
        # Filter out None/empty texts
        valid_texts = []
        valid_indices = []

        for i, text in enumerate(texts):
            if text and isinstance(text, str) and len(text.strip()) > 0:
                valid_texts.append(text.strip())
                valid_indices.append(i)

        if not valid_texts:
            return []

        try:
            # OpenAI can handle batches
            response = self.openai_client.embeddings.create(
                model=self.embedding_model,
                input=valid_texts
            )
            
            # Create result list with None for invalid texts
            results = [None] * len(texts)
            for i, embedding_data in enumerate(response.data):
                results[valid_indices[i]] = embedding_data.embedding
            
            return results
        except Exception as e:
            print(f"Error generating batch embeddings: {str(e)}")
            return [None] * len(texts)

    def upsert_vectors(self, vectors, namespace="default"):
        """Upsert vectors to Pinecone"""
        if not vectors:
            return

        # Prepare vectors for upsert
        pinecone_vectors = []
        for vector_data in vectors:
            if vector_data['embedding'] is not None:
                pinecone_vectors.append({
                    'id': vector_data['id'],
                    'values': vector_data['embedding'],
                    'metadata': vector_data['metadata']
                })

        if pinecone_vectors:
            self.index.upsert(vectors=pinecone_vectors, namespace=namespace)
            print(f"Upserted {len(pinecone_vectors)} vectors to namespace '{namespace}'")

    def search_similar(self, query_text, top_k=5, namespace="default"):
        """Search for similar vectors"""
        query_embedding = self.generate_embedding(query_text)
        if query_embedding is None:
            return []

        results = self.index.query(
            vector=query_embedding,
            top_k=top_k,
            namespace=namespace,
            include_metadata=True
        )

        return results['matches']

    def get_index_stats(self, namespace="default"):
        """Get statistics about the index"""
        stats = self.index.describe_index_stats()
        return stats
    
    def delete_vectors(self, filter: dict, namespace: str = None):
        """
        Delete vectors from Pinecone by metadata filter.
        """
        # This assumes you have a Pinecone index instance as self.index
        # Adjust 'index' and filter syntax as needed for your Pinecone client
        try:
            response = self.index.delete(
                filter=filter,
                namespace=namespace
            )

            print(f"Deleted {len(response)} vectors to namespace '{namespace}'")
            return response
        except Exception as e:
            print(f"[ERROR] Failed to delete vectors: {e}")
            return None

    
  