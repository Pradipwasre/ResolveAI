"""JWT login for support agents."""

from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models import Agent

router = APIRouter(prefix="/api/auth", tags=["auth"])
bearer = HTTPBearer(auto_error=False)


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    token: str
    name: str
    email: str


def create_token(agent: Agent) -> str:
    settings = get_settings()
    payload = {
        "sub": agent.email,
        "name": agent.name,
        "exp": datetime.now(UTC) + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token") from exc


async def current_agent(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not logged in")
    return decode_token(credentials.credentials)


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, session: AsyncSession = Depends(get_session)) -> LoginResponse:
    agent = (await session.execute(select(Agent).where(Agent.email == body.email.lower().strip()))).scalar_one_or_none()
    if agent is None or not bcrypt.checkpw(body.password.encode(), agent.password_hash.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong email or password")
    return LoginResponse(token=create_token(agent), name=agent.name, email=agent.email)


@router.get("/me")
async def me(agent: dict = Depends(current_agent)) -> dict:
    return {"email": agent["sub"], "name": agent["name"]}
