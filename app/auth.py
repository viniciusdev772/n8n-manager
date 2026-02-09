"""Autenticação por Bearer token."""

from fastapi import HTTPException, Request

from .config import API_AUTH_TOKEN


def verify_token(request: Request):
    if not API_AUTH_TOKEN:
        raise HTTPException(500, "Token da API nao configurado no servidor")
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Token ausente")
    token = auth.removeprefix("Bearer ").strip()
    if token != API_AUTH_TOKEN:
        raise HTTPException(403, "Token inválido")
    return token
