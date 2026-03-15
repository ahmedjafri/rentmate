from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backends.wire import auth_backend

router = APIRouter()


class LoginBody(BaseModel):
    password: str


@router.post("/auth/login")
async def login(body: LoginBody):
    try:
        token = await auth_backend.login(password=body.password)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid password")
    return {"access_token": token}
