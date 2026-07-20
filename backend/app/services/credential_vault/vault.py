"""
Credential Vault - OAuth token lifecycle tracking.

Monitors token expiry for all OAuth providers, with special handling for
GCP Testing mode (7-day token expiry) and Google OAuth in general.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.core.logging import get_logger
from app.core.security import CredentialEncryption

if TYPE_CHECKING:
    from app.models.credential import Credential

logger = get_logger(__name__)

EXPIRY_WARNING_HOURS = 48  # Alert 48 hours before expiry
CRITICAL_HOURS = 6  # Critical alert 6 hours before expiry


def get_vault() -> "CredentialVault":
    """Get the global credential vault instance."""
    return CredentialVault(CredentialEncryption(bytes.fromhex("0" * 64)))


class CredentialVault:
    """
    Actively monitors OAuth token lifecycle.

    Research finding: Google Cloud apps in 'Testing' mode expire tokens every 7 days.
    Gmail password changes immediately invalidate OAuth sessions.
    Firebase tokens expire with invalid_grant.

    This vault catches these before they cause production failures.
    """

    def __init__(self, encryption: CredentialEncryption):
        self.encryption = encryption

    async def check_credential(self, credential: "Credential") -> list[dict]:
        """Check credential for expiry issues. Returns list of alert dicts."""
        alerts: list[dict] = []

        if not credential.expires_at:
            return alerts

        now = datetime.now(UTC)
        hours_until_expiry = (credential.expires_at - now).total_seconds() / 3600

        if hours_until_expiry <= 0:
            alerts.append(
                {
                    "severity": "critical",
                    "title": f"Credential expired: {credential.display_name}",
                    "category": "auth",
                    "description": (
                        f"OAuth token for {credential.provider} ({credential.display_name}) "
                        f"expired at {credential.expires_at}. All workflows using this "
                        f"credential are now failing."
                    ),
                    "suggested_fix": self._get_provider_fix(credential.provider),
                    "credential_id": str(credential.id),
                }
            )
        elif hours_until_expiry <= CRITICAL_HOURS:
            alerts.append(
                {
                    "severity": "critical",
                    "title": f"Credential expiring in {hours_until_expiry:.1f}h: {credential.display_name}",
                    "category": "auth",
                    "description": (
                        f"OAuth token expires at {credential.expires_at} "
                        f"({hours_until_expiry:.1f}h from now). Workflows will fail soon."
                    ),
                    "suggested_fix": self._get_provider_fix(credential.provider),
                    "credential_id": str(credential.id),
                }
            )
        elif hours_until_expiry <= EXPIRY_WARNING_HOURS:
            alerts.append(
                {
                    "severity": "warning",
                    "title": f"Credential expiring soon: {credential.display_name}",
                    "category": "auth",
                    "description": (
                        f"OAuth token expires in {hours_until_expiry:.1f} hours "
                        f"(at {credential.expires_at}). Consider re-authenticating soon."
                    ),
                    "suggested_fix": self._get_provider_fix(credential.provider),
                    "credential_id": str(credential.id),
                }
            )

        # GCP-specific check (Testing mode causes weekly expiry)
        if credential.provider == "google" and getattr(credential, "gcp_status", None) == "testing":
            alerts.append(
                {
                    "severity": "warning",
                    "title": f"GCP app in Testing mode: {credential.display_name}",
                    "category": "auth",
                    "description": (
                        "Your Google Cloud OAuth app is in 'Testing' mode. "
                        "This causes refresh tokens to expire every 7 days, "
                        "regardless of the token's stated expiry. Publish your "
                        "app or migrate to a Service Account to fix this."
                    ),
                    "credential_id": str(credential.id),
                }
            )

        return alerts

    def _get_provider_fix(self, provider: str) -> str:
        """Get provider-specific remediation text."""
        fixes = {
            "google": (
                "Re-authenticate the Google credential. If this expires weekly, "
                "publish your GCP OAuth app or migrate to a Service Account."
            ),
            "microsoft": "Re-authenticate the Microsoft credential via OAuth flow.",
            "github": "Regenerate the GitHub personal access token or reinstall the OAuth app.",
            "slack": "Reinstall the Slack app or regenerate the user token.",
        }
        return fixes.get(provider, "Re-authenticate the credential in your integration settings.")

    async def encrypt_credential(
        self, token: str, refresh_token: str | None, workspace_id: str
    ) -> tuple[bytes, bytes | None]:
        """Encrypt credential tokens for storage."""
        token_enc = self.encryption.encrypt(token, workspace_id)
        refresh_enc = (
            self.encryption.encrypt(refresh_token, workspace_id) if refresh_token else None
        )
        return token_enc, refresh_enc

    async def decrypt_credential(
        self, token_enc: bytes, refresh_enc: bytes | None, workspace_id: str
    ) -> tuple[str, str | None]:
        """Decrypt credential tokens for use."""
        try:
            token = self.encryption.decrypt(token_enc, workspace_id)
            refresh = self.encryption.decrypt(refresh_enc, workspace_id) if refresh_enc else None
            return token, refresh
        except Exception as e:
            # Wrap cryptography exceptions (InvalidTag, etc.) in ValueError
            raise ValueError("Invalid credential or wrong workspace") from e
