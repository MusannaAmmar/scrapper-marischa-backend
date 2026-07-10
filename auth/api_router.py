from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from auth.schema import UserCreate, UserLogin, Token, User
from auth.auth_login import UserService
from auth.auth_utils import create_access_token, decode_token
from datetime import timedelta
import os

router = APIRouter()

user_service = UserService(api_key=os.getenv("PINECONE_API_KEY"), index_name=os.getenv("PINECONE_INDEX_NAME"))


@router.post("/create-superuser", response_model=User)
async def create_superuser(user_data: UserCreate):
    try:
        user = user_service.create_superuser(user_data)
        return user
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@router.post("/login", response_model=Token)
async def login(user_data: UserLogin):
    user = user_service.authenticate_user(user_data.email, user_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password"
        )
    
    access_token = create_access_token(data={"sub": user.id})
    
    return {"access_token": access_token, "token_type": "bearer"}

