from pinecone import Pinecone
import uuid
from datetime import datetime
from auth.schema import User, UserCreate
from auth.auth_utils import get_password_hash, verify_password
import json
import hashlib
from typing import Optional



class UserService:
    def __init__(self, api_key: str, index_name:str):
        self.pc = Pinecone(api_key=api_key)
        self.index_name = index_name
        self.index = self.pc.Index(index_name)
    
    def _generate_embedding(self, user_id: str, email: str) -> list:
        """Generate a simple hash-based embedding"""
        combined = f"{user_id}:{email}"
        hash_obj = hashlib.sha256(combined.encode())
        hash_bytes = hash_obj.digest()
        
        # Convert to floats between -1 and 1
        embedding = []
        for i in range(0, len(hash_bytes), 2):
            if i + 1 < len(hash_bytes):
                val = int.from_bytes(hash_bytes[i:i+2], 'big')
                embedding.append((val / 65535.0) * 2 - 1)
        
        # Pad to 1536 dimensions (or your index dimension)
        while len(embedding) < 1536:
            embedding.append(0.1)  # Small non-zero value
        
        return embedding[:1536]
    
    def create_superuser(self, user_data: UserCreate) -> User:
        user_id = str(uuid.uuid4())
        now = datetime.utcnow()
        
        user = User(
            id=user_id,
            email=user_data.email,
            username=user_data.username,
            hashed_password=get_password_hash(user_data.password),
            is_superuser=True,
            created_at=now,
            updated_at=now
        )
        
        # Generate embedding
        embedding = self._generate_embedding(user_id, user.email)
        
        # Store in Pinecone
        self.index.upsert(
            vectors=[{
                "id": user_id,
                "values": embedding,
                "metadata": {
                    "email": user.email,
                    "username": user.username,
                    "hashed_password": user.hashed_password,
                    "is_superuser": user.is_superuser,
                    "created_at": user.created_at.isoformat(),
                    "updated_at": user.updated_at.isoformat()
                }
            }],
            namespace="users"
        )
        
        return user
    
    def get_user_by_id(self, user_id: str) -> Optional[User]:
        result = self.index.fetch(ids=[user_id], namespace="users")
        
        if user_id not in result.get('vectors', {}):
            return None
        
        metadata = result['vectors'][user_id]['metadata']
        return User(
            id=user_id,
            email=metadata['email'],
            username=metadata['username'],
            hashed_password=metadata['hashed_password'],
            is_superuser=metadata['is_superuser'],
            created_at=datetime.fromisoformat(metadata['created_at']),
            updated_at=datetime.fromisoformat(metadata['updated_at'])
        )
    
    def get_user_by_email(self, email: str) -> Optional[User]:
        # Query to find user by email
        # Use a small non-zero vector for querying
        query_vector = [0.1] * 1536
        
        results = self.index.query(
            vector=query_vector,
            filter={"email": {"$eq": email}},
            top_k=1,
            namespace="users",
            include_metadata=True
        )
        
        if not results.matches:
            return None
        
        match = results.matches[0]
        return User(
            id=match.id,
            email=match.metadata['email'],
            username=match.metadata['username'],
            hashed_password=match.metadata['hashed_password'],
            is_superuser=match.metadata['is_superuser'],
            created_at=datetime.fromisoformat(match.metadata['created_at']),
            updated_at=datetime.fromisoformat(match.metadata['updated_at'])
        )
    
    def authenticate_user(self, email: str, password: str) -> Optional[User]:
        user = self.get_user_by_email(email)
        if not user:
            return None
        if not verify_password(password, user.hashed_password):
            return None
        return user