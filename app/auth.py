"""Autenticação por Bearer token."""

from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import API_AUTH_TOKEN

_bearer = HTTPBearer()


def verify_token(credentials: HTTPAuthorizationCredentials = Security(_bearer)):
    if not API_AUTH_TOKEN:
        raise HTTPException(500, "Token da API nao configurado no servidor")
    if credentials.credentials != API_AUTH_TOKEN:
        raise HTTPException(403, "Token inválido")
    return credentials.credentials
