"""
Unit tests for Credential Vault - Task 34.

Tests Google OAuth expiry with 48h warning / 6h critical thresholds.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.services.credential_vault.vault import (
    CRITICAL_HOURS,
    EXPIRY_WARNING_HOURS,
    CredentialVault,
)


class TestCredentialVault:
    """Tests for OAuth credential expiry checking."""

    @pytest.fixture
    def vault(self):
        from app.core.security import CredentialEncryption

        encryption = CredentialEncryption(bytes.fromhex("0" * 64))
        return CredentialVault(encryption)

    # Expired credential cases
    @pytest.mark.asyncio
    async def test_expired_credential_critical_alert(self, vault, mock_credential_expired):
        """Expired credential should generate critical alert."""
        alerts = await vault.check_credential(mock_credential_expired)

        assert len(alerts) >= 1

        expired_alert = next((a for a in alerts if "expired" in a["title"].lower()), None)
        assert expired_alert is not None
        assert expired_alert["severity"] == "critical"
        assert expired_alert["category"] == "auth"

    @pytest.mark.asyncio
    async def test_credential_near_expiry(self, vault):
        """Credential expiring within 6 hours should generate critical alert."""

        class MockCredentialNearExpiry:
            id = "cred_near"
            provider = "google"
            display_name = "Near Expiry Credential"
            expires_at = datetime.now(UTC) + timedelta(hours=3)
            gcp_status = "production"
            status = "active"

        alerts = await vault.check_credential(MockCredentialNearExpiry())

        critical_alert = next(
            (a for a in alerts if a["severity"] == "critical" and "expiring" in a["title"].lower()),
            None,
        )
        assert critical_alert is not None

    @pytest.mark.asyncio
    async def test_credential_warning_window(self, vault):
        """Credential expiring within 48 hours should generate warning."""

        class MockCredentialWarning:
            id = "cred_warn"
            provider = "google"
            display_name = "Warning Credential"
            expires_at = datetime.now(UTC) + timedelta(hours=24)
            gcp_status = "production"
            status = "active"

        alerts = await vault.check_credential(MockCredentialWarning())

        warning_alert = next(
            (a for a in alerts if a["severity"] == "warning" and "expiring" in a["title"].lower()),
            None,
        )
        assert warning_alert is not None

    @pytest.mark.asyncio
    async def test_credential_no_expiry_no_alert(self, vault):
        """Credential without expiry date should not generate alert."""

        class MockCredentialNoExpiry:
            id = "cred_no_expiry"
            provider = "google"
            display_name = "No Expiry Credential"
            expires_at = None
            gcp_status = "production"
            status = "active"

        alerts = await vault.check_credential(MockCredentialNoExpiry())

        assert len(alerts) == 0

    # GCP Testing mode
    @pytest.mark.asyncio
    async def test_gcp_testing_mode_warning(self, vault):
        """GCP app in Testing mode should generate warning."""

        class MockGCPTesting:
            id = "cred_gcp"
            provider = "google"
            display_name = "GCP Testing Credential"
            expires_at = datetime.now(UTC) + timedelta(days=30)  # Far future
            gcp_status = "testing"
            status = "active"

        alerts = await vault.check_credential(MockGCPTesting())

        testing_alert = next((a for a in alerts if "testing" in a["title"].lower()), None)
        assert testing_alert is not None
        assert testing_alert["severity"] == "warning"

    def test_provider_specific_fix_google(self, vault):
        """Google provider should suggest Service Account migration."""
        fix = vault._get_provider_fix("google")

        assert "service account" in fix.lower() or "publish" in fix.lower()

    def test_provider_specific_fix_microsoft(self, vault):
        """Microsoft provider should suggest OAuth refresh."""
        fix = vault._get_provider_fix("microsoft")

        assert "re-authenticate" in fix.lower() or "oauth" in fix.lower()

    def test_provider_specific_fix_github(self, vault):
        """GitHub provider should suggest token regeneration."""
        fix = vault._get_provider_fix("github")

        assert "token" in fix.lower() or "regenerate" in fix.lower()

    # Encryption tests
    @pytest.mark.asyncio
    async def test_encrypt_decrypt_roundtrip(self, vault):
        """Encryption should roundtrip correctly."""
        plaintext = "test-oauth-token-12345"
        workspace_id = "ws_test_123"

        token_enc, refresh_enc = await vault.encrypt_credential(plaintext, None, workspace_id)
        token, refresh = await vault.decrypt_credential(token_enc, refresh_enc, workspace_id)

        assert token == plaintext
        assert refresh is None

    @pytest.mark.asyncio
    async def test_encryption_unique_nonces(self, vault):
        """Each encryption should produce different ciphertext."""
        plaintext = "same-token"
        workspace_id = "ws_test"

        enc1 = await vault.encrypt_credential(plaintext, None, workspace_id)
        enc2 = await vault.encrypt_credential(plaintext, None, workspace_id)

        assert enc1[0] != enc2[0]  # Different nonces

    @pytest.mark.asyncio
    async def test_decrypt_with_wrong_workspace_fails(self, vault):
        """Decrypting with wrong workspace should fail."""
        plaintext = "secret-token"
        workspace_id = "ws_correct"

        token_enc, _ = await vault.encrypt_credential(plaintext, None, workspace_id)

        with pytest.raises(ValueError):
            await vault.decrypt_credential(token_enc, None, "ws_wrong")


class TestExpiryThresholds:
    """Tests for expiry threshold values."""

    def test_warning_hours_value(self):
        """Warning threshold should be 48 hours per Blueprint."""
        assert EXPIRY_WARNING_HOURS == 48

    def test_critical_hours_value(self):
        """Critical threshold should be 6 hours per Blueprint."""
        assert CRITICAL_HOURS == 6
