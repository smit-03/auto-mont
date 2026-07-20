"""
Security utilities for authentication and credential encryption.

Provides:
- API key hashing (SHA-256)
- JWT validation (Clerk)
- AES-256-GCM encryption/decryption for credentials
"""

import hashlib
import secrets
from typing import cast

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from jose import JWTError, jwt
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


def verify_jwt_token(token: str) -> dict | None:
    """
    Verify a Clerk JWT token.

    Verifies signature, expiry, issuer, and audience. Returns the decoded
    payload if valid, None if invalid.
    """
    try:
        # Clerk uses RS256 with their public key
        # For symmetric keys (testing), use HS256
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
            options={
                "verify_aud": settings.JWT_AUDIENCE is not None,
                "verify_iss": settings.JWT_ISSUER is not None,
            },
        )
        return cast(dict, payload)
    except JWTError:
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
