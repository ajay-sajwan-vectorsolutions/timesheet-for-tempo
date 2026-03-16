"""
Unit tests for ConfigManager and CredentialManager classes in tempo_automation.py.

Coverage:
  - CredentialManager.encrypt (empty, non-Windows, Windows roundtrip)
  - CredentialManager.decrypt (no prefix, non-Windows, Windows roundtrip)
  - CredentialManager.PREFIX constant
  - ConfigManager.__init__ (load from file, missing triggers wizard, invalid JSON)
  - ConfigManager.load_config (valid file, missing file, corrupt file)
  - ConfigManager.save_config (create, overwrite, structure preservation)
  - ConfigManager.get_account_id (API success, missing accountId, HTTP error, network error)
  - ConfigManager.setup_wizard (developer flow, PO flow, saves config)
  - ConfigManager._select_role (choices 1-3, invalid input retry)
  - ConfigManager._select_location (choices 1-5, invalid input retry)

All HTTP calls are intercepted by the `responses` library so no real network
traffic is generated.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import responses as responses_lib
import requests

from tempo_automation import ConfigManager, CredentialManager, CONFIG_FILE

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TEMPO_USER_URL = "https://api.tempo.io/4/user"
ACCOUNT_ID = "712020:test-uuid-1234"
USER_EMAIL = "dev@example.com"


# ===========================================================================
# CredentialManager
# ===========================================================================

class TestCredentialManager:
    """Tests for the CredentialManager encryption/decryption utility."""

    def test_prefix_constant(self):
        """PREFIX should be 'ENC:' for identifying encrypted values."""
        assert CredentialManager.PREFIX == "ENC:"

    def test_encrypt_empty_string_returns_empty(self):
        """Empty string should pass through encrypt unchanged."""
        assert CredentialManager.encrypt("") == ""

    def test_encrypt_none_returns_none(self):
        """None should pass through encrypt unchanged (falsy guard)."""
        result = CredentialManager.encrypt(None)
        assert result is None

    @patch("sys.platform", "linux")
    @patch.object(CredentialManager, "_use_dpapi", False)
    @patch.object(CredentialManager, "_use_keyring", False)
    def test_encrypt_non_windows_returns_plaintext(self):
        """On non-Windows platforms, encrypt returns the plain text as-is."""
        result = CredentialManager.encrypt("my-secret-token")
        assert result == "my-secret-token"

    @patch("sys.platform", "darwin")
    @patch.object(CredentialManager, "_use_dpapi", False)
    @patch.object(CredentialManager, "_use_keyring", False)
    def test_encrypt_macos_returns_plaintext(self):
        """On macOS, encrypt returns the plain text as-is (no DPAPI)."""
        result = CredentialManager.encrypt("another-secret")
        assert result == "another-secret"

    def test_decrypt_no_prefix_returns_unchanged(self):
        """A value without 'ENC:' prefix should be returned unchanged."""
        assert CredentialManager.decrypt("plain-text-token") == "plain-text-token"

    def test_decrypt_empty_string_returns_empty(self):
        """Empty string should pass through decrypt unchanged."""
        assert CredentialManager.decrypt("") == ""

    def test_decrypt_none_returns_none(self):
        """None should pass through decrypt unchanged (falsy guard)."""
        result = CredentialManager.decrypt(None)
        assert result is None

    @patch("sys.platform", "linux")
    @patch.object(CredentialManager, "_use_dpapi", False)
    @patch.object(CredentialManager, "_use_keyring", False)
    def test_decrypt_non_windows_returns_unchanged(self):
        """ENC: prefix on non-Windows returns the value unchanged (cannot decrypt)."""
        encrypted = "ENC:c29tZWJhc2U2NA=="
        result = CredentialManager.decrypt(encrypted)
        assert result == encrypted

    @patch("sys.platform", "darwin")
    @patch.object(CredentialManager, "_use_dpapi", False)
    @patch.object(CredentialManager, "_use_keyring", False)
    def test_decrypt_macos_enc_prefix_returns_unchanged(self):
        """ENC: prefix on macOS returns the value unchanged."""
        encrypted = "ENC:dGVzdGRhdGE="
        result = CredentialManager.decrypt(encrypted)
        assert result == encrypted

    @pytest.mark.windows
    @pytest.mark.skipif(
        sys.platform != "win32", reason="DPAPI only available on Windows"
    )
    def test_encrypt_decrypt_roundtrip_on_windows(self):
        """On Windows, encrypt then decrypt should return the original value."""
        original = "my-super-secret-api-token-12345"
        encrypted = CredentialManager.encrypt(original)

        # Should have ENC: prefix on Windows
        assert encrypted.startswith(CredentialManager.PREFIX)
        assert encrypted != original

        # Roundtrip must produce the original
        decrypted = CredentialManager.decrypt(encrypted)
        assert decrypted == original

    @pytest.mark.windows
    @pytest.mark.skipif(
        sys.platform != "win32", reason="DPAPI only available on Windows"
    )
    def test_encrypt_produces_enc_prefix_on_windows(self):
        """On Windows, encrypted output must start with 'ENC:'."""
        encrypted = CredentialManager.encrypt("test-value")
        assert encrypted.startswith("ENC:")


# ===========================================================================
# ConfigManager.__init__ / load_config
# ===========================================================================

class TestConfigManagerInit:
    """Tests for ConfigManager construction and config loading."""

    def test_loads_config_from_existing_file(self, config_file, developer_config):
        """When a valid config file exists, __init__ loads and parses it."""
        cm = ConfigManager(config_path=config_file)
        assert cm.config["user"]["email"] == developer_config["user"]["email"]
        assert cm.config["user"]["role"] == "developer"
        assert cm.config["schedule"]["daily_hours"] == 8.0

    def test_config_path_stored(self, config_file):
        """config_path attribute should reflect the path passed to __init__."""
        cm = ConfigManager(config_path=config_file)
        assert cm.config_path == config_file

    def test_missing_config_triggers_wizard(self, tmp_path):
        """When no config file exists, setup_wizard should be called."""
        missing_path = tmp_path / "nonexistent_config.json"
        mock_config = {"user": {"email": "wizard@test.com"}}

        with patch.object(ConfigManager, "setup_wizard", return_value=mock_config) as mock_wiz:
            cm = ConfigManager(config_path=missing_path)
            mock_wiz.assert_called_once()
            assert cm.config["user"]["email"] == "wizard@test.com"

    def test_invalid_json_raises_error(self, tmp_path):
        """A file with invalid JSON should raise an exception."""
        bad_file = tmp_path / "config.json"
        bad_file.write_text("{invalid json content!!!", encoding="utf-8")

        with pytest.raises(SystemExit):
            ConfigManager(config_path=bad_file)

    def test_custom_config_path(self, tmp_path, developer_config):
        """ConfigManager should accept an arbitrary Path for the config file."""
        custom = tmp_path / "subdir" / "custom_config.json"
        custom.parent.mkdir(parents=True, exist_ok=True)
        custom.write_text(json.dumps(developer_config), encoding="utf-8")

        cm = ConfigManager(config_path=custom)
        assert cm.config_path == custom
        assert cm.config["tempo"]["api_token"] == "tempo-test-token"

    def test_empty_config_file_raises_error(self, tmp_path):
        """An empty file is not valid JSON and should raise an exception."""
        empty_file = tmp_path / "config.json"
        empty_file.write_text("", encoding="utf-8")

        with pytest.raises(SystemExit):
            ConfigManager(config_path=empty_file)


# ===========================================================================
# ConfigManager.save_config
# ===========================================================================

class TestSaveConfig:
    """Tests for persisting configuration to disk."""

    def test_saves_valid_config(self, tmp_path, developer_config):
        """save_config should write valid JSON matching the input dict."""
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(json.dumps(developer_config), encoding="utf-8")
        cm = ConfigManager(config_path=cfg_path)

        new_config = dict(developer_config)
        new_config["user"]["name"] = "Updated Name"
        cm.save_config(new_config)

        with open(cfg_path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        assert saved["user"]["name"] == "Updated Name"

    def test_creates_file_if_not_exists(self, tmp_path, developer_config):
        """save_config should create a new file when none exists yet."""
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(json.dumps(developer_config), encoding="utf-8")
        cm = ConfigManager(config_path=cfg_path)

        new_path = tmp_path / "new_config.json"
        cm.config_path = new_path
        cm.save_config(developer_config)

        assert new_path.exists()
        with open(new_path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        assert saved["user"]["email"] == developer_config["user"]["email"]

    def test_overwrites_existing_config(self, tmp_path, developer_config):
        """save_config should completely overwrite the previous content."""
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(json.dumps(developer_config), encoding="utf-8")
        cm = ConfigManager(config_path=cfg_path)

        minimal_config = {"user": {"email": "new@test.com"}}
        cm.save_config(minimal_config)

        with open(cfg_path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        assert saved == minimal_config
        # Original keys should be gone
        assert "jira" not in saved

    def test_preserves_config_structure(self, tmp_path, developer_config):
        """Save then load roundtrip should produce an identical dict."""
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(json.dumps(developer_config), encoding="utf-8")
        cm = ConfigManager(config_path=cfg_path)

        cm.save_config(developer_config)
        cm2 = ConfigManager(config_path=cfg_path)

        assert cm2.config == developer_config


# ===========================================================================
# ConfigManager.get_account_id
# ===========================================================================

class TestGetAccountId:
    """Tests for fetching the Tempo user account ID."""

    @responses_lib.activate
    def test_returns_account_id_from_api(self, config_file, developer_config):
        """Successful API call returns the accountId field."""
        responses_lib.add(
            responses_lib.GET,
            TEMPO_USER_URL,
            json={"accountId": ACCOUNT_ID, "displayName": "Test User"},
            status=200,
        )
        cm = ConfigManager(config_path=config_file)
        result = cm.get_account_id()
        assert result == ACCOUNT_ID

    @responses_lib.activate
    def test_uses_bearer_token_in_header(self, config_file, developer_config):
        """The request should include the Bearer token from config."""
        responses_lib.add(
            responses_lib.GET,
            TEMPO_USER_URL,
            json={"accountId": ACCOUNT_ID},
            status=200,
        )
        cm = ConfigManager(config_path=config_file)
        cm.get_account_id()

        # Inspect the request that was made
        assert len(responses_lib.calls) == 1
        auth_header = responses_lib.calls[0].request.headers.get("Authorization")
        assert auth_header == "Bearer tempo-test-token"

    @responses_lib.activate
    def test_falls_back_to_email_when_no_account_id(self, config_file, developer_config):
        """When response lacks accountId, fall back to user email."""
        responses_lib.add(
            responses_lib.GET,
            TEMPO_USER_URL,
            json={"displayName": "Test User"},  # no accountId
            status=200,
        )
        cm = ConfigManager(config_path=config_file)
        result = cm.get_account_id()
        assert result == developer_config["user"]["email"]

    @responses_lib.activate
    def test_falls_back_to_email_on_http_error(self, config_file, developer_config):
        """On HTTP 401/403/500 the method should fall back to email."""
        responses_lib.add(
            responses_lib.GET,
            TEMPO_USER_URL,
            json={"error": "Unauthorized"},
            status=401,
        )
        cm = ConfigManager(config_path=config_file)
        result = cm.get_account_id()
        assert result == developer_config["user"]["email"]

    @responses_lib.activate
    def test_falls_back_to_email_on_network_error(self, config_file, developer_config):
        """On network failure (ConnectionError) fall back to email."""
        responses_lib.add(
            responses_lib.GET,
            TEMPO_USER_URL,
            body=requests.ConnectionError("Network unreachable"),
        )
        cm = ConfigManager(config_path=config_file)
        result = cm.get_account_id()
        assert result == developer_config["user"]["email"]

    @responses_lib.activate
    def test_falls_back_to_email_on_timeout(self, config_file, developer_config):
        """On request timeout, fall back to email."""
        responses_lib.add(
            responses_lib.GET,
            TEMPO_USER_URL,
            body=requests.Timeout("Request timed out"),
        )
        cm = ConfigManager(config_path=config_file)
        result = cm.get_account_id()
        assert result == developer_config["user"]["email"]


# ===========================================================================
# ConfigManager.setup_wizard
# ===========================================================================

class TestSetupWizard:
    """Tests for the interactive setup wizard."""

    @pytest.fixture(autouse=True)
    def no_backup_config(self, monkeypatch):
        """Prevent wizard from loading the real AppData backup config."""
        monkeypatch.setattr("tempo_automation.CONFIG_BACKUP_FILE", None)

    def _developer_inputs(self):
        """Input sequence for a developer setup flow."""
        return [
            "dev@example.com",       # email
            "1",                     # role: developer
            "tempo-test-token",      # tempo token
            "jira-test-token",       # jira token (developer only)
            # name auto-populated from Jira /myself mock
            "8",                     # daily hours
            "1",                     # location: US
            "no",                    # enable email: no
        ]

    def _register_jira_myself(self):
        """Register a successful Jira /myself response for developer tests."""
        responses_lib.add(
            responses_lib.GET,
            "https://lmsportal.atlassian.net/rest/api/3/myself",
            json={"displayName": "Test Developer", "accountId": "test-id"},
            status=200,
        )

    def _register_tempo_user(self):
        """Register a successful Tempo response for token verification."""
        responses_lib.add(
            responses_lib.GET,
            "https://api.tempo.io/4/work-attributes",
            json={"results": []},
            status=200,
        )

    def _po_inputs(self):
        """Input sequence for a product owner setup flow."""
        return [
            "po@example.com",        # email
            "2",                     # role: product_owner
            "tempo-po-token",        # tempo token
            "Test PO",               # name
            "8",                     # daily hours
            "1",                     # location: US
            "no",                    # enable email: no
            "yes",                   # add activity?
            "Stakeholder Meetings",  # activity name
            "3",                     # activity hours
            "no",                    # add another? no
        ]

    @responses_lib.activate
    @patch("builtins.input")
    def test_developer_wizard_flow(self, mock_input, tmp_path):
        """Developer wizard should produce a full config with Jira token."""
        self._register_tempo_user()
        self._register_jira_myself()
        mock_input.side_effect = self._developer_inputs()
        cfg_path = tmp_path / "config.json"

        cm = ConfigManager.__new__(ConfigManager)
        cm.config_path = cfg_path
        config = cm.setup_wizard()

        assert config["user"]["email"] == "dev@example.com"
        assert config["user"]["name"] == "Test Developer"
        assert config["user"]["role"] == "developer"
        assert config["jira"]["api_token"] == "jira-test-token"
        assert config["tempo"]["api_token"] == "tempo-test-token"
        assert config["schedule"]["daily_hours"] == 8.0
        assert config["schedule"]["country_code"] == "US"
        assert config["schedule"]["state"] == ""
        assert config["notifications"]["email_enabled"] is False

    @responses_lib.activate
    @patch("builtins.input")
    def test_po_wizard_flow(self, mock_input, tmp_path):
        """PO wizard should produce a config with manual activities and no Jira token."""
        self._register_tempo_user()
        mock_input.side_effect = self._po_inputs()
        cfg_path = tmp_path / "config.json"

        cm = ConfigManager.__new__(ConfigManager)
        cm.config_path = cfg_path
        config = cm.setup_wizard()

        assert config["user"]["role"] == "product_owner"
        assert config["jira"]["api_token"] == ""
        assert config["tempo"]["api_token"] == "tempo-po-token"
        assert len(config["manual_activities"]) == 1
        assert config["manual_activities"][0]["activity"] == "Stakeholder Meetings"
        assert config["manual_activities"][0]["hours"] == 3.0

    @responses_lib.activate
    @patch("builtins.input")
    def test_saves_config_after_wizard(self, mock_input, tmp_path):
        """The wizard should persist the config file to disk."""
        self._register_tempo_user()
        self._register_jira_myself()
        mock_input.side_effect = self._developer_inputs()
        cfg_path = tmp_path / "config.json"

        cm = ConfigManager.__new__(ConfigManager)
        cm.config_path = cfg_path
        cm.setup_wizard()

        assert cfg_path.exists()
        with open(cfg_path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        assert saved["user"]["email"] == "dev@example.com"

    @responses_lib.activate
    @patch("builtins.input")
    def test_developer_gets_jira_token_prompt(self, mock_input, tmp_path):
        """Developer role should prompt for a Jira API token."""
        self._register_tempo_user()
        self._register_jira_myself()
        inputs = self._developer_inputs()
        mock_input.side_effect = inputs
        cfg_path = tmp_path / "config.json"

        cm = ConfigManager.__new__(ConfigManager)
        cm.config_path = cfg_path
        config = cm.setup_wizard()

        # Developer gets a non-empty jira token
        assert config["jira"]["api_token"] != ""
        assert config["jira"]["api_token"] == "jira-test-token"

    @responses_lib.activate
    @patch("builtins.input")
    def test_non_developer_skips_jira_token(self, mock_input, tmp_path):
        """Non-developer roles (PO/Sales) should not get a Jira token prompt."""
        self._register_tempo_user()
        mock_input.side_effect = self._po_inputs()
        cfg_path = tmp_path / "config.json"

        cm = ConfigManager.__new__(ConfigManager)
        cm.config_path = cfg_path
        config = cm.setup_wizard()

        assert config["jira"]["api_token"] == ""

    @responses_lib.activate
    @patch("builtins.input")
    def test_sales_wizard_flow(self, mock_input, tmp_path):
        """Sales role wizard should work with manual activities."""
        self._register_tempo_user()
        inputs = [
            "sales@example.com",    # email
            "3",                    # role: sales
            "tempo-sales-token",    # tempo token
            "Test Sales",           # name
            "8",                    # daily hours
            "1",                    # location: US
            "no",                   # enable email: no
            "no",                   # add activity? no
        ]
        mock_input.side_effect = inputs
        cfg_path = tmp_path / "config.json"

        cm = ConfigManager.__new__(ConfigManager)
        cm.config_path = cfg_path
        config = cm.setup_wizard()

        assert config["user"]["role"] == "sales"
        assert config["manual_activities"] == []
        assert config["jira"]["api_token"] == ""

    @responses_lib.activate
    @patch("builtins.input")
    def test_wizard_sets_hardcoded_jira_url(self, mock_input, tmp_path):
        """The wizard should set the hardcoded Jira URL (org default)."""
        self._register_tempo_user()
        self._register_jira_myself()
        mock_input.side_effect = self._developer_inputs()
        cfg_path = tmp_path / "config.json"

        cm = ConfigManager.__new__(ConfigManager)
        cm.config_path = cfg_path
        config = cm.setup_wizard()

        assert config["jira"]["url"] == "lmsportal.atlassian.net"

    @responses_lib.activate
    @patch("builtins.input")
    def test_wizard_config_has_all_required_sections(self, mock_input, tmp_path):
        """The wizard output must contain all top-level config sections."""
        self._register_tempo_user()
        self._register_jira_myself()
        mock_input.side_effect = self._developer_inputs()
        cfg_path = tmp_path / "config.json"

        cm = ConfigManager.__new__(ConfigManager)
        cm.config_path = cfg_path
        config = cm.setup_wizard()

        required_sections = [
            "user", "jira", "tempo", "organization", "schedule",
            "notifications", "manual_activities", "options",
        ]
        for section in required_sections:
            assert section in config, f"Missing section: {section}"

    def _existing_developer_config(self):
        """A complete config dict simulating a prior developer install."""
        return {
            "user": {"email": "dev@example.com", "name": "Test Dev", "role": "developer"},
            "tempo": {"api_token": "old-tempo-token"},
            "jira": {"api_token": "old-jira-token", "email": "dev@example.com"},
            "schedule": {"daily_hours": 8.0, "country_code": "US", "state": ""},
            "notifications": {"email_enabled": False},
            "manual_activities": [],
            "options": {},
            "organization": {},
            "overhead": {},
        }

    @responses_lib.activate
    @patch("builtins.input")
    def test_reinstall_valid_tokens_reused(self, mock_input, tmp_path):
        """Re-install with valid existing tokens should reuse both without prompting."""
        cfg_path = tmp_path / "config.json"
        with open(cfg_path, "w") as f:
            json.dump(self._existing_developer_config(), f)

        responses_lib.add(
            responses_lib.GET,
            "https://api.tempo.io/4/work-attributes",
            json={"results": []},
            status=200,
        )
        responses_lib.add(
            responses_lib.GET,
            "https://lmsportal.atlassian.net/rest/api/3/myself",
            json={"displayName": "Test Dev", "accountId": "test-id"},
            status=200,
        )

        mock_input.side_effect = []  # no prompts expected
        cm = ConfigManager.__new__(ConfigManager)
        cm.config_path = cfg_path
        config = cm.setup_wizard()

        assert config["tempo"]["api_token"] == "old-tempo-token"
        assert config["jira"]["api_token"] == "old-jira-token"
        assert config["user"]["role"] == "developer"

    @responses_lib.activate
    @patch("builtins.input")
    def test_reinstall_expired_tempo_token_prompts_new(self, mock_input, tmp_path):
        """Re-install with expired Tempo token (401) should prompt for a new one."""
        cfg_path = tmp_path / "config.json"
        with open(cfg_path, "w") as f:
            json.dump(self._existing_developer_config(), f)

        # Old token fails verification
        responses_lib.add(
            responses_lib.GET,
            "https://api.tempo.io/4/work-attributes",
            json={"error": "Unauthorized"},
            status=401,
        )
        # New token verification succeeds
        responses_lib.add(
            responses_lib.GET,
            "https://api.tempo.io/4/work-attributes",
            json={"results": []},
            status=200,
        )
        responses_lib.add(
            responses_lib.GET,
            "https://lmsportal.atlassian.net/rest/api/3/myself",
            json={"displayName": "Test Dev", "accountId": "test-id"},
            status=200,
        )

        mock_input.side_effect = ["new-tempo-token"]
        cm = ConfigManager.__new__(ConfigManager)
        cm.config_path = cfg_path
        config = cm.setup_wizard()

        assert config["tempo"]["api_token"] == "new-tempo-token"
        assert config["jira"]["api_token"] == "old-jira-token"

    @responses_lib.activate
    @patch("builtins.input")
    def test_reinstall_expired_jira_token_prompts_new(self, mock_input, tmp_path):
        """Re-install with expired Jira token (401) should prompt for a new one."""
        cfg_path = tmp_path / "config.json"
        with open(cfg_path, "w") as f:
            json.dump(self._existing_developer_config(), f)

        # Tempo token valid
        responses_lib.add(
            responses_lib.GET,
            "https://api.tempo.io/4/work-attributes",
            json={"results": []},
            status=200,
        )
        # Old Jira token fails
        responses_lib.add(
            responses_lib.GET,
            "https://lmsportal.atlassian.net/rest/api/3/myself",
            json={"error": "Unauthorized"},
            status=401,
        )
        # New Jira token verification succeeds
        responses_lib.add(
            responses_lib.GET,
            "https://lmsportal.atlassian.net/rest/api/3/myself",
            json={"displayName": "Test Dev", "accountId": "test-id"},
            status=200,
        )

        mock_input.side_effect = ["new-jira-token"]
        cm = ConfigManager.__new__(ConfigManager)
        cm.config_path = cfg_path
        config = cm.setup_wizard()

        assert config["jira"]["api_token"] == "new-jira-token"
        assert config["tempo"]["api_token"] == "old-tempo-token"


# ===========================================================================
# ConfigManager._select_role
# ===========================================================================

class TestSelectRole:
    """Tests for the _select_role interactive helper."""

    @patch("builtins.input", return_value="1")
    def test_returns_developer_for_choice_1(self, mock_input):
        """Choice '1' should return 'developer'."""
        cm = ConfigManager.__new__(ConfigManager)
        assert cm._select_role() == "developer"

    @patch("builtins.input", return_value="2")
    def test_returns_product_owner_for_choice_2(self, mock_input):
        """Choice '2' should return 'product_owner'."""
        cm = ConfigManager.__new__(ConfigManager)
        assert cm._select_role() == "product_owner"

    @patch("builtins.input", return_value="3")
    def test_returns_sales_for_choice_3(self, mock_input):
        """Choice '3' should return 'sales'."""
        cm = ConfigManager.__new__(ConfigManager)
        assert cm._select_role() == "sales"

    @patch("builtins.input", side_effect=["x", "0", "4", "abc", "1"])
    def test_retries_on_invalid_input(self, mock_input):
        """Invalid choices should be rejected; loop continues until valid."""
        cm = ConfigManager.__new__(ConfigManager)
        result = cm._select_role()
        assert result == "developer"
        # Should have been called 5 times (4 invalid + 1 valid)
        assert mock_input.call_count == 5

    @patch("builtins.input", side_effect=["", " ", "2"])
    def test_retries_on_empty_input(self, mock_input):
        """Empty or whitespace input should be rejected."""
        cm = ConfigManager.__new__(ConfigManager)
        result = cm._select_role()
        assert result == "product_owner"
        assert mock_input.call_count == 3


# ===========================================================================
# ConfigManager._select_location
# ===========================================================================

class TestSelectLocation:
    """Tests for the _select_location interactive helper."""

    @patch("builtins.input", return_value="1")
    def test_us_returns_us_empty(self, mock_input):
        """Choice '1' (US) should return ('US', '')."""
        cm = ConfigManager.__new__(ConfigManager)
        country, state = cm._select_location()
        assert country == "US"
        assert state == ""

    @patch("builtins.input", return_value="2")
    def test_india_pune_returns_in_mh(self, mock_input):
        """Choice '2' (India - Pune) should return ('IN', 'MH')."""
        cm = ConfigManager.__new__(ConfigManager)
        country, state = cm._select_location()
        assert country == "IN"
        assert state == "MH"

    @patch("builtins.input", return_value="3")
    def test_india_hyderabad_returns_in_tg(self, mock_input):
        """Choice '3' (India - Hyderabad) should return ('IN', 'TG')."""
        cm = ConfigManager.__new__(ConfigManager)
        country, state = cm._select_location()
        assert country == "IN"
        assert state == "TG"

    @patch("builtins.input", return_value="4")
    def test_india_gandhinagar_returns_in_gj(self, mock_input):
        """Choice '4' (India - Gandhinagar) should return ('IN', 'GJ')."""
        cm = ConfigManager.__new__(ConfigManager)
        country, state = cm._select_location()
        assert country == "IN"
        assert state == "GJ"

    @patch("builtins.input", side_effect=["5", "GB", ""])
    def test_other_prompts_for_codes(self, mock_input):
        """Choice '5' (Other) should prompt for country and state codes."""
        cm = ConfigManager.__new__(ConfigManager)
        country, state = cm._select_location()
        assert country == "GB"
        assert state == ""
        # 3 calls: initial choice, country code, state code
        assert mock_input.call_count == 3

    @patch("builtins.input", side_effect=["5", "ca", "on"])
    def test_other_uppercases_codes(self, mock_input):
        """Country and state codes from choice '5' should be uppercased."""
        cm = ConfigManager.__new__(ConfigManager)
        country, state = cm._select_location()
        assert country == "CA"
        assert state == "ON"

    @patch("builtins.input", side_effect=["x", "0", "6", "abc", "1"])
    def test_retries_on_invalid_choice(self, mock_input):
        """Invalid choices should loop until a valid one (1-5) is entered."""
        cm = ConfigManager.__new__(ConfigManager)
        country, state = cm._select_location()
        assert country == "US"
        assert state == ""
        # 4 invalid + 1 valid = 5 calls
        assert mock_input.call_count == 5

    @patch("builtins.input", side_effect=["5", "DE", "BY"])
    def test_other_with_state(self, mock_input):
        """Choice '5' with both country and state codes returns both."""
        cm = ConfigManager.__new__(ConfigManager)
        country, state = cm._select_location()
        assert country == "DE"
        assert state == "BY"


# ===========================================================================
# ConfigManager.validate_config
# ===========================================================================

class TestConfigValidation:
    """Tests for _validate_config() called during config loading.

    The validation is integrated into load_config() and raises SystemExit(1)
    when validation fails.  These tests verify the validation logic by either:
    - Calling _validate_config() directly on a bare ConfigManager instance
    - Checking that ConfigManager() raises SystemExit for invalid configs
    """

    def _make_bare_cm(self):
        """Create a ConfigManager without calling __init__ (bypasses load)."""
        return ConfigManager.__new__(ConfigManager)

    def test_missing_user_email(self, developer_config, capsys):
        """Config with empty user.email -> _validate_config returns False, prints error."""
        developer_config["user"]["email"] = ""
        cm = self._make_bare_cm()

        result = cm._validate_config(developer_config)

        assert result is False
        captured = capsys.readouterr()
        assert "user.email" in captured.out

    def test_missing_tempo_token(self, developer_config, capsys):
        """Empty tempo.api_token -> _validate_config returns False."""
        developer_config["tempo"]["api_token"] = ""
        cm = self._make_bare_cm()

        result = cm._validate_config(developer_config)

        assert result is False
        captured = capsys.readouterr()
        assert "tempo.api_token" in captured.out

    def test_missing_daily_hours(self, developer_config, capsys):
        """Missing schedule.daily_hours -> _validate_config returns False.

        The current implementation requires daily_hours to be explicitly set
        (no implicit default during validation).
        """
        del developer_config["schedule"]["daily_hours"]
        cm = self._make_bare_cm()

        result = cm._validate_config(developer_config)

        assert result is False
        captured = capsys.readouterr()
        assert "daily_hours" in captured.out

    def test_valid_config_passes(self, developer_config):
        """A fully valid developer config -> _validate_config returns True."""
        cm = self._make_bare_cm()

        result = cm._validate_config(developer_config)

        assert result is True

    def test_missing_jira_token_developer(self, developer_config, capsys):
        """Developer role with empty jira.api_token -> validation fails."""
        developer_config["jira"]["api_token"] = ""
        cm = self._make_bare_cm()

        result = cm._validate_config(developer_config)

        assert result is False
        captured = capsys.readouterr()
        assert "jira.api_token" in captured.out

    def test_missing_jira_token_po_ok(self, po_config):
        """PO role without jira.api_token -> validation passes."""
        po_config["jira"]["api_token"] = ""
        cm = self._make_bare_cm()

        result = cm._validate_config(po_config)

        assert result is True

    def test_corrupted_json(self, tmp_path, capsys):
        """Invalid JSON in config file -> load_config raises SystemExit."""
        bad_file = tmp_path / "config.json"
        bad_file.write_text("{not valid json!!!", encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            ConfigManager(config_path=bad_file)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "corrupted" in captured.out.lower() or "json" in captured.out.lower()

    def test_empty_config_file(self, tmp_path, capsys):
        """Empty file -> raises SystemExit with appropriate error message."""
        empty_file = tmp_path / "config.json"
        empty_file.write_text("", encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            ConfigManager(config_path=empty_file)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "empty" in captured.out.lower() or "setup" in captured.out.lower()

    def test_config_version_present(self):
        """config_template.json should have a config_version field.

        This documents the expectation that the template includes versioning
        support for future config migrations.
        """
        import os
        template_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "config_template.json"
        )
        with open(template_path, "r", encoding="utf-8") as f:
            template = json.load(f)

        # config_version should be present in the template
        assert "config_version" in template, (
            "config_template.json should contain a 'config_version' field"
        )
