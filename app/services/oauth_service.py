from dataclasses import dataclass
from time import monotonic
from typing import Any

import httpx
from fastapi import HTTPException, status
from jose import JWTError, jwt

from app.core.config import settings

GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"


@dataclass(frozen=True)
class VerifiedIdentity:
    provider: str
    provider_sub: str
    email: str | None
    email_verified: bool
    name: str | None
    claims: dict[str, Any]


class JwksCache:
    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

    async def get_keys(self, url: str) -> list[dict[str, Any]]:
        cached = self._cache.get(url)
        now = monotonic()
        if cached and cached[0] > now:
            return cached[1]

        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(url)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="OAuth key server is temporarily unavailable",
            ) from exc

        max_age = 3600
        cache_control = response.headers.get("cache-control", "")
        for part in cache_control.split(","):
            part = part.strip()
            if part.startswith("max-age="):
                try:
                    max_age = int(part.split("=", 1)[1])
                except ValueError:
                    max_age = 3600
                break

        keys = data.get("keys", [])
        self._cache[url] = (now + max_age, keys)
        return keys


class OAuthTokenVerifier:
    def __init__(self, jwks_cache: JwksCache | None = None) -> None:
        self.jwks_cache = jwks_cache or JwksCache()

    async def verify_google(self, id_token: str, nonce: str | None = None) -> VerifiedIdentity:
        if settings.auth_allow_unverified_tokens:
            return self._decode_unverified("google", id_token)
        return await self._verify_oidc_token(
            provider="google",
            token=id_token,
            jwks_url=GOOGLE_JWKS_URL,
            audiences=settings.google_client_ids,
            issuers=["https://accounts.google.com", "accounts.google.com"],
            nonce=nonce,
        )

    async def verify_apple(self, id_token: str, nonce: str | None = None) -> VerifiedIdentity:
        if settings.auth_allow_unverified_tokens:
            return self._decode_unverified("apple", id_token)
        return await self._verify_oidc_token(
            provider="apple",
            token=id_token,
            jwks_url=APPLE_JWKS_URL,
            audiences=settings.apple_client_ids,
            issuers=["https://appleid.apple.com"],
            nonce=nonce,
        )

    async def _verify_oidc_token(
        self,
        provider: str,
        token: str,
        jwks_url: str,
        audiences: list[str],
        issuers: list[str],
        nonce: str | None,
    ) -> VerifiedIdentity:
        if not audiences:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"{provider.upper()}_CLIENT_IDS is not configured",
            )

        header = self._get_token_header(token)
        keys = await self.jwks_cache.get_keys(jwks_url)
        key = next((candidate for candidate in keys if candidate.get("kid") == header.get("kid")), None)
        if not key:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown token key")

        last_error: Exception | None = None
        for audience in audiences:
            for issuer in issuers:
                try:
                    claims = jwt.decode(
                        token,
                        key,
                        algorithms=[header.get("alg", "RS256")],
                        audience=audience,
                        issuer=issuer,
                    )
                    self._verify_nonce(claims, nonce)
                    return self._claims_to_identity(provider, claims)
                except JWTError as exc:
                    last_error = exc

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid {provider} id_token: {last_error}",
        )

    def _decode_unverified(self, provider: str, token: str) -> VerifiedIdentity:
        try:
            claims = jwt.get_unverified_claims(token)
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Malformed id_token") from exc
        return self._claims_to_identity(provider, claims)

    def _get_token_header(self, token: str) -> dict[str, Any]:
        try:
            return jwt.get_unverified_header(token)
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Malformed id_token") from exc

    def _verify_nonce(self, claims: dict[str, Any], nonce: str | None) -> None:
        if nonce is not None and claims.get("nonce") != nonce:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid nonce")

    def _claims_to_identity(self, provider: str, claims: dict[str, Any]) -> VerifiedIdentity:
        provider_sub = claims.get("sub")
        if not provider_sub:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing subject")

        email_verified_raw = claims.get("email_verified", False)
        email_verified = email_verified_raw is True or email_verified_raw == "true"
        return VerifiedIdentity(
            provider=provider,
            provider_sub=str(provider_sub),
            email=claims.get("email"),
            email_verified=email_verified,
            name=claims.get("name"),
            claims=claims,
        )
