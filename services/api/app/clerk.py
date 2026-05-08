"""Clerk JWT verification for Bearer-token auth on the API.

Verifies the JWT against Clerk's JWKS and extracts the user_id (`sub` claim).
The frontend forwards Clerk session tokens via `Authorization: Bearer <jwt>`.

Usage in routes:
    from app.clerk import require_user

    @router.post("/feedback", dependencies=[Depends(rate_limit)])
    async def feedback(payload: FeedbackInput, user_id: str = Depends(require_user)):
        ...
"""
from __future__ import annotations

import logging
from functools import lru_cache

import httpx
import jwt
from fastapi import Depends, Header, HTTPException, status
from jwt.algorithms import RSAAlgorithm

from app.config import settings

logger = logging.getLogger("cratedigger-api.clerk")


@lru_cache(maxsize=1)
def _jwks_client() -> httpx.Client:
    return httpx.Client(timeout=10.0)


def _fetch_jwks() -> dict:
    if not settings.clerk_jwks_url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Clerk not configured",
        )
    resp = _jwks_client().get(settings.clerk_jwks_url)
    resp.raise_for_status()
    return resp.json()


def _verify(token: str) -> dict:
    jwks = _fetch_jwks()
    header = jwt.get_unverified_header(token)
    kid = header.get("kid")
    key_data = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
    if not key_data:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown key")
    public_key = RSAAlgorithm.from_jwk(key_data)
    try:
        return jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            issuer=settings.clerk_issuer or None,
            options={"verify_aud": False},
        )
    except jwt.InvalidTokenError as exc:
        logger.warning("JWT verification failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc


async def require_user(authorization: str = Header(default="")) -> str:
    """Extract Clerk user_id from the Bearer token. Raises 401 if missing/invalid."""
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header",
        )
    token = authorization[7:].strip()
    claims = _verify(token)
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No subject in token")
    return user_id
