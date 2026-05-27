"""
Módulo de autenticación y validación de tokens JWT.
Los tokens son emitidos por el microservicio de usuarios y llegan a través de Azure APIM.

Soporta dos modos (configurado vía variables de entorno):
  - RS256 + JWKS: si USUARIOS_MS_URL está definida (recomendado — no comparte secreto)
  - HS256 + secret: si JWT_SECRET está definida (fallback para desarrollo)

Validación:
- Endpoints HTTP: validan JWT del header Authorization: Bearer <token>
- WebSocket: validan JWT del query param ?token=<jwt>
"""
import logging
import os
import time
from typing import Any, Optional

import httpx
from fastapi import HTTPException, status
from jose import jwt, JWTError

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Configuración JWT desde variables de entorno
# ------------------------------------------------------------------
USUARIOS_MS_URL: str = os.getenv("USUARIOS_MS_URL", "").rstrip("/")
JWT_SECRET: str = os.getenv("JWT_SECRET", "")
JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "RS256" if USUARIOS_MS_URL else "HS256")
JWT_AUDIENCE: Optional[str] = os.getenv("JWT_AUDIENCE") or None
JWKS_CACHE_SECONDS: int = int(os.getenv("JWKS_CACHE_MS", "3600000")) // 1000

# Cache en memoria del JWKS (evita descargar las claves en cada petición)
_jwks_cache: dict = {}
_jwks_cache_time: float = 0.0


# ------------------------------------------------------------------
# JWKS fetching (solo se usa en modo RS256)
# ------------------------------------------------------------------

async def _obtener_jwks() -> dict:
    """
    Descarga el JWKS del microservicio de usuarios y lo cachea en memoria.
    El cache dura JWKS_CACHE_SECONDS (default 1 hora).
    """
    global _jwks_cache, _jwks_cache_time

    now = time.monotonic()
    if _jwks_cache and (now - _jwks_cache_time) < JWKS_CACHE_SECONDS:
        return _jwks_cache

    jwks_url = f"{USUARIOS_MS_URL}/.well-known/jwks.json"
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(jwks_url)
        response.raise_for_status()
        _jwks_cache = response.json()
        _jwks_cache_time = now
        logger.debug("JWKS actualizado desde %s", jwks_url)
        return _jwks_cache


def _extraer_clave_rsa(jwks: dict, token: str) -> dict:
    """
    Busca en el JWKS la clave que corresponde al 'kid' del token.
    Retorna el dict JWK para que python-jose lo use directamente.
    """
    try:
        header = jwt.get_unverified_header(token)
    except JWTError as exc:
        raise ValueError(f"Header JWT inválido: {exc}") from exc

    kid = header.get("kid")

    for key in jwks.get("keys", []):
        if not kid or key.get("kid") == kid:
            return key

    raise ValueError(f"No se encontró clave JWKS para kid='{kid}'")


# ------------------------------------------------------------------
# Decodificación
# ------------------------------------------------------------------

async def _decodificar_token_rs256(token: str) -> dict[str, Any]:
    """Valida el token usando RS256 + JWKS del microservicio de usuarios."""
    if not USUARIOS_MS_URL:
        raise ValueError("USUARIOS_MS_URL no configurada — no se puede usar RS256+JWKS")

    jwks = await _obtener_jwks()
    rsa_key = _extraer_clave_rsa(jwks, token)

    try:
        options = {"verify_exp": True}
        if JWT_AUDIENCE:
            return jwt.decode(
                token, rsa_key, algorithms=["RS256"],
                audience=JWT_AUDIENCE, options=options,
            )
        return jwt.decode(token, rsa_key, algorithms=["RS256"], options=options)
    except JWTError as exc:
        raise ValueError(f"Token RS256 inválido: {exc}") from exc


def _decodificar_token_hs256(token: str) -> dict[str, Any]:
    """Valida el token usando HS256 + secret compartido."""
    if not JWT_SECRET:
        raise ValueError("JWT_SECRET no configurada — no se puede usar HS256")

    try:
        options = {"verify_exp": True}
        if JWT_AUDIENCE:
            return jwt.decode(
                token, JWT_SECRET, algorithms=["HS256"],
                audience=JWT_AUDIENCE, options=options,
            )
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"], options=options)
    except JWTError as exc:
        raise ValueError(f"Token HS256 inválido: {exc}") from exc


async def _decodificar_token(token: str) -> dict[str, Any]:
    """
    Decodifica y verifica el token JWT.
    Usa RS256+JWKS si USUARIOS_MS_URL está definida, HS256 en caso contrario.
    """
    if USUARIOS_MS_URL:
        return await _decodificar_token_rs256(token)
    return _decodificar_token_hs256(token)


# ------------------------------------------------------------------
# API pública
# ------------------------------------------------------------------

async def verificar_token(token: str) -> dict[str, Any]:
    """
    Verifica el JWT para endpoints HTTP. Lanza HTTPException 401 si es inválido.
    """
    try:
        return await _decodificar_token(token)
    except ValueError as exc:
        logger.warning("Token JWT rechazado: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido o expirado. Inicie sesión nuevamente.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


async def verificar_token_ws(token: str) -> dict[str, Any]:
    """
    Verifica el JWT para conexiones WebSocket.
    Lanza ValueError porque en WebSocket no se pueden enviar respuestas HTTP estándar.
    """
    return await _decodificar_token(token)
