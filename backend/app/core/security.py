"""
Security utilities for authentication and credential encryption.

Provides:
- API key hashing (SHA-256)
- JWT validation (Clerk)
- AES-256-GCM encryption/decryption for credentials
"""

import hashlib
import secrets
from functools import lru_cache
from typing import cast

import jwt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from jwt import PyJWKClient
from passlib.context import CryptContext

from app.config import settings

# Password hashing context (for API keys stored hashed)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_api_key(api_key: str) -> str:
    """Hash an API key using SHA-256 for storage."""
    return hashlib.sha256(api_key.encode()).hexdigest()


def verify_api_key(plain_key: str, hashed_key: str) -> bool:
    """Verify an API key against its hash."""
    return secrets.compare_digest(hash_api_key(plain_key), hashed_key)


def generate_api_key(prefix: str = "wrp_live") -> str:
    """Generate a secure API key with the given prefix."""
    random_part = secrets.token_hex(16)  # 32 characters
    return f"{prefix}_{random_part}"


@lru_cache(maxsize=1)
def _get_jwks_client() -> PyJWKClient:
    """
    Build a cached JWKS client for the configured Clerk issuer.

    Clerk publishes its rotating RS256 public keys at
    <issuer>/.well-known/jwks.json. PyJWKClient fetches and caches them,
    refetching when it sees a token signed by an unknown key id (kid).
    """
    if not settings.JWT_ISSUER:
        raise RuntimeError("JWT_ISSUER must be set to verify Clerk tokens")
    jwks_url = f"{settings.JWT_ISSUER.rstrip('/')}/.well-known/jwks.json"
    return PyJWKClient(jwks_url)


def verify_jwt_token(token: str) -> dict | None:
    """
    Verify a Clerk JWT token.

    Selects the RS256 public key from Clerk's JWKS by the token's `kid`,
    verifies signature, expiry, issuer, and (optionally) audience. Returns
    the decoded payload if valid, None if invalid.
    """
    try:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.JWT_AUDIENCE or None,
            issuer=settings.JWT_ISSUER or None,
            options={
                "verify_aud": bool(settings.JWT_AUDIENCE),
                "verify_iss": bool(settings.JWT_ISSUER),
            },
        )
        return cast(dict, payload)
    except (jwt.PyJWTError, jwt.PyJWKClientError):
        return None


def extract_workspace_id(token_payload: dict) -> str | None:
    """
    Extract workspace_id from Clerk JWT payload.

    Clerk Organization membership is stored in the 'org_id' claim.
    We map this to our workspace concept.
    """
    # Try multiple claim names for flexibility
    return (
        token_payload.get("org_id") or token_payload.get("workspace_id") or token_payload.get("sub")
    )


class CredentialEncryption:
    """
    AES-256-GCM encryption for OAuth tokens.

    Key derivation: SHA-256(master_key + workspace_id)
    Each encrypt operation generates a unique nonce.
    """

    def __init__(self, master_key: bytes):
        self.master_key = master_key

    def encrypt(self, plaintext: str, workspace_id: str) -> bytes:
        """Encrypt a plaintext string for a workspace."""
        key = self._derive_key(workspace_id)
        aesgcm = AESGCM(key)
        nonce = secrets.token_bytes(12)
        ciphertext = aesgcm.encrypt(nonce, plaintext.encode(), workspace_id.encode())
        return nonce + ciphertext  # Prepend nonce for storage

    def decrypt(self, ciphertext: bytes, workspace_id: str) -> str:
        """Decrypt ciphertext for a workspace."""
        key = self._derive_key(workspace_id)
        aesgcm = AESGCM(key)
        nonce, data = ciphertext[:12], ciphertext[12:]
        return aesgcm.decrypt(nonce, data, workspace_id.encode()).decode()

    def _derive_key(self, workspace_id: str) -> bytes:
        """Derive a 32-byte key from master key + workspace_id salt."""
        return hashlib.sha256(self.master_key + workspace_id.encode()).digest()


def get_credential_encryption() -> CredentialEncryption:
    """Get the credential encryption instance."""
    master_key_bytes = bytes.fromhex(settings.CREDENTIAL_MASTER_KEY)
    return CredentialEncryption(master_key_bytes)
