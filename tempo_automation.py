#!/usr/bin/env python3
"""
Tempo Timesheet Automation Script
==================================

Automates daily timesheet entry and monthly submission for Tempo/Jira users.

Features:
- Auto-syncs Jira worklogs to Tempo (for developers)
- Supports manual configuration (for POs, Sales)
- Auto-submits timesheets at month-end
- Email notifications
- Error handling and retry logic

Author: Vector Solutions Engineering Team
Version: 1.0.0
Date: February 2026
"""

import argparse
import calendar
import html
import io
import json
import logging
import os
import re
import smtplib
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests

# Optional keyring for cross-platform credential storage (Mac/Linux)
try:
    import keyring as _keyring_mod
except ImportError:
    _keyring_mod = None

# Force UTF-8 output to avoid UnicodeEncodeError on Windows when redirecting to file.
# Under pythonw.exe (no console), sys.stdout/stderr are None -- redirect to devnull.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
elif sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")
elif sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


class DualWriter:
    """Writes to both the console (original stdout) and an external log file."""

    def __init__(self, console, logfile_path: str):
        self.console = console
        self.logfile = open(logfile_path, "a", encoding="utf-8")

    def write(self, text):
        self.console.write(text)
        self.logfile.write(text)
        self.logfile.flush()

    def flush(self):
        self.console.flush()
        self.logfile.flush()

    def close(self):
        self.logfile.close()


class JsonLogFormatter(logging.Formatter):
    """JSON log formatter for structured logging."""

    def format(self, record):
        log_entry = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "message": record.getMessage(),
            "module": record.module,
            "line": record.lineno,
        }
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry)


# ============================================================================
# CONFIGURATION
# ============================================================================

# Script directory
SCRIPT_DIR = Path(__file__).parent
CONFIG_FILE = SCRIPT_DIR / "config.json"
LOG_FILE = SCRIPT_DIR / "tempo_automation.log"
SHORTFALL_FILE = SCRIPT_DIR / "monthly_shortfall.json"
SUBMITTED_FILE = SCRIPT_DIR / "monthly_submitted.json"


# Persistent backup location for config -- survives re-installation to a new folder
# Windows: %APPDATA%\TempoAutomation\config.json
# Mac/Linux: ~/.config/TempoAutomation/config.json
def _get_config_backup_path() -> Path | None:
    try:
        if sys.platform == "win32":
            appdata = os.environ.get("APPDATA", "")
            if appdata:
                return Path(appdata) / "TempoAutomation" / "config.json"
        else:
            return Path.home() / ".config" / "TempoAutomation" / "config.json"
    except Exception:
        pass
    return None


CONFIG_BACKUP_FILE: Path | None = _get_config_backup_path()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


# ============================================================================
# CREDENTIAL MANAGER (DPAPI on Windows, keyring on Mac/Linux)
# ============================================================================


class CredentialManager:
    """Encrypt/decrypt sensitive config values.

    On Windows: uses DPAPI (ties encryption to current user account).
    On Mac/Linux: uses keyring library (system credential store).
    Encrypted values are stored as 'ENC:<base64>' in config.json (DPAPI).
    Keyring values are stored in the OS credential store, and the
    original plain-text value is kept in config.json unchanged.
    Plain-text values are accepted for backward compatibility on
    all platforms and will be returned as-is by decrypt().
    """

    PREFIX = "ENC:"
    KEYRING_SERVICE = "tempo-automation"

    _use_dpapi = sys.platform == "win32"
    _use_keyring = not _use_dpapi and _keyring_mod is not None

    @staticmethod
    def encrypt(plain_text: str, key: str = "") -> str:
        """Encrypt a string using the platform credential store.

        Args:
            plain_text: The value to encrypt/store.
            key: Identifier for keyring storage (e.g. 'jira_token').
                 Ignored on Windows (DPAPI encrypts inline).

        Returns 'ENC:<base64>' on Windows, plain text on other
        platforms (keyring stores the secret separately).
        """
        if not plain_text:
            return plain_text

        # Mac/Linux: store in keyring if available
        if CredentialManager._use_keyring and key:
            try:
                _keyring_mod.set_password(CredentialManager.KEYRING_SERVICE, key, plain_text)
                logger.info(f"Credential stored in keyring for key: {key}")
                return plain_text
            except Exception as e:
                logger.warning(f"Keyring store failed: {e}")
                return plain_text

        # Windows: DPAPI encryption
        if not CredentialManager._use_dpapi:
            return plain_text

        try:
            import ctypes
            import ctypes.wintypes as wt

            class BLOB(ctypes.Structure):
                _fields_ = [
                    ("cbData", wt.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_byte)),
                ]

            raw = plain_text.encode("utf-8")
            inp = BLOB()
            inp.cbData = len(raw)
            inp.pbData = (ctypes.c_byte * len(raw))(*raw)

            out = BLOB()
            if ctypes.windll.crypt32.CryptProtectData(
                ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)
            ):
                enc = bytes(
                    (ctypes.c_byte * out.cbData).from_address(ctypes.addressof(out.pbData.contents))
                )
                ctypes.windll.kernel32.LocalFree(out.pbData)
                import base64

                return f"{CredentialManager.PREFIX}{base64.b64encode(enc).decode('ascii')}"
            return plain_text
        except Exception as e:
            logger.warning(f"DPAPI encrypt failed: {e}")
            return plain_text

    @staticmethod
    def decrypt(value: str, key: str = "") -> str:
        """Decrypt a credential using the platform credential store.

        Args:
            value: The stored value (may be 'ENC:<base64>' on Windows,
                   or plain text on other platforms).
            key: Identifier for keyring lookup (e.g. 'jira_token').
                 Ignored on Windows (DPAPI decrypts inline).

        Returns plain text. If value is not encrypted (no ENC:
        prefix and no keyring entry), returns it unchanged.
        """
        if not value:
            return value

        # Mac/Linux: try keyring first
        if CredentialManager._use_keyring and key:
            try:
                stored = _keyring_mod.get_password(CredentialManager.KEYRING_SERVICE, key)
                if stored is not None:
                    return stored
            except Exception as e:
                logger.warning(f"Keyring retrieve failed: {e}")

        # Not an encrypted value -- return as-is (plain-text fallback)
        if not value.startswith(CredentialManager.PREFIX):
            return value

        # DPAPI decryption (Windows only)
        if not CredentialManager._use_dpapi:
            logger.warning("Cannot decrypt DPAPI value on non-Windows")
            return value

        try:
            import base64
            import ctypes
            import ctypes.wintypes as wt

            class BLOB(ctypes.Structure):
                _fields_ = [
                    ("cbData", wt.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_byte)),
                ]

            raw = base64.b64decode(value[len(CredentialManager.PREFIX) :])
            inp = BLOB()
            inp.cbData = len(raw)
            inp.pbData = (ctypes.c_byte * len(raw))(*raw)

            out = BLOB()
            if ctypes.windll.crypt32.CryptUnprotectData(
                ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)
            ):
                dec = bytes(
                    (ctypes.c_byte * out.cbData).from_address(ctypes.addressof(out.pbData.contents))
                )
                ctypes.windll.kernel32.LocalFree(out.pbData)
                return dec.decode("utf-8")
            return value
        except Exception as e:
            logger.warning(f"DPAPI decrypt failed: {e}")
            return value


# ============================================================================
# CONFIGURATION MANAGER
# ============================================================================


class ConfigManager:
    """Manages user configuration and credentials."""

    def __init__(self, config_path: Path = CONFIG_FILE):
        self.config_path = config_path
        self.config = self.load_config()

    def load_config(self) -> dict:
        """Load configuration from file or create new one."""
        if not self.config_path.exists():
            logger.info("No configuration found. Starting setup wizard...")
            config = self.setup_wizard()
            if config is None:
                raise SystemExit(1)
            return config

        try:
            with open(self.config_path) as f:
                content = f.read()
            if not content.strip():
                print("[FAIL] Config file is empty. Run --setup to configure.")
                raise SystemExit(1)
            config = json.loads(content)
            logger.info("Configuration loaded successfully")
            if not self._validate_config(config):
                raise SystemExit(1)
            return config
        except json.JSONDecodeError as e:
            print(f"[FAIL] Config file is corrupted: {e}. Run --setup to reconfigure.")
            raise SystemExit(1)
        except SystemExit:
            raise
        except Exception as e:
            logger.error(f"Error loading configuration: {e}")
            raise

    def _validate_config(self, config: dict) -> bool:
        """Validate config has required keys with valid values.

        Returns:
            True if valid, False if any validation fails.
        """
        valid = True

        # user.email must exist and not be empty
        user_email = config.get("user", {}).get("email", "")
        if not user_email:
            print("[FAIL] Config validation: Missing required field 'user.email'")
            valid = False

        # user.role must be one of the allowed values
        user_role = config.get("user", {}).get("role", "")
        allowed_roles = ("developer", "qa", "product_owner", "sales")
        if user_role not in allowed_roles:
            print(
                f"[FAIL] Config validation: Invalid 'user.role' "
                f"'{user_role}'. Must be one of: "
                f"{', '.join(allowed_roles)}"
            )
            valid = False

        # tempo.api_token must exist and not be empty
        tempo_token = config.get("tempo", {}).get("api_token", "")
        if not tempo_token:
            print("[FAIL] Config validation: Missing required field 'tempo.api_token'")
            valid = False

        # jira.api_token required for developer and qa roles
        if user_role in ("developer", "qa"):
            jira_token = config.get("jira", {}).get("api_token", "")
            if not jira_token:
                print(
                    "[FAIL] Config validation: Missing required "
                    f"field 'jira.api_token' (required for "
                    f"{user_role} role)"
                )
                valid = False

        # schedule.daily_hours must be between 0.5 and 24
        daily_hours = config.get("schedule", {}).get("daily_hours", None)
        if daily_hours is None:
            print("[FAIL] Config validation: Missing required field 'schedule.daily_hours'")
            valid = False
        elif not isinstance(daily_hours, int | float):
            print("[FAIL] Config validation: 'schedule.daily_hours' must be a number")
            valid = False
        elif daily_hours < 0.5 or daily_hours > 24:
            print(
                f"[FAIL] Config validation: "
                f"'schedule.daily_hours' must be between "
                f"0.5 and 24 (got {daily_hours})"
            )
            valid = False

        if not valid:
            print("\n[INFO] Run --setup to reconfigure.")

        return valid

    def setup_wizard(self) -> dict:
        """Interactive setup wizard for first-time or re-installation setup.

        On re-installation, existing credentials are revalidated automatically.
        Questions are only asked for credentials that are missing or invalid.
        """
        import base64
        import re

        max_retries = 3

        # Load existing config for reuse on re-installation.
        # Check local script dir first; fall back to persistent AppData backup
        # so re-installs to a new folder still detect prior credentials.
        existing: dict = {}
        existing_source = ""
        for candidate in [self.config_path, CONFIG_BACKUP_FILE]:
            if candidate and candidate.exists():
                try:
                    with open(candidate) as f:
                        existing = json.loads(f.read())
                    existing_source = str(candidate)
                    break
                except Exception:
                    existing = {}

        has_existing = bool(existing)

        print("\n" + "=" * 60)
        if has_existing:
            print("TEMPO AUTOMATION - SETUP (RE-INSTALLATION DETECTED)")
            print("=" * 60)
            print(f"\nExisting configuration found: {existing_source}")
            print(
                "Valid credentials will be reused automatically."
                " Only invalid or missing credentials will be asked.\n"
            )
        else:
            print("TEMPO AUTOMATION - FIRST TIME SETUP")
            print("=" * 60)
            print("\nThis wizard will help you set up the automation.")
            print("Your credentials will be stored locally and encrypted.\n")

        # ------------------------------------------------------------------ #
        # USER INFORMATION                                                     #
        # ------------------------------------------------------------------ #
        print("--- USER INFORMATION ---")

        # Email
        existing_email = existing.get("user", {}).get("email", "")
        if existing_email:
            print(f"Email address: {existing_email} (reusing existing)")
            user_email = existing_email
        else:
            while True:
                user_email = input("Enter your email address: ").strip()
                if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", user_email):
                    break
                print("Invalid email format. Please try again.")

        # Name (populated later from Jira profile if possible)
        user_name = existing.get("user", {}).get("name", "")

        # Role
        existing_role = existing.get("user", {}).get("role", "")
        if existing_role in ("developer", "qa", "product_owner", "sales"):
            print(f"Role: {existing_role} (reusing existing)")
            user_role = existing_role
        else:
            user_role = self._select_role()

        # ------------------------------------------------------------------ #
        # JIRA/TEMPO CONFIGURATION                                            #
        # ------------------------------------------------------------------ #
        print("\n--- JIRA/TEMPO CONFIGURATION ---")
        jira_url = "lmsportal.atlassian.net"
        print(f"Jira URL: {jira_url} (organization default)")

        # -- Tempo token ---------------------------------------------------
        existing_tempo_token = existing.get("tempo", {}).get("api_token", "")
        tempo_token = existing_tempo_token
        tempo_needs_input = True

        if existing_tempo_token:
            print("\nTempo API token: [found existing] - verifying...")
            try:
                resp = requests.get(
                    "https://api.tempo.io/4/work-attributes",
                    headers={"Authorization": f"Bearer {existing_tempo_token}"},
                    timeout=30,
                )
                resp.raise_for_status()
                print("[OK] Existing Tempo token is still valid - reusing")
                tempo_needs_input = False
            except requests.exceptions.HTTPError as e:
                if e.response is not None and e.response.status_code in (401, 403):
                    print(
                        "[FAIL] Existing Tempo token is invalid or "
                        "expired. A new token is required."
                    )
                    tempo_token = ""
                else:
                    # Non-auth HTTP error (e.g. 500) -- reuse token
                    logger.warning(f"Could not fully verify Tempo token: {e}")
                    print(
                        "[!] Could not verify Tempo token (network/server issue) - reusing existing"
                    )
                    tempo_needs_input = False
            except Exception as e:
                logger.warning(f"Could not verify Tempo token: {e}")
                print("[!] Could not verify Tempo token (network issue) - reusing existing")
                tempo_needs_input = False

        if tempo_needs_input:
            print("\n[INFO] To get your Tempo API token:")
            print(
                "   1. Go to https://lmsportal.atlassian.net/plugins/"
                "servlet/ac/io.tempo.jira/tempo-app#!/configuration/"
                "api-integration"
            )
            print("   2. Click 'New Token'")
            print("   3. Give it a name (e.g., 'Tempo Automation')")
            print("   4. Copy the generated token")
            tempo_token = input("\nEnter your Tempo API token: ").strip()

            for attempt in range(max_retries):
                try:
                    resp = requests.get(
                        "https://api.tempo.io/4/work-attributes",
                        headers={"Authorization": f"Bearer {tempo_token}"},
                        timeout=30,
                    )
                    resp.raise_for_status()
                    print("[OK] Tempo API token verified")
                    break
                except requests.exceptions.HTTPError as e:
                    if e.response is not None and e.response.status_code in (401, 403, 404):
                        print(
                            "\n[FAIL] Authentication failed. "
                            "The Tempo API token is invalid or expired."
                        )
                        if attempt < max_retries - 1:
                            print(
                                "Please re-enter your Tempo API token (or press Ctrl+C to cancel)."
                            )
                            tempo_token = input("Tempo API token: ").strip()
                        else:
                            print("\n[FAIL] Could not verify Tempo token after 3 attempts.")
                            print()
                            print(
                                "***********   Setup cannot "
                                "continue without valid "
                                "credentials.   *************"
                            )
                            print(
                                "***********   Please start a "
                                "fresh setup with correct "
                                "credentials.   ***********"
                            )
                            print("\nSetup aborted.")
                            return None
                    else:
                        logger.warning(f"Could not verify Tempo token: {e}")
                        break
                except Exception as e:
                    logger.warning(f"Could not verify Tempo token: {e}")
                    break

        # -- Jira token (developers and QA) --------------------------------
        if user_role in ("developer", "qa"):
            existing_jira_token = existing.get("jira", {}).get("api_token", "")
            existing_jira_email = existing.get("jira", {}).get("email", user_email)
            jira_token = existing_jira_token
            jira_email = existing_jira_email
            jira_needs_input = True

            if existing_jira_token:
                print("\nJira API token: [found existing] - verifying...")
                try:
                    creds = base64.b64encode(
                        f"{jira_email}:{existing_jira_token}".encode()
                    ).decode()
                    resp = requests.get(
                        f"https://{jira_url}/rest/api/3/myself",
                        headers={"Authorization": f"Basic {creds}"},
                        timeout=30,
                    )
                    resp.raise_for_status()
                    jira_name = resp.json().get("displayName", "")
                    if jira_name:
                        user_name = jira_name
                        print(f"[OK] Existing Jira token is still valid - Welcome, {user_name}!")
                    else:
                        print("[OK] Existing Jira token is still valid - reusing")
                    jira_needs_input = False
                except requests.exceptions.HTTPError as e:
                    if e.response is not None and e.response.status_code == 401:
                        print(
                            "[FAIL] Existing Jira token is invalid or "
                            "expired. A new token is required."
                        )
                        jira_token = ""
                    else:
                        logger.warning(f"Could not fully verify Jira token: {e}")
                        print(
                            "[!] Could not verify Jira token "
                            "(network/server issue) - reusing existing"
                        )
                        jira_needs_input = False
                except Exception as e:
                    logger.warning(f"Could not verify Jira token: {e}")
                    print("[!] Could not verify Jira token (network issue) - reusing existing")
                    jira_needs_input = False

            if jira_needs_input:
                print("\n[INFO] To get your Jira API token:")
                print("   1. Go to https://id.atlassian.com/manage-profile/security/api-tokens")
                print("   2. Click 'Create API token'")
                jira_token = input("\nEnter your Jira API token: ").strip()
                jira_email = user_email

                for attempt in range(max_retries):
                    try:
                        creds = base64.b64encode(f"{jira_email}:{jira_token}".encode()).decode()
                        resp = requests.get(
                            f"https://{jira_url}/rest/api/3/myself",
                            headers={"Authorization": f"Basic {creds}"},
                            timeout=30,
                        )
                        resp.raise_for_status()
                        jira_name = resp.json().get("displayName", "")
                        if jira_name:
                            user_name = jira_name
                            print(f"[OK] Welcome, {user_name}!")
                        break
                    except requests.exceptions.HTTPError as e:
                        if e.response is not None and e.response.status_code == 401:
                            print(
                                "\n[FAIL] Authentication failed. "
                                "Either the email or Jira API token "
                                "is incorrect."
                            )
                            if attempt < max_retries - 1:
                                print(
                                    "Please re-enter your credentials (or press Ctrl+C to cancel)."
                                )
                                while True:
                                    jira_email = input("Email address: ").strip()
                                    if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", jira_email):
                                        break
                                    print("Invalid email format. Please try again.")
                                user_email = jira_email
                                jira_token = input("Jira API token: ").strip()
                            else:
                                print(
                                    "\n[FAIL] Could not verify Jira credentials after 3 attempts."
                                )
                                print()
                                print(
                                    "***********   Setup cannot "
                                    "continue without valid "
                                    "credentials.   *************"
                                )
                                print(
                                    "***********   Please start a "
                                    "fresh setup with correct "
                                    "credentials.   ***********"
                                )
                                print("\nSetup aborted.")
                                return None
                        else:
                            logger.warning(f"Could not fetch Jira profile: {e}")
                            break
                    except Exception as e:
                        logger.warning(f"Could not fetch Jira profile: {e}")
                        break

            if not user_name:
                user_name = input("Enter your full name: ").strip()
        else:
            jira_token = ""
            jira_email = user_email
            if not user_name:
                user_name = input("Enter your full name: ").strip()

        # ------------------------------------------------------------------ #
        # WORK SCHEDULE & LOCATION                                            #
        # ------------------------------------------------------------------ #
        print("\n--- WORK SCHEDULE & LOCATION ---")

        existing_daily_hours = existing.get("schedule", {}).get("daily_hours", None)
        if existing_daily_hours is not None:
            print(f"Daily hours: {existing_daily_hours} (reusing existing)")
            daily_hours = existing_daily_hours
        else:
            while True:
                try:
                    daily_hours = float(
                        input("Standard work hours per day (default 8): ").strip() or "8"
                    )
                    break
                except ValueError:
                    print("  [ERROR] Please enter a valid number.")

        existing_country = existing.get("schedule", {}).get("country_code", "")
        existing_state = existing.get("schedule", {}).get("state", "")
        if existing_country:
            loc_display = existing_country
            if existing_state:
                loc_display += f"/{existing_state}"
            print(f"Location: {loc_display} (reusing existing)")
            country_code = existing_country
            state_code = existing_state
        else:
            country_code, state_code = self._select_location()

        # Organization holidays URL
        holidays_url = (
            "https://ajay-sajwan-vectorsolutions.github.io/local-assets/org_holidays.json"
        )
        print(f"\nOrg holidays URL: {holidays_url} (organization default)")

        # Teams webhook (disabled -- pending Graph API)
        teams_webhook = existing.get("notifications", {}).get("teams_webhook_url", "")

        # ------------------------------------------------------------------ #
        # EMAIL NOTIFICATIONS                                                 #
        # ------------------------------------------------------------------ #
        existing_notif = existing.get("notifications", {})
        if has_existing and "email_enabled" in existing_notif:
            enable_email = existing_notif.get("email_enabled", False)
            smtp_server = existing_notif.get("smtp_server", "smtp.office365.com")
            smtp_port = existing_notif.get("smtp_port", 587)
            smtp_user = existing_notif.get("smtp_user", user_email)
            smtp_password = existing_notif.get("smtp_password", "")
            print(
                f"\nEmail notifications: "
                f"{'enabled' if enable_email else 'disabled'}"
                f" (reusing existing)"
            )
        else:
            print("\n--- EMAIL NOTIFICATIONS ---")
            print("SMTP server: smtp.office365.com (auto-configured)")
            enable_email = (
                input("Enable email notifications? (yes/no, default: no): ").strip().lower()
            )
            enable_email = enable_email in ["yes", "y"]

            smtp_server = "smtp.office365.com"
            smtp_port = 587
            smtp_user = user_email
            smtp_password = ""
            if enable_email:
                print(f"\nSMTP login will use your email: {user_email}")
                print("[INFO] If MFA is enabled, create an App Password at")
                print("   https://mysignins.microsoft.com/security-info")
                raw_password = input("Enter your email/app password: ").strip()
                smtp_password = CredentialManager.encrypt(raw_password, key="smtp_password")
                print("[OK] Password encrypted and saved securely")

        # ------------------------------------------------------------------ #
        # MANUAL ACTIVITIES (non-developers)                                  #
        # ------------------------------------------------------------------ #
        existing_activities = existing.get("manual_activities", [])
        if has_existing and existing_activities:
            print(f"\nManual activities: {len(existing_activities)} configured (reusing existing)")
            manual_activities = existing_activities
        elif user_role in ["product_owner", "sales"]:
            manual_activities = []
            print("\n--- DEFAULT ACTIVITIES ---")
            print("Set up your typical daily activities (optional)")
            while True:
                add_activity = input("\nAdd a default activity? (yes/no): ").strip().lower()
                if add_activity not in ["yes", "y"]:
                    break
                activity = input("Activity name (e.g., 'Stakeholder Meetings'): ").strip()
                while True:
                    try:
                        hours = float(input("Typical hours per day: ").strip())
                        break
                    except ValueError:
                        print("  [ERROR] Please enter a valid number.")
                manual_activities.append({"activity": activity, "hours": hours})
        else:
            manual_activities = []

        # ------------------------------------------------------------------ #
        # BUILD CONFIGURATION                                                 #
        # Preserve existing schedule overrides (PTO, holidays, etc.)         #
        # ------------------------------------------------------------------ #
        existing_schedule = existing.get("schedule", {})
        config = {
            "config_version": "4.0",
            "user": {"email": user_email, "name": user_name, "role": user_role},
            "jira": {"url": jira_url, "email": jira_email, "api_token": jira_token},
            "tempo": {"api_token": tempo_token},
            "organization": {"holidays_url": holidays_url},
            "schedule": {
                "daily_hours": daily_hours,
                "daily_sync_time": existing_schedule.get("daily_sync_time", "18:00"),
                "monthly_submit_day": existing_schedule.get("monthly_submit_day", "last"),
                "country_code": country_code,
                "state": state_code,
                # Preserve user overrides from previous install
                "pto_days": existing_schedule.get("pto_days", []),
                "extra_holidays": existing_schedule.get("extra_holidays", []),
                "working_days": existing_schedule.get("working_days", []),
            },
            "notifications": {
                "email_enabled": enable_email,
                "smtp_server": smtp_server,
                "smtp_port": smtp_port,
                "smtp_user": smtp_user,
                "smtp_password": smtp_password,
                "notification_email": existing_notif.get("notification_email", user_email),
                "teams_webhook_url": teams_webhook,
                "notify_on_shortfall": existing_notif.get("notify_on_shortfall", True),
            },
            "manual_activities": manual_activities,
            "options": existing.get(
                "options",
                {"auto_submit": True, "require_confirmation": False, "sync_on_startup": False},
            ),
        }

        # Preserve overhead config if present
        if "overhead" in existing:
            config["overhead"] = existing["overhead"]

        # Preserve distribution_weights if present
        if "distribution_weights" in existing.get("schedule", {}):
            config["schedule"]["distribution_weights"] = existing["schedule"][
                "distribution_weights"
            ]

        # Save configuration
        self.save_config(config)

        print("\n" + "=" * 60)
        print("[OK] SETUP COMPLETE!")
        print("=" * 60)
        print(f"\nConfiguration saved to: {self.config_path}")
        print("You can edit this file manually if needed.\n")

        return config

    def _select_role(self) -> str:
        """Helper to select user role."""
        print("\nSelect your role:")
        print("  1. Developer (works with Jira tickets in development)")
        print("  2. QA (works with Jira tickets in testing)")
        print("  3. Product Owner")
        print("  4. Sales Team")

        while True:
            choice = input("Enter choice (1-4): ").strip()
            if choice == "1":
                return "developer"
            elif choice == "2":
                return "qa"
            elif choice == "3":
                return "product_owner"
            elif choice == "4":
                return "sales"
            else:
                print("Invalid choice. Please enter 1, 2, 3, or 4.")

    def _select_location(self) -> tuple[str, str]:
        """Helper to select country and city/state for holiday detection."""
        print("\nSelect your office location:")
        print("  1. US (United States)")
        print("  2. India - Pune (Maharashtra)")
        print("  3. India - Hyderabad (Telangana)")
        print("  4. India - Gandhinagar (Gujarat)")
        print("  5. Other (enter country code manually)")

        while True:
            choice = input("Enter choice (1-5): ").strip()
            if choice == "1":
                return "US", ""
            elif choice == "2":
                return "IN", "MH"
            elif choice == "3":
                return "IN", "TG"
            elif choice == "4":
                return "IN", "GJ"
            elif choice == "5":
                cc = input("Enter ISO country code (e.g., US, IN, GB): ").strip().upper()
                st = input("Enter state/province code (or Enter to skip): ").strip().upper()
                return cc, st
            else:
                print("Invalid choice. Please enter 1-5.")

    def save_config(self, config: dict):
        """Save configuration to file and persistent backup location."""
        try:
            with open(self.config_path, "w") as f:
                json.dump(config, f, indent=2)
            if sys.platform != "win32":
                os.chmod(self.config_path, 0o600)
            logger.info("Configuration saved successfully")
        except Exception as e:
            logger.error(f"Error saving configuration: {e}")
            raise

        # Also write to persistent backup so re-installs can detect prior setup
        if CONFIG_BACKUP_FILE:
            try:
                CONFIG_BACKUP_FILE.parent.mkdir(parents=True, exist_ok=True)
                with open(CONFIG_BACKUP_FILE, "w") as f:
                    json.dump(config, f, indent=2)
                if sys.platform != "win32":
                    os.chmod(CONFIG_BACKUP_FILE, 0o600)
                logger.info(f"Config backup saved to {CONFIG_BACKUP_FILE}")
            except Exception as e:
                logger.warning(f"Could not save config backup: {e}")

    def get_account_id(self) -> str:
        """Get account ID for current user via Jira /myself endpoint.

        The Tempo API does not expose a /user endpoint; the canonical
        source for accountId is Jira's REST API.
        """
        jira_url = self.config.get("jira", {}).get("url", "lmsportal.atlassian.net")
        jira_email = self.config.get("jira", {}).get("email", "")
        jira_token = self.config.get("jira", {}).get("api_token", "")

        try:
            import base64

            url = f"https://{jira_url}/rest/api/3/myself"
            creds = base64.b64encode(f"{jira_email}:{jira_token}".encode()).decode()
            headers = {
                "Authorization": f"Basic {creds}",
                "Content-Type": "application/json",
            }

            response = requests.get(url, headers=headers, timeout=30)
            logger.info(f"API call to {url}: {response.status_code}")
            response.raise_for_status()

            user_data = response.json()
            account_id = user_data.get("accountId", "")

            if account_id:
                logger.info(f"Retrieved account ID from Jira: {account_id}")
                return account_id
            else:
                logger.warning("accountId not found in Jira response, using email")
                return self.config.get("user", {}).get("email", "")

        except Exception as e:
            logger.error(f"Error fetching account ID from Jira: {e}")
            logger.info("Falling back to email as account ID")
            return self.config.get("user", {}).get("email", "")


# ============================================================================
# SCHEDULE MANAGER
# ============================================================================

ORG_HOLIDAYS_FILE = SCRIPT_DIR / "org_holidays.json"
ORG_HOLIDAYS_CACHE_FILE = SCRIPT_DIR / "org_holidays_cache.json"


class ScheduleManager:
    """Manages working days, holidays, PTO, and schedule overrides."""

    def __init__(self, config: dict, config_path: Path = None):
        self.config = config
        self.config_path = config_path or CONFIG_FILE
        self.schedule = config.get("schedule", {})
        self.daily_hours = self.schedule.get("daily_hours", 8)
        self.country_code = self.schedule.get("country_code", "US")
        self.state = self.schedule.get("state", "")
        self.pto_days = set(self.schedule.get("pto_days", []))
        self.extra_holidays = set(self.schedule.get("extra_holidays", []))
        self.working_days = set(self.schedule.get("working_days", []))
        self._org_holidays_data = {}
        self._org_holidays = {}  # flat {date_str: name}
        self._country_holidays = None
        self._load_org_holidays()
        self._load_country_holidays()

    # ------------------------------------------------------------------
    # Holiday loading
    # ------------------------------------------------------------------

    def _load_org_holidays(self):
        """Load org holidays from URL (primary) with local file as fallback."""
        # Try fetching from URL first (source of truth)
        remote_data = self._fetch_remote_org_holidays()

        if remote_data:
            self._org_holidays_data = remote_data
        elif ORG_HOLIDAYS_FILE.exists():
            # Fallback to local file when URL is unavailable
            try:
                with open(ORG_HOLIDAYS_FILE) as f:
                    self._org_holidays_data = json.load(f)
                logger.info("Org holidays loaded from local file (fallback)")
            except Exception as e:
                logger.warning(f"Could not load org_holidays.json: {e}")
                self._org_holidays_data = {}
        else:
            logger.info("No org_holidays.json found, skipping org holidays")
            self._org_holidays_data = {}

        self._parse_org_holidays()

    def _parse_org_holidays(self):
        """Parse org holidays data into flat {date_str: name} dict."""
        self._org_holidays = {}
        holidays_data = self._org_holidays_data.get("holidays", {})
        country_data = holidays_data.get(self.country_code, {})

        # Load current year and next year (for year-end boundary)
        today = date.today()
        for year in [str(today.year), str(today.year + 1)]:
            year_data = country_data.get(year, {})

            # Common holidays (apply to all in this country)
            common = year_data.get("common", [])
            for h in common:
                self._org_holidays[h["date"]] = h["name"]

            # State-specific holidays
            if self.state:
                state_holidays = year_data.get(self.state, [])
                for h in state_holidays:
                    self._org_holidays[h["date"]] = h["name"]

        count = len(self._org_holidays)
        logger.info(f"Parsed {count} org holidays for {self.country_code}/{self.state or 'all'}")

    def _fetch_remote_org_holidays(self) -> dict | None:
        """Fetch org_holidays.json from central URL (source of truth).

        Uses ETag/Last-Modified caching with a 24-hour TTL to avoid
        unnecessary network requests. On 304 Not Modified, reuses
        cached data. Always saves a local copy as offline backup.

        Returns:
            Remote data dict if fetch succeeded, None otherwise.
        """
        holidays_url = self.config.get("organization", {}).get("holidays_url", "")
        if not holidays_url:
            return None

        # Load cache metadata (ETag, Last-Modified, timestamp)
        cache_meta = self._load_holiday_cache_meta()
        cache_ttl_hours = 24

        # Skip fetch if cache is less than TTL hours old
        import time as _time_mod

        last_fetch = cache_meta.get("last_fetch_ts", 0)
        now_ts = _time_mod.time()
        if last_fetch and (now_ts - last_fetch) < cache_ttl_hours * 3600:
            # Cache is fresh -- use local file if it exists
            if ORG_HOLIDAYS_FILE.exists():
                try:
                    with open(ORG_HOLIDAYS_FILE) as f:
                        cached_data = json.load(f)
                    logger.info("Org holidays loaded from cache (TTL not expired)")
                    return cached_data
                except Exception:
                    pass  # Fall through to network fetch

        try:
            # Build conditional request headers
            headers = {}
            etag = cache_meta.get("etag", "")
            last_modified = cache_meta.get("last_modified", "")
            if etag:
                headers["If-None-Match"] = etag
            if last_modified:
                headers["If-Modified-Since"] = last_modified

            response = requests.get(holidays_url, headers=headers, timeout=10)

            if response.status_code == 304:
                # Not Modified -- use cached data
                self._save_holiday_cache_meta(etag, last_modified, now_ts)
                if ORG_HOLIDAYS_FILE.exists():
                    with open(ORG_HOLIDAYS_FILE) as f:
                        cached_data = json.load(f)
                    logger.info("Org holidays unchanged (304 Not Modified)")
                    return cached_data
                return None

            response.raise_for_status()
            remote_data = response.json()
            remote_version = remote_data.get("version", "")

            # Save remote data to local file as backup
            with open(ORG_HOLIDAYS_FILE, "w") as f:
                json.dump(remote_data, f, indent=2)

            # Update cache metadata
            new_etag = response.headers.get("ETag", "")
            new_last_modified = response.headers.get("Last-Modified", "")
            self._save_holiday_cache_meta(new_etag, new_last_modified, now_ts)

            logger.info(f"Org holidays fetched from URL (version: {remote_version})")
            return remote_data
        except Exception as e:
            logger.warning(f"Could not fetch remote org holidays: {e}")
            return None

    def _load_holiday_cache_meta(self) -> dict:
        """Load holiday cache metadata (ETag, Last-Modified, timestamp).

        Returns:
            Dict with etag, last_modified, and last_fetch_ts keys.
        """
        if not ORG_HOLIDAYS_CACHE_FILE.exists():
            return {}
        try:
            with open(ORG_HOLIDAYS_CACHE_FILE) as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_holiday_cache_meta(self, etag: str, last_modified: str, fetch_ts: float):
        """Save holiday cache metadata for conditional requests.

        Args:
            etag: ETag header value from server response.
            last_modified: Last-Modified header value from server.
            fetch_ts: Unix timestamp of the fetch.
        """
        meta = {"etag": etag, "last_modified": last_modified, "last_fetch_ts": fetch_ts}
        try:
            with open(ORG_HOLIDAYS_CACHE_FILE, "w") as f:
                json.dump(meta, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save holiday cache metadata: {e}")

    def _load_country_holidays(self):
        """Load country holidays from holidays library."""
        try:
            import holidays as holidays_lib

            state_arg = self.state if self.state else None
            self._country_holidays = holidays_lib.country_holidays(
                self.country_code, state=state_arg
            )
            logger.info(f"Country holidays loaded: {self.country_code}/{state_arg or 'national'}")
        except ImportError:
            logger.warning("holidays library not installed. Install with: pip install holidays")
            self._country_holidays = None
        except Exception as e:
            logger.warning(f"Could not load country holidays: {e}")
            self._country_holidays = None

    # ------------------------------------------------------------------
    # Day classification
    # ------------------------------------------------------------------

    def is_working_day(self, target_date: str) -> tuple[bool, str]:
        """
        Determine if a date is a working day.

        Priority order:
        1. working_days override -> WORK
        2. pto_days -> SKIP
        3. weekend -> SKIP
        4. org_holidays -> SKIP
        5. country holidays (library) -> SKIP
        6. extra_holidays -> SKIP
        7. default -> WORK

        Returns:
            (is_working, reason) tuple
        """
        dt = datetime.strptime(target_date, "%Y-%m-%d").date()

        # 1. Compensatory working day overrides everything
        if target_date in self.working_days:
            return True, "Compensatory working day"

        # 2. PTO
        if target_date in self.pto_days:
            return False, "PTO"

        # 3. Weekend
        if dt.weekday() >= 5:
            day_name = "Saturday" if dt.weekday() == 5 else "Sunday"
            return False, f"Weekend ({day_name})"

        # 4. Org holidays
        if target_date in self._org_holidays:
            return False, f"Holiday: {self._org_holidays[target_date]}"

        # 5. Country holidays (library)
        if self._country_holidays is not None and dt in self._country_holidays:
            return False, f"Holiday: {self._country_holidays.get(dt)}"

        # 6. Extra holidays (user-defined)
        if target_date in self.extra_holidays:
            return False, "Extra holiday"

        # 7. Default -- working day
        return True, "Working day"

    def get_holiday_name(self, target_date: str) -> str | None:
        """Get the holiday name for a date, or None if not a holiday."""
        if target_date in self._org_holidays:
            return self._org_holidays[target_date]
        dt = datetime.strptime(target_date, "%Y-%m-%d").date()
        if self._country_holidays is not None and dt in self._country_holidays:
            return self._country_holidays.get(dt)
        return None

    def count_working_days(self, start_date: str, end_date: str) -> int:
        """Count working days in a date range (inclusive)."""
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
        count = 0
        current = start
        while current <= end:
            is_working, _ = self.is_working_day(current.strftime("%Y-%m-%d"))
            if is_working:
                count += 1
            current += timedelta(days=1)
        return count

    def get_expected_hours(self, start_date: str, end_date: str) -> float:
        """Calculate expected hours for a date range."""
        return self.count_working_days(start_date, end_date) * self.daily_hours

    def check_year_end_warning(self) -> str | None:
        """Warn if next year's holiday data is missing in December."""
        today = date.today()
        if today.month != 12:
            return None
        next_year = str(today.year + 1)
        holidays_data = self._org_holidays_data.get("holidays", {})
        country_data = holidays_data.get(self.country_code, {})
        if next_year not in country_data:
            msg = (
                f"[!] WARNING: org_holidays.json does not contain "
                f"{next_year} holidays for {self.country_code}.\n"
                f"    Please ask your admin to update the central "
                f"holiday file on GitHub."
            )
            print(msg)
            logger.warning(msg)
            return msg
        if self.state:
            next_year_data = country_data.get(next_year, {})
            if self.state not in next_year_data:
                msg = (
                    f"[!] WARNING: org_holidays.json has {next_year} "
                    f"common holidays but no state holidays for "
                    f"{self.state}.\n"
                    f"    Please ask your admin to add {self.state} "
                    f"holidays for {next_year}."
                )
                print(msg)
                logger.warning(msg)
                return msg
        return None

    # ------------------------------------------------------------------
    # Calendar display
    # ------------------------------------------------------------------

    def get_month_calendar(self, year: int, month: int) -> list[dict]:
        """Generate calendar data for a month."""
        _, num_days = calendar.monthrange(year, month)
        days = []
        for day in range(1, num_days + 1):
            dt = date(year, month, day)
            date_str = dt.strftime("%Y-%m-%d")
            is_working, reason = self.is_working_day(date_str)
            # Determine display status
            if date_str in self.working_days:
                status = "comp_working"
                label = "CW"
            elif date_str in self.pto_days:
                status = "pto"
                label = "PTO"
            elif dt.weekday() >= 5:
                status = "weekend"
                label = "."
            elif not is_working:
                status = "holiday"
                label = "H"
            else:
                status = "working"
                label = "W"
            days.append(
                {
                    "date": date_str,
                    "day": day,
                    "weekday": dt.weekday(),
                    "day_name": dt.strftime("%A"),
                    "status": status,
                    "label": label,
                    "reason": reason,
                }
            )
        return days

    def print_month_calendar(self, month_str: str = "current"):
        """Print month calendar with day classifications."""
        if month_str == "current":
            today = date.today()
            year, month = today.year, today.month
        else:
            try:
                parts = month_str.split("-")
                year, month = int(parts[0]), int(parts[1])
                if not (1 <= month <= 12):
                    raise ValueError(f"Month must be 1-12, got {month}")
            except (ValueError, IndexError):
                print(f"[ERROR] Invalid month format: {month_str}")
                print("        Use YYYY-MM (e.g., 2026-03)")
                return

        days = self.get_month_calendar(year, month)
        month_name = calendar.month_name[month]

        print(f"\n{month_name} {year}")
        print("=" * 48)
        print("Mon  Tue  Wed  Thu  Fri  | Sat  Sun")

        # Pad first week
        first_weekday = days[0]["weekday"]
        line_dates = "     " * first_weekday
        line_labels = "     " * first_weekday

        holidays_list = []
        pto_list = []
        cw_list = []
        working_count = 0

        for day_info in days:
            wd = day_info["weekday"]
            day_num = day_info["day"]
            label = day_info["label"]

            # Separator before Sat column
            if wd == 5:
                line_dates += "| "
                line_labels += "| "

            line_dates += f"{day_num:>2}   "
            line_labels += f"{label:>2}   "

            # Track stats
            if day_info["status"] == "working":
                working_count += 1
            elif day_info["status"] == "comp_working":
                working_count += 1
                cw_list.append(day_info)
            elif day_info["status"] == "holiday":
                holidays_list.append(day_info)
            elif day_info["status"] == "pto":
                pto_list.append(day_info)

            # End of week (Sunday) or last day
            if wd == 6 or day_info == days[-1]:
                print(line_dates.rstrip())
                print(line_labels.rstrip())
                line_dates = ""
                line_labels = ""

        print()
        print("Legend: W=Working  H=Holiday  PTO=PTO  CW=Comp. Working  .=Weekend")
        print()
        expected = working_count * self.daily_hours
        print("Summary:")
        print(f"  Working days: {working_count}  |  Expected hours: {expected:.1f}h")
        if holidays_list:
            names = [
                f"{h['reason'].replace('Holiday: ', '')} - {month_name[:3]} {h['day']}"
                for h in holidays_list
            ]
            print(f"  Holidays: {len(holidays_list)} ({', '.join(names)})")
        if pto_list:
            pto_dates = [str(p["day"]) for p in pto_list]
            print(f"  PTO days: {len(pto_list)} ({month_name[:3]} {', '.join(pto_dates)})")
        if cw_list:
            cw_dates = [str(c["day"]) for c in cw_list]
            print(f"  Comp. working days: {len(cw_list)} ({month_name[:3]} {', '.join(cw_dates)})")
        print()

    # ------------------------------------------------------------------
    # Config modification (PTO, holidays, working days)
    # ------------------------------------------------------------------

    def add_pto(self, dates: list[str]) -> tuple[list[str], list[str]]:
        """
        Add PTO dates to config.
        Returns: (added_dates, skipped_messages) tuple.
        """
        added = []
        skipped = []
        for d in dates:
            d = d.strip()
            if not self._validate_date(d):
                skipped.append(f"{d}: invalid format")
                continue
            dt = datetime.strptime(d, "%Y-%m-%d").date()
            if dt.weekday() >= 5:
                day_name = dt.strftime("%A")
                msg = f"{d} is a {day_name} (weekend) -- PTO not needed"
                print(f"  [SKIP] {msg}")
                skipped.append(msg)
                continue
            if d not in self.pto_days:
                self.pto_days.add(d)
                print(f"  [OK] {d} ({dt.strftime('%A')})")
                added.append(d)
            else:
                msg = f"{d} already in PTO list"
                print(f"  [SKIP] {msg}")
                skipped.append(msg)
        if added:
            self._save_schedule_to_config()
            print(f"\n[OK] Added {len(added)} PTO day(s). Config saved.")
        return added, skipped

    def remove_pto(self, dates: list[str]) -> list[str]:
        """Remove PTO dates from config."""
        removed = []
        for d in dates:
            d = d.strip()
            if d in self.pto_days:
                self.pto_days.discard(d)
                print(f"  [OK] Removed {d}")
                removed.append(d)
            else:
                print(f"  [SKIP] {d} not in PTO list")
        if removed:
            self._save_schedule_to_config()
            print(f"\n[OK] Removed {len(removed)} PTO day(s). Config saved.")
        return removed

    def add_extra_holidays(self, dates: list[str]) -> list[str]:
        """Add extra holiday dates to config."""
        added = []
        for d in dates:
            d = d.strip()
            if not self._validate_date(d):
                continue
            if d not in self.extra_holidays:
                self.extra_holidays.add(d)
                dt = datetime.strptime(d, "%Y-%m-%d").date()
                print(f"  [OK] {d} ({dt.strftime('%A')})")
                added.append(d)
            else:
                print(f"  [SKIP] {d} already in extra holidays")
        if added:
            self._save_schedule_to_config()
            print(f"\n[OK] Added {len(added)} extra holiday(s). Config saved.")
        return added

    def remove_extra_holidays(self, dates: list[str]) -> list[str]:
        """Remove extra holiday dates from config."""
        removed = []
        for d in dates:
            d = d.strip()
            if d in self.extra_holidays:
                self.extra_holidays.discard(d)
                print(f"  [OK] Removed {d}")
                removed.append(d)
            else:
                print(f"  [SKIP] {d} not in extra holidays")
        if removed:
            self._save_schedule_to_config()
            print(f"\n[OK] Removed {len(removed)} extra holiday(s). Config saved.")
        return removed

    def add_working_days(self, dates: list[str]) -> list[str]:
        """Add compensatory working day dates to config."""
        added = []
        for d in dates:
            d = d.strip()
            if not self._validate_date(d):
                continue
            if d not in self.working_days:
                self.working_days.add(d)
                dt = datetime.strptime(d, "%Y-%m-%d").date()
                print(f"  [OK] {d} ({dt.strftime('%A')})")
                added.append(d)
            else:
                print(f"  [SKIP] {d} already in working days")
        if added:
            self._save_schedule_to_config()
            print(f"\n[OK] Added {len(added)} working day(s). Config saved.")
        return added

    def remove_working_days(self, dates: list[str]) -> list[str]:
        """Remove compensatory working day dates from config."""
        removed = []
        for d in dates:
            d = d.strip()
            if d in self.working_days:
                self.working_days.discard(d)
                print(f"  [OK] Removed {d}")
                removed.append(d)
            else:
                print(f"  [SKIP] {d} not in working days")
        if removed:
            self._save_schedule_to_config()
            print(f"\n[OK] Removed {len(removed)} working day(s). Config saved.")
        return removed

    def _save_schedule_to_config(self):
        """Persist schedule changes back to config.json."""
        self.config.setdefault("schedule", {})
        self.config["schedule"]["pto_days"] = sorted(self.pto_days)
        self.config["schedule"]["extra_holidays"] = sorted(self.extra_holidays)
        self.config["schedule"]["working_days"] = sorted(self.working_days)
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2)
            if sys.platform != "win32":
                os.chmod(self.config_path, 0o600)
            logger.info("Schedule config saved")
        except Exception as e:
            logger.error(f"Error saving schedule config: {e}")

    def _validate_date(self, date_str: str) -> bool:
        """Validate date string format and allowed characters."""
        import re

        if not re.match(r"^[\d\-]+$", date_str):
            print(f"  [ERROR] Invalid characters in: {date_str} (only digits and '-' allowed)")
            return False
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
            return True
        except ValueError:
            print(f"  [ERROR] Invalid date: {date_str} (use YYYY-MM-DD format)")
            return False

    # ------------------------------------------------------------------
    # Interactive menu
    # ------------------------------------------------------------------

    def interactive_menu(self):
        """Interactive schedule management menu."""
        while True:
            print("\nSchedule Management")
            print("=" * 40)
            print("1.  Add PTO day(s)")
            print("2.  Remove PTO day(s)")
            print("3.  Add extra holiday(s)")
            print("4.  Remove extra holiday(s)")
            print("5.  Add compensatory working day(s)")
            print("6.  Remove compensatory working day(s)")
            print("7.  View month schedule")
            print("8.  List all PTO days")
            print("9.  List all extra holidays")
            print("10. List all compensatory working days")
            print("0.  Exit")
            print()

            choice = input("Choice: ").strip()

            if choice == "0":
                break
            elif choice == "1":
                raw = input("Enter date(s) (YYYY-MM-DD, comma-separated): ").strip()
                dates = [d.strip() for d in raw.split(",") if d.strip()]
                self.add_pto(dates)
            elif choice == "2":
                raw = input("Enter date(s) to remove (YYYY-MM-DD, comma-separated): ").strip()
                dates = [d.strip() for d in raw.split(",") if d.strip()]
                self.remove_pto(dates)
            elif choice == "3":
                raw = input("Enter date(s) (YYYY-MM-DD, comma-separated): ").strip()
                dates = [d.strip() for d in raw.split(",") if d.strip()]
                self.add_extra_holidays(dates)
            elif choice == "4":
                raw = input("Enter date(s) to remove (YYYY-MM-DD, comma-separated): ").strip()
                dates = [d.strip() for d in raw.split(",") if d.strip()]
                self.remove_extra_holidays(dates)
            elif choice == "5":
                raw = input("Enter date(s) (YYYY-MM-DD, comma-separated): ").strip()
                dates = [d.strip() for d in raw.split(",") if d.strip()]
                self.add_working_days(dates)
            elif choice == "6":
                raw = input("Enter date(s) to remove (YYYY-MM-DD, comma-separated): ").strip()
                dates = [d.strip() for d in raw.split(",") if d.strip()]
                self.remove_working_days(dates)
            elif choice == "7":
                raw = input("Month (YYYY-MM, or Enter for current): ").strip()
                self.print_month_calendar(raw or "current")
            elif choice == "8":
                self._list_dates("PTO days", self.pto_days)
            elif choice == "9":
                self._list_dates("Extra holidays", self.extra_holidays)
            elif choice == "10":
                self._list_dates("Compensatory working days", self.working_days)
            else:
                print("[ERROR] Invalid choice. Enter 0-10.")

    def _list_dates(self, label: str, date_set: set):
        """Print a sorted list of dates."""
        if not date_set:
            print(f"\n{label}: (none)")
            return
        print(f"\n{label}:")
        for d in sorted(date_set):
            dt = datetime.strptime(d, "%Y-%m-%d").date()
            print(f"  {d} ({dt.strftime('%A')})")
        print(f"Total: {len(date_set)}")

    # ------------------------------------------------------------------
    # Locations (for setup wizard)
    # ------------------------------------------------------------------

    def get_locations(self) -> dict:
        """Return locations map from org_holidays.json."""
        return self._org_holidays_data.get("locations", {})


# ============================================================================
# JIRA API CLIENT
# ============================================================================

# Status sets used for active-issue JQL queries, keyed by role
DEVELOPER_STATUSES = ["IN DEVELOPMENT", "CODE REVIEW"]
QA_STATUSES = ["Testing", "User Acceptance Testing"]


class JiraClient:
    """Handles Jira API interactions."""

    def __init__(self, config: dict):
        self.config = config
        jira_cfg = config.get("jira", {})
        self.base_url = f"https://{jira_cfg.get('url', 'lmsportal.atlassian.net')}"
        self.email = jira_cfg.get("email", "")
        self.api_token = jira_cfg.get("api_token", "")
        self.session = requests.Session()
        self.session.auth = (self.email, self.api_token)
        self.session.headers.update({"Content-Type": "application/json"})
        # Retry on 429 (rate limit) and 5xx errors with exponential backoff
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry

        retry = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 502, 503, 504],
            respect_retry_after_header=True,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.account_id = self.get_myself_account_id()

    def get_myself_account_id(self) -> str:
        """Get current user's Atlassian account ID via Jira API."""
        try:
            url = f"{self.base_url}/rest/api/3/myself"
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            acct = response.json().get("accountId", "")
            if acct:
                logger.info(f"Jira account ID: {acct}")
                return acct
        except Exception as e:
            logger.warning(f"Could not get Jira account ID: {e}")
        return ""

    def get_my_worklogs(self, date_from: str, date_to: str) -> list[dict]:
        """
        Fetch worklogs for current user in date range.

        Args:
            date_from: Start date (YYYY-MM-DD)
            date_to: End date (YYYY-MM-DD)

        Returns:
            List of worklog dictionaries
        """
        try:
            # Get all issues with worklogs by current user
            jql = f'worklogAuthor = currentUser() AND worklogDate >= "{date_from}" AND worklogDate <= "{date_to}"'

            url = f"{self.base_url}/rest/api/3/search/jql"
            params = {"jql": jql, "fields": "worklog,summary,key", "maxResults": 100}

            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()

            issues = response.json().get("issues", [])

            worklogs = []
            for issue in issues:
                issue_key = issue["key"]
                issue_summary = issue["fields"]["summary"]

                # Get worklogs for this issue (with pagination)
                worklog_url = f"{self.base_url}/rest/api/3/issue/{issue_key}/worklog"
                issue_worklogs = []
                start_at = 0
                while True:
                    worklog_response = self.session.get(
                        worklog_url, params={"startAt": start_at}, timeout=30
                    )
                    worklog_response.raise_for_status()
                    wl_data = worklog_response.json()
                    issue_worklogs.extend(wl_data.get("worklogs", []))
                    total = wl_data.get("total", 0)
                    start_at += wl_data.get("maxResults", len(issue_worklogs))
                    if start_at >= total:
                        break

                # Filter worklogs by date and current user
                for wl in issue_worklogs:
                    started = wl["started"][:10]  # Extract date part
                    if date_from <= started <= date_to:
                        author = wl.get("author", {})
                        author_email = author.get("emailAddress", "")
                        author_id = author.get("accountId", "")
                        if author_email == self.email or author_id == self.account_id:
                            worklogs.append(
                                {
                                    "worklog_id": wl["id"],
                                    "issue_key": issue_key,
                                    "issue_summary": issue_summary,
                                    "time_spent_seconds": wl["timeSpentSeconds"],
                                    "started": started,
                                    "comment": wl.get("comment", ""),
                                }
                            )

            logger.info(f"Fetched {len(worklogs)} worklogs from Jira")
            return worklogs

        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching Jira worklogs: {e}")
            return []

    def delete_worklog(self, issue_key: str, worklog_id: str) -> bool:
        """
        Delete a worklog from a Jira issue.

        Args:
            issue_key: Jira issue key (e.g., "TS-1234")
            worklog_id: ID of the worklog to delete

        Returns:
            True if successful, False otherwise
        """
        try:
            url = f"{self.base_url}/rest/api/3/issue/{issue_key}/worklog/{worklog_id}"
            response = self.session.delete(url, timeout=30)
            response.raise_for_status()

            logger.info(f"Deleted worklog {worklog_id} from {issue_key}")
            return True

        except requests.exceptions.RequestException as e:
            logger.error(f"Error deleting worklog {worklog_id} from {issue_key}: {e}")
            return False

    def get_my_active_issues(self, statuses: list[str] = None) -> list[dict]:
        """
        Fetch issues assigned to current user that are actively being worked.

        Args:
            statuses: List of Jira status names to filter by.
                      Defaults to DEVELOPER_STATUSES if not provided.

        Returns:
            List of dicts with issue_key and issue_summary
        """
        if statuses is None:
            statuses = DEVELOPER_STATUSES
        try:
            quoted = ", ".join(f'"{s}"' for s in statuses)
            jql = f"assignee = currentUser() AND status IN ({quoted})"

            url = f"{self.base_url}/rest/api/3/search/jql"
            params = {"jql": jql, "fields": "summary", "maxResults": 50}

            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()

            issues = response.json().get("issues", [])

            result = []
            for issue in issues:
                result.append(
                    {"issue_key": issue["key"], "issue_summary": issue["fields"]["summary"]}
                )

            logger.info(f"Found {len(result)} active issues assigned to current user")
            return result

        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching active issues: {e}")
            return []

    def get_issues_in_status_on_date(
        self, target_date: str, statuses: list[str] = None
    ) -> list[dict]:
        """
        Fetch issues that were in the given statuses on a past date.

        Uses historical JQL (status WAS ... ON ...) to find tickets that
        were active on a specific date, even if their status has since changed.

        Args:
            target_date: Date string (YYYY-MM-DD)
            statuses: List of Jira status names to filter by.
                      Defaults to DEVELOPER_STATUSES if not provided.

        Returns:
            List of dicts with issue_key and issue_summary
        """
        if statuses is None:
            statuses = DEVELOPER_STATUSES
        try:
            was_clauses = " OR ".join(f'status WAS "{s}" ON "{target_date}"' for s in statuses)
            jql = f"assignee = currentUser() AND ({was_clauses})"

            url = f"{self.base_url}/rest/api/3/search/jql"
            params = {"jql": jql, "fields": "summary", "maxResults": 50}

            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()

            issues = response.json().get("issues", [])

            result = []
            for issue in issues:
                result.append(
                    {"issue_key": issue["key"], "issue_summary": issue["fields"]["summary"]}
                )

            logger.info(f"Found {len(result)} issues in status on {target_date}")
            return result

        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching historical issues for {target_date}: {e}")
            return []

    def get_issue_details(self, issue_key: str) -> dict | None:
        """
        Fetch issue description and recent comments.

        Args:
            issue_key: Jira issue key (e.g., "TS-1234")

        Returns:
            Dict with summary, description_text, and recent_comments, or None on failure
        """
        try:
            url = f"{self.base_url}/rest/api/3/issue/{issue_key}"
            params = {"fields": "summary,description,comment"}

            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()

            data = response.json()
            fields = data.get("fields", {})

            # Extract plain text from ADF description
            description_text = self._extract_adf_text(fields.get("description"))

            # Extract recent comments (latest 3, most recent first)
            comment_body = fields.get("comment", {})
            raw_comments = comment_body.get("comments", [])
            recent_comments = []
            for c in raw_comments[-3:]:
                text = self._extract_adf_text(c.get("body"))
                if text:
                    recent_comments.append(text)

            return {
                "summary": fields.get("summary", ""),
                "description_text": description_text,
                "recent_comments": recent_comments,
            }

        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching issue details for {issue_key}: {e}")
            return None

    def get_overhead_stories(self) -> list[dict]:
        """
        Fetch active overhead stories from the OVERHEAD project.

        Queries Jira for stories in the OVERHEAD project with status
        "In Progress" and extracts PI identifiers from sprint names.

        Returns:
            List of dicts with issue_key, issue_summary, pi_identifier
        """
        try:
            jql = 'project = OVERHEAD AND status = "In Progress"'

            url = f"{self.base_url}/rest/api/3/search/jql"
            params = {"jql": jql, "fields": "summary,sprint", "maxResults": 50}

            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()

            issues = response.json().get("issues", [])
            pi_pattern = re.compile(r"PI\.(\d{2})\.(\d+)\.([A-Z]{3})\.(\d{1,2})")

            result = []
            for issue in issues:
                fields = issue.get("fields", {})

                # Extract sprint name -- may be dict or list
                sprint_name = ""
                sprint_data = fields.get("sprint")
                if sprint_data and isinstance(sprint_data, dict):
                    sprint_name = sprint_data.get("name", "")
                elif sprint_data and isinstance(sprint_data, list):
                    if sprint_data:
                        sprint_name = sprint_data[-1].get("name", "")

                # Extract PI identifier from sprint name or summary
                pi_match = pi_pattern.search(sprint_name)
                if not pi_match:
                    # Fallback: parse PI from issue summary
                    summary = fields.get("summary", "")
                    pi_match = pi_pattern.search(summary)
                pi_identifier = pi_match.group(0) if pi_match else ""

                result.append(
                    {
                        "issue_key": issue["key"],
                        "issue_summary": fields.get("summary", ""),
                        "pi_identifier": pi_identifier,
                    }
                )

            logger.info(f"Found {len(result)} overhead stories")
            return result

        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching overhead stories: {e}")
            return []

    @staticmethod
    def _extract_adf_text(adf_content) -> str:
        """
        Recursively extract plain text from Jira ADF (Atlassian Document Format).

        ADF is a nested JSON structure. This walks all nodes and collects text.
        """
        if not adf_content or not isinstance(adf_content, dict):
            return ""

        parts = []

        def _walk(node):
            if isinstance(node, dict):
                if node.get("type") == "text":
                    parts.append(node.get("text", ""))
                for child in node.get("content", []):
                    _walk(child)
            elif isinstance(node, list):
                for item in node:
                    _walk(item)

        _walk(adf_content)
        return " ".join(parts).strip()

    def create_worklog(
        self, issue_key: str, time_spent_seconds: int, started: str, comment: str = ""
    ):
        """
        Create a worklog on a Jira issue.

        Args:
            issue_key: Jira issue key (e.g., "TS-1234")
            time_spent_seconds: Time spent in seconds
            started: Date string (YYYY-MM-DD), will be formatted to ISO datetime
            comment: Optional comment text

        Returns:
            Worklog ID (str) if successful, None otherwise
        """
        try:
            url = f"{self.base_url}/rest/api/3/issue/{issue_key}/worklog"

            # Format started as ISO datetime (Jira v3 requires full datetime)
            started_dt = f"{started}T09:00:00.000+0000"

            payload = {
                "timeSpentSeconds": time_spent_seconds,
                "started": started_dt,
            }

            # Jira v3 API requires comment in ADF (Atlassian Document Format)
            # Each line becomes its own paragraph for multi-line descriptions
            if comment:
                paragraphs = []
                for line in comment.split("\n"):
                    line = line.strip()
                    if line:
                        paragraphs.append(
                            {"type": "paragraph", "content": [{"type": "text", "text": line}]}
                        )
                if paragraphs:
                    payload["comment"] = {"type": "doc", "version": 1, "content": paragraphs}

            response = self.session.post(url, json=payload, timeout=30)
            response.raise_for_status()

            worklog_id = str(response.json().get("id", ""))
            logger.info(
                f"Created Jira worklog {worklog_id}: "
                f"{issue_key} - {time_spent_seconds}s on {started}"
            )
            return worklog_id

        except requests.exceptions.RequestException as e:
            logger.error(f"Error creating Jira worklog for {issue_key}: {e}")
            return None


# ============================================================================
# TEMPO API CLIENT
# ============================================================================


class TempoClient:
    """Handles Tempo API interactions."""

    def __init__(self, config: dict, account_id: str = ""):
        self.config = config
        self.api_token = config.get("tempo", {}).get("api_token", "")
        self.base_url = "https://api.tempo.io/4"
        self.session = requests.Session()
        self.session.headers.update(
            {"Authorization": f"Bearer {self.api_token}", "Content-Type": "application/json"}
        )
        # Retry on 429 (rate limit) and 5xx errors with exponential backoff
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry

        retry = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 502, 503, 504],
            respect_retry_after_header=True,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.account_id = account_id or config.get("user", {}).get("email", "")

    @staticmethod
    def _forge_error_hint(exc: Exception) -> str:
        """Return a Forge-migration hint if the error looks related.

        Checks for HTTP 403, 404, 502, or connection errors that
        commonly occur when an instance transitions to Forge.
        """
        hint = ""
        status = getattr(getattr(exc, "response", None), "status_code", None)
        forge_statuses = {403, 404, 502}
        if status in forge_statuses:
            hint = (
                f" Tempo API returned {status}. If Tempo recently "
                "migrated to Forge, your API token may need to be "
                "regenerated. See: https://help.tempo.io/timesheets/"
                "latest/expected-changes-in-timesheets-on-forge"
            )
        elif isinstance(exc, requests.exceptions.ConnectionError | requests.exceptions.Timeout):
            hint = (
                " If Tempo recently migrated to Forge, check your "
                "network/firewall settings. See: https://help.tempo"
                ".io/timesheets/latest/expected-changes-in-"
                "timesheets-on-forge"
            )
        return hint

    def get_user_worklogs(self, date_from: str, date_to: str) -> list[dict]:
        """
        Fetch Tempo worklogs for current user.

        Args:
            date_from: Start date (YYYY-MM-DD)
            date_to: End date (YYYY-MM-DD)

        Returns:
            List of worklog dictionaries
        """
        try:
            url = f"{self.base_url}/worklogs/user/{self.account_id}"
            params = {"from": date_from, "to": date_to}

            all_worklogs = []
            while url:
                response = self.session.get(url, params=params, timeout=30)
                response.raise_for_status()

                data = response.json()
                all_worklogs.extend(data.get("results", []))

                # Follow pagination via metadata.next
                next_url = data.get("metadata", {}).get("next")
                if next_url:
                    url = next_url
                    params = {}  # next URL includes query params
                else:
                    break

            logger.info(f"Fetched {len(all_worklogs)} worklogs from Tempo")
            return all_worklogs

        except requests.exceptions.RequestException as e:
            hint = self._forge_error_hint(e)
            logger.error(f"Error fetching Tempo worklogs: {e}{hint}")
            return []

    def create_worklog(
        self, issue_key: str, time_seconds: int, start_date: str, description: str = ""
    ) -> bool:
        """
        Create a new Tempo worklog entry.

        Args:
            issue_key: Jira issue key (e.g., "TS-1234")
            time_seconds: Time spent in seconds
            start_date: Date (YYYY-MM-DD)
            description: Optional description

        Returns:
            True if successful, False otherwise
        """
        try:
            url = f"{self.base_url}/worklogs"

            data = {
                "issueKey": issue_key,
                "timeSpentSeconds": time_seconds,
                "startDate": start_date,
                "startTime": "09:00:00",
                "authorAccountId": self.account_id,
                "description": description,
            }

            response = self.session.post(url, json=data, timeout=30)
            response.raise_for_status()

            logger.info(f"Created Tempo worklog: {issue_key} - {time_seconds}s on {start_date}")
            return True

        except requests.exceptions.RequestException as e:
            hint = self._forge_error_hint(e)
            logger.error(f"Error creating Tempo worklog: {e}{hint}")
            return False

    def submit_timesheet(self, period_key: str = None) -> bool:
        """
        Submit timesheet for approval.

        Args:
            period_key: Timesheet period key (auto-detects if None)

        Returns:
            True if successful, False otherwise
        """
        try:
            # If no period specified, use current period
            if not period_key:
                period_key = self._get_current_period()

            url = f"{self.base_url}/timesheet-approvals/submit"

            data = {"worker": {"accountId": self.account_id}, "period": {"key": period_key}}

            response = self.session.post(url, json=data, timeout=30)
            response.raise_for_status()

            logger.info(f"Successfully submitted timesheet for period: {period_key}")
            return True

        except requests.exceptions.RequestException as e:
            hint = self._forge_error_hint(e)
            logger.error(f"Error submitting timesheet: {e}{hint}")
            return False

    def _get_current_period(self) -> str:
        """Get current timesheet period key."""
        try:
            url = f"{self.base_url}/periods"

            # Get current date to find matching period
            today = date.today().strftime("%Y-%m-%d")

            response = self.session.get(url, params={"from": today, "to": today}, timeout=30)
            response.raise_for_status()

            periods = response.json().get("results", [])

            # Find period containing today's date
            for period in periods:
                period_from = period.get("dateFrom")
                period_to = period.get("dateTo")

                if period_from and period_to:
                    if period_from <= today <= period_to:
                        period_key = period.get("key")
                        logger.info(f"Found current period: {period_key}")
                        return period_key

            # Fallback to simplified format
            logger.warning("No period found in API, using simplified format")
            today_obj = date.today()
            return f"{today_obj.year}-{today_obj.month:02d}"

        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching Tempo period: {e}")
            # Fallback to simplified format
            today_obj = date.today()
            return f"{today_obj.year}-{today_obj.month:02d}"

    def check_forge_status(self) -> dict:
        """Check if Tempo instance has migrated to Atlassian Forge.

        Calls GET /work-attributes and inspects response headers for
        Forge-specific indicators.  Returns a dict with platform
        detection results.

        Returns:
            Dict with keys: platform ('forge', 'connect', or 'unknown'),
            headers (dict of relevant response headers), latency_ms (int).
        """
        result = {
            "platform": "unknown",
            "headers": {},
            "latency_ms": 0,
            "healthy": False,
        }
        forge_indicators = [
            "x-forge-app",
            "x-forge-request-id",
            "x-atlassian-forge",
        ]
        try:
            url = f"{self.base_url}/work-attributes"
            import time as _time

            start = _time.monotonic()
            response = self.session.get(url, timeout=15)
            elapsed = int((_time.monotonic() - start) * 1000)
            result["latency_ms"] = elapsed
            response.raise_for_status()
            result["healthy"] = True

            # Capture interesting headers
            for hdr in list(response.headers.keys()):
                lower = hdr.lower()
                if any(ind in lower for ind in forge_indicators):
                    result["headers"][hdr] = response.headers[hdr]
                elif lower in ("server", "via", "x-request-id"):
                    result["headers"][hdr] = response.headers[hdr]

            # Detect platform
            if any(
                ind in " ".join(k.lower() for k in response.headers.keys())
                for ind in forge_indicators
            ):
                result["platform"] = "forge"
                logger.info("Tempo platform detected: Forge")
            else:
                result["platform"] = "connect"
                logger.info("Tempo platform detected: Connect (legacy)")

        except requests.exceptions.RequestException as e:
            logger.warning(f"Forge status check failed: {e}")

        return result

    def get_timesheet_periods(self, date_from: str, date_to: str) -> list[dict]:
        """Fetch Tempo timesheet approval periods for a date range.

        Args:
            date_from: Start date (YYYY-MM-DD).
            date_to: End date (YYYY-MM-DD).

        Returns:
            List of period dicts with status, dateFrom, dateTo keys.
        """
        try:
            url = f"{self.base_url}/timesheet-approvals/user/{self.account_id}"
            params = {"from": date_from, "to": date_to}

            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()

            data = response.json()
            # API may return a single object or a list
            if isinstance(data, dict):
                periods = data.get("results", [data])
            elif isinstance(data, list):
                periods = data
            else:
                periods = []

            logger.info(f"Fetched {len(periods)} timesheet period(s) for {date_from} to {date_to}")
            return periods

        except requests.exceptions.RequestException as e:
            hint = self._forge_error_hint(e)
            logger.error(f"Error fetching timesheet periods: {e}{hint}")
            return []


# ============================================================================
# NOTIFICATION MANAGER
# ============================================================================


class NotificationManager:
    """Handles email notifications."""

    def __init__(self, config: dict):
        self.config = config
        self.enabled = config.get("notifications", {}).get("email_enabled", False)

    def send_daily_summary(self, worklogs: list[dict], total_hours: float):
        """Send daily timesheet summary email."""
        if not self.enabled:
            return

        subject = f"Tempo Daily Summary - {date.today().strftime('%Y-%m-%d')}"

        body = f"""
        <html>
        <body>
        <h2>Daily Tempo Timesheet Summary</h2>
        <p><strong>Date:</strong> {date.today().strftime("%B %d, %Y")}</p>
        <p><strong>Total Hours Logged:</strong> {total_hours:.2f} / {self.config.get("schedule", {}).get("daily_hours", 8)}</p>

        <h3>Entries:</h3>
        <ul>
        """

        for wl in worklogs:
            hours = wl["time_spent_seconds"] / 3600
            body += f"<li>{html.escape(wl['issue_key'])}: {hours:.2f}h - {html.escape(wl.get('issue_summary', ''))}</li>\n"

        status = (
            "[OK] Complete"
            if total_hours >= self.config.get("schedule", {}).get("daily_hours", 8)
            else "[!] Incomplete"
        )
        body += f"""
        </ul>

        <p><strong>Status:</strong> {status}</p>

        <hr>
        <p style="color: gray; font-size: 0.9em;">
        This is an automated message from Tempo Automation Script.
        </p>
        </body>
        </html>
        """

        self._send_email(subject, body)

    def send_submission_confirmation(self, period: str):
        """Send timesheet submission confirmation."""
        if not self.enabled:
            return

        subject = f"Timesheet Submitted - {period}"

        body = f"""
        <html>
        <body>
        <h2>✓ Timesheet Submitted Successfully</h2>
        <p>Your timesheet for <strong>{period}</strong> has been automatically submitted for approval.</p>

        <p>No further action needed!</p>

        <hr>
        <p style="color: gray; font-size: 0.9em;">
        This is an automated message from Tempo Automation Script.
        </p>
        </body>
        </html>
        """

        self._send_email(subject, body)

    def _send_email(self, subject: str, body: str):
        """Send email via SMTP."""
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self.config["notifications"]["smtp_user"]
            msg["To"] = self.config["notifications"]["notification_email"]

            msg.attach(MIMEText(body, "html"))

            server = smtplib.SMTP(
                self.config["notifications"]["smtp_server"],
                self.config["notifications"]["smtp_port"],
            )
            try:
                server.starttls()
                smtp_password = CredentialManager.decrypt(
                    self.config["notifications"].get("smtp_password", ""), key="smtp_password"
                )
                server.login(self.config["notifications"]["smtp_user"], smtp_password)
                server.send_message(msg)
                logger.info(f"Email sent: {subject}")
            finally:
                server.quit()

        except Exception as e:
            logger.error(f"Error sending email: {e}")

    def send_teams_notification(self, title: str, body: str, facts: list[dict] = None):
        """
        Send notification to MS Teams via incoming webhook.

        Uses Adaptive Card format for rich display.
        Silently skips if webhook URL not configured.
        """
        webhook_url = self.config.get("notifications", {}).get("teams_webhook_url", "")
        if not webhook_url:
            logger.info("Teams webhook not configured, skipping")
            return

        try:
            card_body = [
                {"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium"},
                {"type": "TextBlock", "text": body, "wrap": True},
            ]

            if facts:
                fact_set = {
                    "type": "FactSet",
                    "facts": [{"title": f["title"], "value": f["value"]} for f in facts],
                }
                card_body.append(fact_set)

            payload = {
                "type": "message",
                "attachments": [
                    {
                        "contentType": "application/vnd.microsoft.card.adaptive",
                        "content": {
                            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                            "type": "AdaptiveCard",
                            "version": "1.4",
                            "body": card_body,
                        },
                    }
                ],
            }

            response = requests.post(webhook_url, json=payload, timeout=30)
            response.raise_for_status()
            logger.info(f"Teams notification sent: {title}")
            print("  [OK] Teams notification sent")

        except Exception as e:
            logger.error(f"Error sending Teams notification: {e}")
            print(f"  [!] Teams notification failed: {e}")

    def send_windows_notification(self, title: str, body: str):
        """Show a desktop notification (Windows toast or Mac osascript)."""
        if sys.platform == "win32":
            try:
                from winotify import Notification, audio

                toast = Notification(
                    app_id="Tempo Automation", title=title, msg=body, duration="long"
                )
                toast.set_audio(audio.Default, loop=False)
                toast.show()
                logger.info(f"Toast notification shown: {title}")
                print("  [OK] Desktop notification sent")
            except ImportError:
                # Fallback to MessageBox if winotify not installed
                try:
                    from ctypes import c_int, c_wchar_p, windll

                    windll.user32.MessageBoxW(
                        c_int(0), c_wchar_p(body), c_wchar_p(title), 0x00000030 | 0x00001000
                    )
                    logger.info(f"MessageBox notification shown: {title}")
                    print("  [OK] Desktop notification shown")
                except Exception as e2:
                    logger.warning(f"Notification failed: {e2}")
            except Exception as e:
                logger.warning(f"Toast notification failed: {e}")
        elif sys.platform == "darwin":
            try:
                import subprocess as sp

                safe_title = title.replace('"', '\\"')
                safe_body = body.replace('"', '\\"')
                script = f'display notification "{safe_body}" with title "{safe_title}"'
                sp.Popen(["osascript", "-e", script])
                logger.info(f"Mac notification shown: {title}")
                print("  [OK] Desktop notification sent")
            except Exception as e:
                logger.warning(f"Mac notification failed: {e}")

    def send_shortfall_email(self, title: str, body: str, facts: list[dict] = None):
        """Send shortfall notification via email."""
        if not self.enabled:
            return
        facts_html = ""
        if facts:
            rows = "".join(
                f"<tr><td><strong>{html.escape(str(f['title']))}</strong></td>"
                f"<td>{html.escape(str(f['value']))}</td></tr>"
                for f in facts
            )
            facts_html = f"<table border='1' cellpadding='6' cellspacing='0'>{rows}</table>"
        html_body = f"""
        <html><body>
        <h2>[!] {title}</h2>
        <p>{body.replace(chr(10), "<br>")}</p>
        {facts_html}
        <hr>
        <p style="color: gray; font-size: 0.9em;">
        Automated message from Tempo Automation Script.
        </p>
        </body></html>
        """
        self._send_email(title, html_body)


# ============================================================================
# AUTOMATION ENGINE
# ============================================================================


class TempoAutomation:
    """Main automation engine."""

    def __init__(self, config_path: Path = CONFIG_FILE, dry_run: bool = False):
        self.dry_run = dry_run
        self.config_manager = ConfigManager(config_path)
        self.config = self.config_manager.config
        self.schedule_mgr = ScheduleManager(self.config)

        self.jira_client = None
        if self.config.get("user", {}).get("role") in ("developer", "qa"):
            self.jira_client = JiraClient(self.config)

        account_id = self.jira_client.account_id if self.jira_client else ""
        self.tempo_client = TempoClient(self.config, account_id)
        self.notifier = NotificationManager(self.config)

        # Check for year-end holiday warnings
        self.schedule_mgr.check_year_end_warning()

        # Check overhead story configuration
        if self.config.get("user", {}).get("role") in ("developer", "qa"):
            if not self._is_overhead_configured():
                print("[INFO] Overhead stories not configured. Run --select-overhead when ready.")
            elif not self._check_overhead_pi_current():
                print(
                    "[!] Overhead stories may be from a previous PI. "
                    "Run --select-overhead to update."
                )

    def _sync_pto_overhead(self, target_date: str):
        """
        Log overhead hours for a PTO day (Case 3).

        Idempotent: skips if PTO hours already logged for the date.
        """
        daily_hours = self.config.get("schedule", {}).get("daily_hours", 8)
        total_seconds = int(daily_hours * 3600)

        logger.info(f"PTO day {target_date} -- logging overhead hours")
        now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n{'=' * 60}")
        print(f"TEMPO DAILY SYNC - {target_date} [PTO] (started {now_ts})")
        print(f"{'=' * 60}\n")

        if self.dry_run:
            print("[DRY RUN] Preview mode -- no changes will be made\n")

        print("[INFO] PTO day -- logging hours to overhead story")

        oh = self._get_overhead_config()
        pto_key = oh.get("pto_story_key", "")

        # Check existing hours -- Tempo is source of truth
        existing = []
        jira_seconds = 0
        if self.jira_client:
            existing = self.jira_client.get_my_worklogs(target_date, target_date)
            jira_seconds = sum(wl["time_spent_seconds"] for wl in existing)
        tempo_seconds = 0
        if self.tempo_client.account_id:
            tempo_worklogs = self.tempo_client.get_user_worklogs(target_date, target_date)
            tempo_seconds = sum(twl.get("timeSpentSeconds", 0) for twl in tempo_worklogs)
        existing_seconds = max(jira_seconds, tempo_seconds)

        if existing_seconds >= total_seconds:
            print(f"[OK] PTO hours already logged ({existing_seconds / 3600:.2f}h)")
            worklogs_created = [
                {
                    "issue_key": wl["issue_key"],
                    "issue_summary": wl.get("issue_summary", wl["issue_key"]),
                    "time_spent_seconds": wl["time_spent_seconds"],
                }
                for wl in existing
            ]
        else:
            # Log remaining PTO hours via Jira (syncs to Tempo)
            remaining = total_seconds - existing_seconds
            worklogs_created = self._log_overhead_hours(
                target_date,
                remaining,
                [{"issue_key": pto_key, "summary": pto_key}] if pto_key else None,
                "single" if pto_key else None,
            )

        total_hours = (
            sum(wl["time_spent_seconds"] for wl in worklogs_created) / 3600
            if worklogs_created
            else 0.0
        )

        self.notifier.send_daily_summary(worklogs_created, total_hours)
        done_ts = datetime.now().strftime("%H:%M:%S")
        print(f"\n{'=' * 60}")
        print(f"[OK] PTO SYNC COMPLETE ({done_ts})")
        print(f"{'=' * 60}")
        print(f"Total hours: {total_hours:.2f} / {daily_hours}")
        print()
        logger.info(f"PTO sync completed for {target_date}: {total_hours:.2f}h")

    def _forge_sync_wait(self):
        """Wait for Jira-to-Tempo sync if forge_sync_delay_seconds is set.

        During the Tempo Forge migration window, sync latency between
        Jira worklogs and their Tempo mirror may increase.  This delay
        is applied after Jira writes and before Tempo reads.
        """
        delay = self.config.get("tempo", {}).get("forge_sync_delay_seconds", 0)
        if delay and delay > 0:
            import time as _time

            logger.info(f"Forge sync delay: waiting {delay}s for Jira->Tempo sync")
            print(f"  [INFO] Waiting {delay}s for Jira->Tempo sync (Forge migration)...")
            _time.sleep(delay)

    def _pre_sync_health_check(self) -> bool:
        """Check Jira and Tempo API connectivity before mutating data.

        Returns:
            True if both APIs are reachable and authenticated,
            False with diagnostic print if either fails.
        """
        # Check Jira API
        if self.jira_client:
            try:
                url = f"{self.jira_client.base_url}/rest/api/3/myself"
                response = self.jira_client.session.get(url, timeout=10)
                logger.info(f"API call to {url}: {response.status_code}")
                response.raise_for_status()
            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else "unknown"
                if status == 401:
                    msg = "[FAIL] Jira token expired (401)"
                else:
                    msg = f"[FAIL] Jira API error ({status})"
                print(msg)
                logger.error(f"Health check failed: {msg}")
                return False
            except Exception as e:
                msg = f"[FAIL] Jira API unreachable: {e}"
                print(msg)
                logger.error(f"Health check failed: {msg}")
                return False

        # Check Tempo API using /work-attributes (lightweight, always available)
        if self.tempo_client.account_id or self.tempo_client.api_token:
            try:
                url = f"{self.tempo_client.base_url}/work-attributes"
                response = self.tempo_client.session.get(url, timeout=10)
                logger.info(f"API call to {url}: {response.status_code}")
                response.raise_for_status()
            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else "unknown"
                hint = TempoClient._forge_error_hint(e)
                if status == 401:
                    msg = "[FAIL] Tempo token expired (401)"
                else:
                    msg = f"[FAIL] Tempo API error ({status})"
                if hint:
                    msg += f"\n       {hint.strip()}"
                print(msg)
                logger.error(f"Health check failed: {msg}")
                return False
            except Exception as e:
                msg = f"[FAIL] Tempo API unreachable: {e}"
                hint = TempoClient._forge_error_hint(e)
                if hint:
                    msg += f"\n       {hint.strip()}"
                print(msg)
                logger.error(f"Health check failed: {msg}")
                return False

        # Check Forge platform connectivity (non-blocking warning)
        self._check_forge_connectivity()

        logger.info("Pre-sync health check passed")
        return True

    def _check_forge_connectivity(self):
        """Warn if Atlassian Forge infrastructure is unreachable.

        This is a non-blocking check -- a failure only logs a warning
        since the Tempo API proxy (api.tempo.io) may still work even
        if direct Forge domains are unreachable.
        """
        import socket

        forge_hosts = [
            ("api.tempo.io", 443),
            ("api.atlassian.com", 443),
        ]
        for host, port in forge_hosts:
            try:
                sock = socket.create_connection((host, port), timeout=5)
                sock.close()
            except (TimeoutError, OSError) as e:
                logger.warning(f"Forge connectivity warning: {host}:{port} unreachable ({e})")
                print(
                    f"  [!] Network warning: {host} unreachable. "
                    "If Tempo migrated to Forge, check firewall "
                    "settings."
                )

    def sync_daily(self, target_date: str = None):
        """
        Sync daily timesheet entries.

        Args:
            target_date: Date to sync (YYYY-MM-DD), defaults to today
        """
        if not target_date:
            target_date = date.today().strftime("%Y-%m-%d")

        # --- Schedule guard ---
        is_working, reason = self.schedule_mgr.is_working_day(target_date)
        if not is_working:
            # Case 3: PTO/Holiday -- log overhead instead of skipping
            is_off_day = reason != "Weekend"
            if is_off_day and self.config.get("user", {}).get("role") == "developer":
                if self._is_overhead_configured():
                    self._sync_pto_overhead(target_date)
                    return
                else:
                    print(f"\n[SKIP] {target_date} is {reason}.")
                    self._warn_overhead_not_configured()
                    logger.info(f"Skipped {reason} {target_date}: overhead not configured")
                    return
            print(f"\n[SKIP] {target_date} is not a working day: {reason}")
            print("       Use --add-workday to override if this day should be worked.")
            logger.info(f"Skipped {target_date}: {reason}")
            return
        # --- End guard ---

        logger.info(f"Starting daily sync for {target_date}")

        # Pre-sync health check
        if not self._pre_sync_health_check():
            print("[FAIL] Aborting daily sync due to API health check failure.")
            return

        now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n{'=' * 60}")
        print(f"TEMPO DAILY SYNC - {target_date} (started {now_ts})")
        print(f"{'=' * 60}\n")

        if self.dry_run:
            print("[DRY RUN] Preview mode -- no changes will be made\n")

        worklogs_created = []

        if self.config.get("user", {}).get("role") in ("developer", "qa"):
            # Auto-log time across active Jira tickets
            worklogs_created = self._auto_log_jira_worklogs(target_date)
        else:
            # Use manual configuration
            worklogs_created = self._sync_manual_activities(target_date)

        # Calculate total hours
        total_hours = sum(wl["time_spent_seconds"] for wl in worklogs_created) / 3600

        # Send notification
        self.notifier.send_daily_summary(worklogs_created, total_hours)

        # Print summary
        done_ts = datetime.now().strftime("%H:%M:%S")
        print(f"\n{'=' * 60}")
        print(f"[OK] SYNC COMPLETE ({done_ts})")
        print(f"{'=' * 60}")
        print(f"Total entries: {len(worklogs_created)}")
        print(f"Total hours: {total_hours:.2f} / {self.schedule_mgr.daily_hours}")

        if total_hours >= self.schedule_mgr.daily_hours:
            print("Status: [OK] Complete")
        else:
            print(f"Status: [!] Incomplete ({total_hours:.2f}h logged)")
        print()

        logger.info(f"Daily sync completed: {len(worklogs_created)} entries, {total_hours:.2f}h")

        # Determine reason for any shortfall (used by tray app for accurate notification)
        oh_stories = self._get_overhead_config().get("current_pi", {}).get("stories", [])
        fallback = self._get_overhead_config().get("fallback_issue_key", "")
        if total_hours == 0 and not oh_stories and not fallback:
            reason = "no_overhead"
        elif total_hours == 0:
            reason = "no_tickets"
        elif total_hours < self.schedule_mgr.daily_hours:
            reason = "partial"
        else:
            reason = "ok"

        return {
            "hours_logged": total_hours,
            "target_hours": self.schedule_mgr.daily_hours,
            "reason": reason,
        }

    def _sync_jira_worklogs(self, target_date: str) -> list[dict]:
        """Sync Jira worklogs to Tempo."""
        # Fetch Jira worklogs
        jira_worklogs = self.jira_client.get_my_worklogs(target_date, target_date)

        if not jira_worklogs:
            logger.info("No Jira worklogs found for today")
            return []

        # Wait for Jira->Tempo sync if Forge delay is configured
        self._forge_sync_wait()

        # Check which ones already exist in Tempo
        tempo_worklogs = self.tempo_client.get_user_worklogs(target_date, target_date)
        tempo_issue_keys = {wl.get("issue", {}).get("key") for wl in tempo_worklogs}

        # Create missing entries
        created = []
        for wl in jira_worklogs:
            if wl["issue_key"] not in tempo_issue_keys:
                success = self.tempo_client.create_worklog(
                    issue_key=wl["issue_key"],
                    time_seconds=wl["time_spent_seconds"],
                    start_date=target_date,
                    description=wl.get("comment", ""),
                )

                if success:
                    print(
                        f"  [OK] Created: {wl['issue_key']} - {wl['time_spent_seconds'] / 3600:.2f}h"
                    )
                    created.append(wl)
                else:
                    print(f"  [FAIL] {wl['issue_key']}")
            else:
                print(f"  [SKIP] Exists: {wl['issue_key']}")

        return created

    def _rollback_created(self, created_worklogs: list[dict], target_date: str):
        """Roll back newly created worklogs after a partial failure.

        Args:
            created_worklogs: List of dicts with issue_key and worklog_id
            target_date: Date string for logging context
        """
        if not created_worklogs:
            return
        print(f"\n[!] Rolling back {len(created_worklogs)} created worklog(s)...")
        for wl in created_worklogs:
            deleted = self.jira_client.delete_worklog(wl["issue_key"], wl.get("worklog_id", ""))
            if deleted:
                print(f"  [OK] Rolled back {wl['issue_key']}")
            else:
                print(f"  [FAIL] Could not roll back {wl['issue_key']}")
        print("[!] Rollback complete. Original worklogs preserved.\n")

    def _auto_log_jira_worklogs(self, target_date: str) -> list[dict]:
        """
        Auto-log worklogs by distributing daily hours across active
        Jira tickets. Preserves manually-entered overhead worklogs.

        Cases handled:
        - Case 0: Default daily overhead (e.g. 2h) logged first
        - Case 1: No active tickets -> overhead fallback
        - Case 2: Manual overhead preserved, remaining hours distributed
        - Case 4: Planning week -> upcoming PI overhead stories
        """
        oh_prefix = self._get_overhead_config().get("project_prefix", "OVERHEAD-")
        daily_hours = self.config.get("schedule", {}).get("daily_hours", 8)
        total_seconds = int(daily_hours * 3600)

        # Fetch Jira worklogs (has issue keys for identification)
        jira_worklogs = self.jira_client.get_my_worklogs(target_date, target_date)
        jira_total = sum(wl["time_spent_seconds"] for wl in jira_worklogs)

        # Separate Jira worklogs: overhead vs non-overhead
        jira_overhead = [
            wl for wl in jira_worklogs if wl.get("issue_key", "").startswith(oh_prefix)
        ]
        jira_non_overhead = [
            wl for wl in jira_worklogs if not wl.get("issue_key", "").startswith(oh_prefix)
        ]

        # Wait for Jira->Tempo sync if Forge delay is configured
        self._forge_sync_wait()

        # Fetch Tempo total (catches manual Tempo entries too)
        tempo_worklogs = self.tempo_client.get_user_worklogs(target_date, target_date)
        tempo_total = sum(twl.get("timeSpentSeconds", 0) for twl in tempo_worklogs)

        # Manual Tempo-only hours = entries added directly in Tempo
        # (not via Jira, so not visible in Jira API)
        tempo_only_seconds = max(0, tempo_total - jira_total)

        # Total overhead = Jira overhead + Tempo-only entries
        jira_oh_seconds = sum(wl["time_spent_seconds"] for wl in jira_overhead)
        overhead_seconds = jira_oh_seconds + tempo_only_seconds

        # Save non-overhead worklogs for deletion AFTER new ones succeed
        worklogs_to_delete = list(jira_non_overhead)
        if worklogs_to_delete:
            if self.dry_run:
                print(
                    f"[DRY RUN] Would remove "
                    f"{len(worklogs_to_delete)} non-overhead "
                    f"worklog(s) for {target_date}:"
                )
                for wl in worklogs_to_delete:
                    wl_h = wl["time_spent_seconds"] / 3600
                    print(f"  [DRY RUN] Would remove {wl_h:.2f}h from {wl['issue_key']}")
            else:
                print(
                    f"Found {len(worklogs_to_delete)} non-overhead "
                    f"worklog(s) to replace for {target_date}."
                )
            print()

        # Show overhead summary
        if overhead_seconds > 0:
            oh_hours = overhead_seconds / 3600
            print(f"Overhead hours detected ({oh_hours:.2f}h):")
            for wl in jira_overhead:
                wl_h = wl["time_spent_seconds"] / 3600
                print(f"  - {wl['issue_key']}: {wl_h:.2f}h (Jira)")
            if tempo_only_seconds > 0:
                t_h = tempo_only_seconds / 3600
                print(f"  - Manual Tempo entries: {t_h:.2f}h")
            print()

        # Case 0: Default daily overhead -- ensure minimum overhead hours
        default_oh_hours = self._get_overhead_config().get("daily_overhead_hours", 0)
        default_oh_seconds = int(default_oh_hours * 3600)
        if (
            default_oh_seconds > 0
            and overhead_seconds < default_oh_seconds
            and self._is_overhead_configured()
        ):
            gap_seconds = default_oh_seconds - overhead_seconds
            gap_hours = gap_seconds / 3600
            print(
                f"Default daily overhead: {default_oh_hours}h, "
                f"existing: {overhead_seconds / 3600:.2f}h, "
                f"logging {gap_hours:.2f}h more"
            )
            created_oh = self._log_overhead_hours(target_date, gap_seconds)
            overhead_seconds = default_oh_seconds
            # Add to overhead result tracking
            for wl in created_oh:
                jira_overhead.append(
                    {
                        "issue_key": wl["issue_key"],
                        "issue_summary": wl.get("issue_summary", wl["issue_key"]),
                        "time_spent_seconds": wl["time_spent_seconds"],
                    }
                )
            print()

        remaining_seconds = total_seconds - overhead_seconds

        # Build result list starting with preserved overhead
        overhead_result = [
            {
                "issue_key": wl["issue_key"],
                "issue_summary": wl.get("issue_summary", wl["issue_key"]),
                "time_spent_seconds": wl["time_spent_seconds"],
            }
            for wl in jira_overhead
        ]
        if tempo_only_seconds > 0:
            overhead_result.append(
                {
                    "issue_key": "OVERHEAD (Tempo)",
                    "issue_summary": "Manual Tempo entries",
                    "time_spent_seconds": tempo_only_seconds,
                }
            )

        if remaining_seconds <= 0:
            print(
                f"[OK] Overhead hours ({overhead_seconds / 3600:.2f}h) "
                f"meet/exceed daily target ({daily_hours}h). "
                f"No additional logging needed."
            )
            return overhead_result

        # Case 4: Check for planning week
        if self._is_planning_week(target_date):
            print("[INFO] PI planning week detected -- logging to overhead stories")
            oh = self._get_overhead_config()
            planning = oh.get("planning_pi", {})
            p_stories = planning.get("stories")
            p_dist = planning.get("distribution")
            if not p_stories:
                # Fall back to current PI stories
                print("  [INFO] No planning PI stories configured, using current PI stories")
                p_stories = None
                p_dist = None
            created = self._log_overhead_hours(target_date, remaining_seconds, p_stories, p_dist)
            return overhead_result + created

        # Get active issues for the current role's statuses
        active_issues = self.jira_client.get_my_active_issues(statuses=self._get_active_statuses())

        # Case 1: No active tickets -> overhead fallback
        if not active_issues:
            status_label = " / ".join(self._get_active_statuses())
            logger.warning(f"No active issues found ({status_label})")
            if self._is_overhead_configured():
                print("[INFO] No active tickets found. Logging to overhead stories.")
                created = self._log_overhead_hours(target_date, remaining_seconds)
                return overhead_result + created
            else:
                self._warn_overhead_not_configured()
                print("[!] No active tickets found and no overhead configured.")
                return overhead_result

        # Normal flow: distribute remaining hours across active tickets
        num_tickets = len(active_issues)
        remaining_hours = remaining_seconds / 3600
        print(f"Found {num_tickets} active ticket(s):")
        for issue in active_issues:
            print(f"  - {issue['issue_key']}: {issue['issue_summary']}")

        # Check for weighted distribution config
        weights = self.config.get("schedule", {}).get("distribution_weights", {})
        use_weights = bool(
            weights and any(issue["issue_key"] in weights for issue in active_issues)
        )

        if use_weights:
            # Weighted distribution
            weighted_items = []
            total_weight = 0.0
            for issue in active_issues:
                w = float(weights.get(issue["issue_key"], 1.0))
                weighted_items.append((issue, w))
                total_weight += w

            print(f"\nWeighted distribution ({remaining_hours:.2f}h):")
            allocations = []
            allocated = 0
            for idx, (issue, w) in enumerate(weighted_items):
                if idx == len(weighted_items) - 1:
                    t_secs = remaining_seconds - allocated
                else:
                    t_secs = int(remaining_seconds * w / total_weight)
                    allocated += t_secs
                t_hours = t_secs / 3600
                pct = (w / total_weight * 100) if total_weight else 0
                print(f"  - {issue['issue_key']}: {t_hours:.2f}h (weight {w:.1f}, {pct:.0f}%)")
                allocations.append((issue, t_secs))
            print()
        else:
            # Equal distribution (default)
            seconds_per_ticket = remaining_seconds // num_tickets
            remainder_secs = remaining_seconds - (seconds_per_ticket * num_tickets)
            print(
                f"\n{remaining_hours:.2f}h / {num_tickets} tickets = "
                f"{seconds_per_ticket / 3600:.2f}h each\n"
            )
            allocations = []
            for i, issue in enumerate(active_issues):
                t_secs = seconds_per_ticket + (remainder_secs if i == num_tickets - 1 else 0)
                allocations.append((issue, t_secs))

        # Phase 1: CREATE new worklogs (or preview in dry-run mode)
        # Use parallel creation when 2+ tickets and not dry-run
        if num_tickets >= 2 and not self.dry_run:
            created, creation_failed = self._create_worklogs_parallel(
                allocations, target_date, num_tickets
            )
        else:
            # Sequential for single ticket or dry-run
            created = []
            creation_failed = False
            for i, (issue, ticket_seconds) in enumerate(allocations):
                ticket_hours = ticket_seconds / 3600

                if self.dry_run:
                    print(f"  [DRY RUN] Would log {ticket_hours:.2f}h on {issue['issue_key']}")
                    created.append(
                        {
                            "issue_key": issue["issue_key"],
                            "issue_summary": issue["issue_summary"],
                            "time_spent_seconds": ticket_seconds,
                        }
                    )
                    continue

                comment = self._generate_work_summary(issue["issue_key"], issue["issue_summary"])
                worklog_id = self.jira_client.create_worklog(
                    issue_key=issue["issue_key"],
                    time_spent_seconds=ticket_seconds,
                    started=target_date,
                    comment=comment,
                )

                if worklog_id:
                    print(
                        f"  [{i + 1}/{num_tickets}] [OK] Logged "
                        f"{ticket_hours:.2f}h on "
                        f"{issue['issue_key']}"
                    )
                    print(f"    Description: {comment[:80]}{'...' if len(comment) > 80 else ''}")
                    created.append(
                        {
                            "issue_key": issue["issue_key"],
                            "issue_summary": issue["issue_summary"],
                            "time_spent_seconds": ticket_seconds,
                            "worklog_id": worklog_id,
                        }
                    )
                else:
                    print(f"  [{i + 1}/{num_tickets}] [FAIL] {issue['issue_key']}")
                    creation_failed = True
                    break

        # If any creation failed, roll back and preserve originals
        if creation_failed:
            self._rollback_created(created, target_date)
            return overhead_result

        # Phase 2: DELETE old worklogs only after all new ones succeeded
        if worklogs_to_delete and not self.dry_run:
            print(f"\nRemoving {len(worklogs_to_delete)} old non-overhead worklog(s)...")
            for wl in worklogs_to_delete:
                deleted = self.jira_client.delete_worklog(wl["issue_key"], wl["worklog_id"])
                if deleted:
                    print(
                        f"  [OK] Removed "
                        f"{wl['time_spent_seconds'] / 3600:.2f}h "
                        f"from {wl['issue_key']}"
                    )
                else:
                    print(f"  [FAIL] Could not remove worklog from {wl['issue_key']}")
            print()

        return overhead_result + created

    def _create_worklogs_parallel(
        self, allocations: list[tuple], target_date: str, num_tickets: int
    ) -> tuple[list[dict], bool]:
        """Create worklogs in parallel using ThreadPoolExecutor.

        Args:
            allocations: List of (issue, ticket_seconds) tuples.
            target_date: Date string (YYYY-MM-DD) for worklogs.
            num_tickets: Total number of tickets being logged.

        Returns:
            Tuple of (created worklogs list, creation_failed bool).
        """
        created = []
        creation_failed = False
        futures = {}

        with ThreadPoolExecutor(max_workers=4) as executor:
            for i, (issue, ticket_seconds) in enumerate(allocations):
                comment = self._generate_work_summary(issue["issue_key"], issue["issue_summary"])
                future = executor.submit(
                    self.jira_client.create_worklog,
                    issue_key=issue["issue_key"],
                    time_spent_seconds=ticket_seconds,
                    started=target_date,
                    comment=comment,
                )
                futures[future] = (i, issue, ticket_seconds, comment)

            # Collect results as they complete
            results = {}
            for future in as_completed(futures):
                i, issue, ticket_seconds, comment = futures[future]
                ticket_hours = ticket_seconds / 3600
                try:
                    worklog_id = future.result()
                    if worklog_id:
                        results[i] = {
                            "success": True,
                            "issue": issue,
                            "ticket_seconds": ticket_seconds,
                            "ticket_hours": ticket_hours,
                            "comment": comment,
                            "worklog_id": worklog_id if isinstance(worklog_id, str) else "",
                        }
                    else:
                        results[i] = {
                            "success": False,
                            "issue": issue,
                        }
                except Exception as e:
                    logger.error(f"Parallel worklog creation failed for {issue['issue_key']}: {e}")
                    results[i] = {
                        "success": False,
                        "issue": issue,
                    }

        # Print results in original order
        for i in sorted(results.keys()):
            r = results[i]
            if r["success"]:
                print(
                    f"  [{i + 1}/{num_tickets}] [OK] Logged "
                    f"{r['ticket_hours']:.2f}h on "
                    f"{r['issue']['issue_key']}"
                )
                print(
                    f"    Description: {r['comment'][:80]}{'...' if len(r['comment']) > 80 else ''}"
                )
                created.append(
                    {
                        "issue_key": r["issue"]["issue_key"],
                        "issue_summary": r["issue"]["issue_summary"],
                        "time_spent_seconds": r["ticket_seconds"],
                        "worklog_id": r.get("worklog_id", ""),
                    }
                )
            else:
                print(f"  [{i + 1}/{num_tickets}] [FAIL] {r['issue']['issue_key']}")
                creation_failed = True

        return created, creation_failed

    def _generate_work_summary(self, issue_key: str, issue_summary: str) -> str:
        """
        Generate a brief worklog description from a Jira ticket's content.

        Reads the ticket's description and recent comments, then builds
        a concise 1-3 line summary of work done.

        Args:
            issue_key: Jira issue key
            issue_summary: Issue title/summary

        Returns:
            A brief description string for the worklog comment
        """
        details = self.jira_client.get_issue_details(issue_key)

        if not details:
            return f"Worked on {issue_key}: {issue_summary}"

        lines = []

        # Line 1: What the ticket is about (from summary + description)
        desc = details.get("description_text", "")
        if desc:
            # Take the first sentence or up to 120 chars from description
            first_sentence = desc.split(".")[0].strip()
            if len(first_sentence) > 120:
                first_sentence = first_sentence[:117] + "..."
            lines.append(first_sentence)
        else:
            lines.append(issue_summary)

        # Lines 2-3: What was actually done (from recent comments)
        comments = details.get("recent_comments", [])
        for c in reversed(comments):  # most recent first
            # Take first meaningful line from the comment
            c_line = c.split("\n")[0].strip()
            if c_line and len(c_line) > 5:
                if len(c_line) > 120:
                    c_line = c_line[:117] + "..."
                lines.append(c_line)
            if len(lines) >= 3:
                break

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # OVERHEAD STORY SUPPORT
    # ------------------------------------------------------------------

    def _get_active_statuses(self) -> list[str]:
        """Return Jira statuses considered 'active' for the current role."""
        role = self.config.get("user", {}).get("role", "developer")
        if role == "qa":
            return QA_STATUSES
        return DEVELOPER_STATUSES

    def _get_overhead_config(self) -> dict:
        """Get overhead configuration section from config."""
        return self.config.get("overhead", {})

    def _is_overhead_configured(self) -> bool:
        """Check if overhead stories are configured for current PI."""
        oh = self._get_overhead_config()
        current_pi = oh.get("current_pi", {})
        return bool(current_pi.get("pi_identifier") and current_pi.get("stories"))

    def _parse_pi_end_date(self, pi_identifier: str) -> str | None:
        """
        Parse PI end date from identifier (PI.YY.N.MON.DD).

        Args:
            pi_identifier: e.g. "PI.26.2.APR.17"

        Returns:
            Date string "YYYY-MM-DD" or None if parsing fails
        """
        match = re.match(r"PI\.(\d{2})\.(\d+)\.([A-Z]{3})\.(\d{1,2})", pi_identifier)
        if not match:
            return None
        yy, _, mon_str, dd = match.groups()
        month_map = {
            "JAN": 1,
            "FEB": 2,
            "MAR": 3,
            "APR": 4,
            "MAY": 5,
            "JUN": 6,
            "JUL": 7,
            "AUG": 8,
            "SEP": 9,
            "OCT": 10,
            "NOV": 11,
            "DEC": 12,
        }
        month = month_map.get(mon_str)
        if not month:
            return None
        year = 2000 + int(yy)
        try:
            end_date = date(year, month, int(dd))
            return end_date.strftime("%Y-%m-%d")
        except ValueError:
            return None

    def _is_planning_week(self, target_date: str) -> bool:
        """
        Check if target_date falls in the PI planning week.

        Planning week = 5 working days immediately after PI end date.
        """
        oh = self._get_overhead_config()
        current_pi = oh.get("current_pi", {})
        pi_end_str = current_pi.get("pi_end_date", "")
        if not pi_end_str:
            pi_id = current_pi.get("pi_identifier", "")
            pi_end_str = self._parse_pi_end_date(pi_id)
            if not pi_end_str:
                return False

        pi_end = datetime.strptime(pi_end_str, "%Y-%m-%d").date()
        target = datetime.strptime(target_date, "%Y-%m-%d").date()

        # Planning week starts the day after PI end
        if target <= pi_end:
            return False

        # Count 5 working days after PI end
        current = pi_end + timedelta(days=1)
        working_count = 0
        planning_end = None
        while working_count < 5:
            is_working, _ = self.schedule_mgr.is_working_day(current.strftime("%Y-%m-%d"))
            if is_working:
                working_count += 1
                planning_end = current
            current += timedelta(days=1)
            # Safety: don't scan more than 14 calendar days
            if (current - pi_end).days > 14:
                return False

        if planning_end is None:
            return False
        return pi_end < target <= planning_end

    def _log_overhead_hours(
        self,
        target_date: str,
        total_seconds: int,
        stories: list[dict] = None,
        distribution: str = None,
    ) -> list[dict]:
        """
        Create Jira worklogs on overhead stories (syncs to Tempo).

        Args:
            target_date: Date string YYYY-MM-DD
            total_seconds: Total seconds to distribute
            stories: List of story dicts with issue_key, summary, hours.
                     If None, uses current_pi.stories from config.
            distribution: "single", "equal", or "custom".
                          If None, uses current_pi.distribution from config.

        Returns:
            List of created worklog dicts
        """
        oh = self._get_overhead_config()

        if stories is None:
            current_pi = oh.get("current_pi", {})
            stories = current_pi.get("stories", [])
            if distribution is None:
                distribution = current_pi.get("distribution", "equal")

        if distribution is None:
            distribution = "equal"

        if not stories:
            # Try fallback
            fallback = oh.get("fallback_issue_key", "")
            if fallback:
                stories = [{"issue_key": fallback, "summary": fallback}]
                distribution = "single"
                print(f"  [INFO] Using fallback overhead: {fallback}")
            else:
                print(
                    "[!] No overhead stories configured. "
                    "Run: python tempo_automation.py --select-overhead"
                )
                return []

        # Calculate seconds per story based on distribution mode
        allocations = []
        if distribution == "single":
            allocations = [(stories[0], total_seconds)]
        elif distribution == "custom":
            # Proportional scaling based on configured hours
            total_configured = sum(s.get("hours", 0) for s in stories)
            if total_configured <= 0:
                total_configured = len(stories)
                for s in stories:
                    s["hours"] = 1
            for s in stories:
                ratio = s.get("hours", 0) / total_configured
                allocations.append((s, int(total_seconds * ratio)))
            # Fix rounding: adjust last entry
            allocated = sum(a[1] for a in allocations)
            if allocations and allocated != total_seconds:
                last_s, last_sec = allocations[-1]
                allocations[-1] = (last_s, last_sec + total_seconds - allocated)
        else:
            # equal distribution
            num = len(stories)
            per_ticket = total_seconds // num
            remainder = total_seconds - (per_ticket * num)
            for i, s in enumerate(stories):
                secs = per_ticket + (remainder if i == num - 1 else 0)
                allocations.append((s, secs))

        created = []
        num_alloc = len(allocations)
        for idx, (story, seconds) in enumerate(allocations):
            if seconds <= 0:
                continue
            hours = seconds / 3600
            key = story["issue_key"]
            summary = story.get("summary", key)
            comment = f"Overhead - {summary}"

            if self.dry_run:
                print(f"  [DRY RUN] Would log {hours:.2f}h on {key} (overhead)")
                created.append(
                    {"issue_key": key, "issue_summary": summary, "time_spent_seconds": seconds}
                )
                continue

            success = self.jira_client.create_worklog(
                issue_key=key, time_spent_seconds=seconds, started=target_date, comment=comment
            )
            if success:
                print(f"  [{idx + 1}/{num_alloc}] [OK] Logged {hours:.2f}h on {key} (overhead)")
                created.append(
                    {"issue_key": key, "issue_summary": summary, "time_spent_seconds": seconds}
                )
            else:
                print(f"  [{idx + 1}/{num_alloc}] [FAIL] {key}")

        return created

    def _warn_overhead_not_configured(self):
        """Warn user that overhead stories need to be selected."""
        msg = (
            "[!] Overhead stories not configured for current PI.\n"
            "    Run: python tempo_automation.py --select-overhead"
        )
        print(msg)
        logger.warning("Overhead stories not configured")
        try:
            self.notifier.send_windows_notification(
                "Overhead Not Configured",
                "Run --select-overhead to set overhead stories for this PI.",
            )
        except Exception:
            pass

    def _save_config(self):
        """Write current config to config.json."""
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2)
            if sys.platform != "win32":
                os.chmod(CONFIG_FILE, 0o600)
            logger.info("Config saved")
        except Exception as e:
            logger.error(f"Error saving config: {e}")

    def _check_overhead_pi_current(self) -> bool:
        """
        Check if stored overhead PI matches the current active PI.

        Caches check daily to avoid API calls on every run.
        """
        oh = self._get_overhead_config()
        stored_pi = oh.get("current_pi", {}).get("pi_identifier", "")
        if not stored_pi:
            return False

        # Check cache -- only verify once per day
        last_check = oh.get("_last_pi_check", "")
        today_str = date.today().strftime("%Y-%m-%d")
        if last_check == today_str:
            return True

        if not self.jira_client:
            return True  # Can't verify without Jira client

        stories = self.jira_client.get_overhead_stories()
        if not stories:
            return True  # Can't verify, assume current

        # Check if any story has a different PI
        pi_ids = set(s["pi_identifier"] for s in stories if s.get("pi_identifier"))
        is_current = stored_pi in pi_ids

        # Update cache timestamp
        if "overhead" not in self.config:
            self.config["overhead"] = {}
        self.config["overhead"]["_last_pi_check"] = today_str
        self._save_config()

        return is_current

    def select_overhead_stories(self) -> bool:
        """
        Interactive CLI flow for selecting overhead stories.

        Queries Jira OVERHEAD project, groups by PI, lets user
        select stories for current PI and planning PI.

        Returns:
            True if selection saved, False if cancelled/failed
        """
        if not self.jira_client:
            print("[ERROR] Jira client not available (developer role required)")
            return False

        print(f"\n{'=' * 60}")
        print("OVERHEAD STORY SELECTION")
        print(f"{'=' * 60}")

        stories = self.jira_client.get_overhead_stories()
        if not stories:
            print("\n[!] No active overhead stories found in Jira.")
            print("    (project = OVERHEAD, status = In Progress)")
            fallback = input("\nEnter a fallback issue key (or Enter to skip): ").strip()
            if fallback:
                self.config.setdefault("overhead", {})
                self.config["overhead"]["fallback_issue_key"] = fallback
                self._save_config()
                print(f"[OK] Fallback set to {fallback}")
            return False

        # Group stories by PI
        pi_groups = {}
        no_pi = []
        for s in stories:
            pi = s.get("pi_identifier", "")
            if pi:
                pi_groups.setdefault(pi, []).append(s)
            else:
                no_pi.append(s)

        # Sort PIs (latest first)
        sorted_pis = sorted(pi_groups.keys(), reverse=True)

        # Display all stories grouped by PI
        print(f"\nFound {len(stories)} overhead story(ies):\n")
        display_list = []
        for pi in sorted_pis:
            print(f"  -- {pi} --")
            for s in pi_groups[pi]:
                idx = len(display_list) + 1
                print(f"  {idx}. {s['issue_key']}: {s['issue_summary']}")
                display_list.append(s)
        if no_pi:
            print("  -- No PI --")
            for s in no_pi:
                idx = len(display_list) + 1
                print(f"  {idx}. {s['issue_key']}: {s['issue_summary']}")
                display_list.append(s)

        # --- Current PI selection ---
        print("\n--- Current PI Stories ---")
        print("Select stories for normal days (no active tickets):")
        raw = input("Enter numbers (comma-separated), 'all', or Enter to skip: ").strip()

        if not raw:
            print("[!] No stories selected.")
            return False

        selected = self._parse_story_selection(raw, display_list)
        if not selected:
            print("[!] Invalid selection.")
            return False

        print(f"\nSelected {len(selected)} story(ies):")
        for s in selected:
            print(f"  - {s['issue_key']}: {s['issue_summary']}")

        # Distribution mode
        distribution = self._ask_distribution_mode(selected)

        # Assign custom hours if needed
        story_configs = []
        if distribution == "custom":
            daily_hours = self.config.get("schedule", {}).get("daily_hours", 8)
            print(f"\nAssign hours per story (total = {daily_hours}h):")
            remaining = daily_hours
            for i, s in enumerate(selected):
                if i == len(selected) - 1:
                    hrs = remaining
                    print(f"  {s['issue_key']} ({s['issue_summary']}): {hrs}h (remainder)")
                else:
                    raw_h = input(f"  {s['issue_key']} ({s['issue_summary']}): ").strip()
                    try:
                        hrs = min(float(raw_h), remaining)
                    except ValueError:
                        hrs = remaining / (len(selected) - i)
                    remaining -= hrs
                story_configs.append(
                    {"issue_key": s["issue_key"], "summary": s["issue_summary"], "hours": hrs}
                )
        else:
            for s in selected:
                story_configs.append({"issue_key": s["issue_key"], "summary": s["issue_summary"]})

        # Detect current PI from selection
        current_pi_id = ""
        for s in selected:
            if s.get("pi_identifier"):
                current_pi_id = s["pi_identifier"]
                break
        pi_end = self._parse_pi_end_date(current_pi_id) or ""

        # --- PTO story selection ---
        print("\n--- PTO Story ---")
        print("Which story for PTO/Holiday days?")
        print("(Use the numbered list above)")
        pto_raw = input("Choice (enter number): ").strip()
        pto_key = ""
        pto_summary = ""
        if pto_raw.isdigit():
            idx = int(pto_raw) - 1
            if 0 <= idx < len(display_list):
                pto_key = display_list[idx]["issue_key"]
                pto_summary = display_list[idx]["issue_summary"]
        if not pto_key:
            pto_key = selected[0]["issue_key"]
            pto_summary = selected[0]["issue_summary"]
            print(f"  Defaulting to: {pto_key}")
        print(f"  PTO story: {pto_key}: {pto_summary}")

        # --- Planning PI selection ---
        planning_config = {}
        # Find PIs different from current
        other_pis = [p for p in sorted_pis if p != current_pi_id]
        if other_pis:
            print("\n--- Planning Week Stories ---")
            print("Planning week uses UPCOMING PI stories.")
            upcoming_pi = other_pis[0]  # Latest non-current PI
            upcoming_stories = pi_groups.get(upcoming_pi, [])
            if upcoming_stories:
                print(f"\nUpcoming PI: {upcoming_pi}")
                for i, s in enumerate(upcoming_stories, 1):
                    print(f"  {i}. {s['issue_key']}: {s['issue_summary']}")
                p_raw = input(
                    "Select for planning (comma-separated, 'all', Enter for all): "
                ).strip()
                if not p_raw or p_raw.lower() == "all":
                    p_selected = upcoming_stories
                else:
                    p_selected = self._parse_story_selection(p_raw, upcoming_stories)
                if p_selected:
                    p_dist = self._ask_distribution_mode(p_selected)
                    p_stories = []
                    if p_dist == "custom":
                        daily_h = self.config.get("schedule", {}).get("daily_hours", 8)
                        print(f"\nAssign planning hours (total = {daily_h}h):")
                        p_rem = daily_h
                        for i, s in enumerate(p_selected):
                            if i == len(p_selected) - 1:
                                h = p_rem
                                print(f"  {s['issue_key']}: {h}h (remainder)")
                            else:
                                raw_h = input(f"  {s['issue_key']}: ").strip()
                                try:
                                    h = float(raw_h)
                                except ValueError:
                                    h = p_rem / (len(p_selected) - i)
                                p_rem -= h
                            p_stories.append(
                                {
                                    "issue_key": s["issue_key"],
                                    "summary": s["issue_summary"],
                                    "hours": h,
                                }
                            )
                    else:
                        for s in p_selected:
                            p_stories.append(
                                {"issue_key": s["issue_key"], "summary": s["issue_summary"]}
                            )

                    planning_config = {
                        "pi_identifier": upcoming_pi,
                        "stories": p_stories,
                        "distribution": p_dist,
                    }
        else:
            print(
                "\n[INFO] Only one PI found. Planning week stories "
                "can be configured later when the next PI is "
                "created."
            )

        # --- Fallback ---
        existing_fallback = self._get_overhead_config().get("fallback_issue_key", "")
        print("\n--- Fallback ---")
        fb_raw = input(f"Fallback issue key (Enter for '{existing_fallback or 'none'}'): ").strip()
        fallback_key = fb_raw if fb_raw else existing_fallback

        # --- Default daily overhead hours ---
        existing_doh = self._get_overhead_config().get("daily_overhead_hours", 2)
        print("\n--- Default Daily Overhead ---")
        print(
            "Hours logged to overhead EVERY working day "
            "(before distributing remaining to active tickets)."
        )
        doh_raw = input(f"Daily overhead hours (Enter for {existing_doh}): ").strip()
        try:
            daily_oh_hours = float(doh_raw) if doh_raw else existing_doh
        except ValueError:
            daily_oh_hours = existing_doh
        print(f"  Daily overhead: {daily_oh_hours}h")

        # --- Save ---
        overhead_config = {
            "current_pi": {
                "pi_identifier": current_pi_id,
                "pi_end_date": pi_end,
                "stories": story_configs,
                "distribution": distribution,
            },
            "pto_story_key": pto_key,
            "pto_story_summary": pto_summary,
            "planning_pi": planning_config,
            "daily_overhead_hours": daily_oh_hours,
            "fallback_issue_key": fallback_key,
            "project_prefix": self._get_overhead_config().get("project_prefix", "OVERHEAD-"),
            "_last_pi_check": date.today().strftime("%Y-%m-%d"),
        }
        self.config["overhead"] = overhead_config
        self._save_config()

        # --- Summary ---
        print(f"\n{'=' * 60}")
        print("[OK] Overhead stories saved")
        print(f"{'=' * 60}")
        print(f"  Current PI: {current_pi_id}")
        if pi_end:
            print(f"  PI end date: {pi_end}")
        print(f"  Distribution: {distribution}")
        print(f"  Stories: {len(story_configs)}")
        for s in story_configs:
            hrs = s.get("hours", "")
            hrs_str = f" ({hrs}h)" if hrs else ""
            print(f"    - {s['issue_key']}: {s['summary']}{hrs_str}")
        print(f"  Daily overhead: {daily_oh_hours}h")
        print(f"  PTO story: {pto_key}: {pto_summary}")
        if planning_config:
            print(f"  Planning PI: {planning_config.get('pi_identifier', '')}")
            print(f"  Planning stories: {len(planning_config.get('stories', []))}")
        if fallback_key:
            print(f"  Fallback: {fallback_key}")
        print()

        return True

    def _parse_story_selection(self, raw: str, stories: list[dict]) -> list[dict]:
        """Parse user input for story selection."""
        if raw.lower() == "all":
            return list(stories)
        indices = []
        for part in raw.split(","):
            part = part.strip()
            if part.isdigit():
                idx = int(part) - 1
                if 0 <= idx < len(stories):
                    indices.append(idx)
        return [stories[i] for i in indices]

    def _ask_distribution_mode(self, selected: list[dict]) -> str:
        """Ask user for hour distribution mode."""
        if len(selected) == 1:
            return "single"
        print("\nHow should hours be distributed?")
        print("  1. Equal split across stories")
        print("  2. Custom hours per story")
        choice = input("Choice (1 or 2, default 1): ").strip()
        if choice == "2":
            return "custom"
        return "equal"

    def show_overhead_config(self):
        """Display current overhead story configuration."""
        oh = self._get_overhead_config()
        current_pi = oh.get("current_pi", {})
        if not current_pi or not current_pi.get("stories"):
            print("\n[INFO] No overhead stories configured.")
            print("  Run: python tempo_automation.py --select-overhead")
            return

        print("\nOverhead Configuration")
        print("=" * 50)
        print(f"  PI: {current_pi.get('pi_identifier', '(none)')}")
        print(f"  PI End Date: {current_pi.get('pi_end_date', '(none)')}")
        print(f"  Distribution: {current_pi.get('distribution', '(none)')}")
        print("  Stories:")
        for s in current_pi.get("stories", []):
            hrs = s.get("hours", "")
            hrs_str = f" ({hrs}h)" if hrs else ""
            print(f"    - {s['issue_key']}: {s.get('summary', '')}{hrs_str}")
        daily_oh = oh.get("daily_overhead_hours", 0)
        print(f"  Daily Overhead: {daily_oh}h")
        pto_key = oh.get("pto_story_key", "")
        pto_sum = oh.get("pto_story_summary", "")
        if pto_key:
            print(f"  PTO Story: {pto_key}: {pto_sum}")
        else:
            print("  PTO Story: (none)")

        planning = oh.get("planning_pi", {})
        if planning and planning.get("stories"):
            print(f"  Planning PI: {planning.get('pi_identifier', '(none)')}")
            print(f"  Planning Distribution: {planning.get('distribution', '(none)')}")
            for s in planning.get("stories", []):
                hrs = s.get("hours", "")
                hrs_str = f" ({hrs}h)" if hrs else ""
                print(f"    - {s['issue_key']}: {s.get('summary', '')}{hrs_str}")

        print(f"  Fallback: {oh.get('fallback_issue_key', '(none)')}")

        # Show planning week dates if PI end date known
        pi_end = current_pi.get("pi_end_date", "")
        if pi_end:
            pi_end_dt = datetime.strptime(pi_end, "%Y-%m-%d").date()
            pw_start = pi_end_dt + timedelta(days=1)
            # Find the 5th working day
            current_d = pw_start
            count = 0
            while count < 5:
                is_w, _ = self.schedule_mgr.is_working_day(current_d.strftime("%Y-%m-%d"))
                if is_w:
                    count += 1
                    if count == 5:
                        break
                current_d += timedelta(days=1)
                if (current_d - pi_end_dt).days > 14:
                    break
            print(
                f"\n  Planning week: "
                f"{pw_start.strftime('%Y-%m-%d')} to "
                f"{current_d.strftime('%Y-%m-%d')}"
            )
        print()

    def _sync_manual_activities(self, target_date: str) -> list[dict]:
        """Sync manual activities from configuration."""
        manual_activities = self.config.get("manual_activities", [])

        if not manual_activities:
            logger.warning("No manual activities configured")
            print("[!] No manual activities configured. Please edit config.json")
            return []

        # Check existing entries
        tempo_worklogs = self.tempo_client.get_user_worklogs(target_date, target_date)
        existing_hours = sum(wl.get("timeSpentSeconds", 0) for wl in tempo_worklogs) / 3600

        if existing_hours >= self.schedule_mgr.daily_hours:
            logger.info("Manual entries already meet daily hours")
            print("[SKIP] Timesheet entries already meet daily hours")
            return tempo_worklogs

        # Create entries from configuration
        created = []
        for activity in manual_activities:
            # Get issue key from config, or use default
            # Ask your Jira admin what issue key to use for general time tracking
            issue_key = self.config.get("organization", {}).get("default_issue_key", "GENERAL-001")

            time_seconds = int(activity.get("hours", 0) * 3600)

            if self.dry_run:
                act_hours = activity.get("hours", 0)
                print(
                    f"  [DRY RUN] Would log "
                    f"{act_hours}h on {issue_key} "
                    f"({activity.get('activity', '')})"
                )
                created.append(
                    {
                        "issue_key": issue_key,
                        "issue_summary": activity["activity"],
                        "time_spent_seconds": time_seconds,
                    }
                )
                continue

            success = self.tempo_client.create_worklog(
                issue_key=issue_key,
                time_seconds=time_seconds,
                start_date=target_date,
                description=activity.get("activity", ""),
            )

            if success:
                print(f"  [OK] Created: {activity['activity']} - {activity['hours']}h")
                created.append(
                    {
                        "issue_key": issue_key,
                        "issue_summary": activity["activity"],
                        "time_spent_seconds": time_seconds,
                    }
                )

        return created

    def submit_timesheet(self):
        """Submit monthly timesheet with per-day gap detection.

        Runs from day 28 onwards (or last 7 days for short months),
        or earlier when all remaining days are non-working
        (PTO/holidays/weekends).
        - If shortfalls found: saves shortfall JSON, does NOT submit.
        - If no shortfalls and last day: auto-submits.
        - If no shortfalls but not last day: reports clean status.
        """
        # Pre-sync health check
        if not self._pre_sync_health_check():
            print("[FAIL] Aborting timesheet submission due to API health check failure.")
            return

        today = date.today()
        last_day_num = calendar.monthrange(today.year, today.month)[1]
        is_last_day = today.day == last_day_num
        period = f"{today.year}-{today.month:02d}"

        # Guard: already submitted this month (skipped in dry-run)
        if not self.dry_run and self._is_already_submitted(period):
            print(f"[OK] Timesheet for {period} was already submitted.")
            logger.info(f"Skipping submission: {period} already submitted")
            return

        # Check early submission eligibility: all remaining days
        # are non-working (PTO, holidays, weekends)
        tomorrow = today + timedelta(days=1)
        last_date = today.replace(day=last_day_num)
        if tomorrow <= last_date:
            remaining = self.schedule_mgr.count_working_days(
                tomorrow.strftime("%Y-%m-%d"), last_date.strftime("%Y-%m-%d")
            )
            early_submit_eligible = remaining == 0
        else:
            early_submit_eligible = True  # today IS last day

        # Guard: only run in submission window (last 7 days)
        # unless early submission is eligible
        submission_start = max(1, last_day_num - 6)
        if today.day < submission_start and not early_submit_eligible:
            print(
                f"[SKIP] Not in submission window yet "
                f"(day {today.day}/{last_day_num}). "
                f"Window opens on day {submission_start}."
            )
            logger.info(
                f"Skipping submission -- day {today.day} before window (day {submission_start})"
            )
            return

        if early_submit_eligible and today.day < submission_start:
            print("[INFO] All remaining days are non-working (PTO/holidays/weekends).")
            print("       Submitting timesheet early.\n")
            logger.info("Early submission: no working days remain in month after today")

        logger.info("Starting monthly submission check")
        now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n{'=' * 60}")
        print(f"TEMPO MONTHLY SUBMISSION CHECK ({now_ts})")
        print(f"{'=' * 60}\n")

        # --- Per-day gap detection ---
        gap_data = self._detect_monthly_gaps(today.year, today.month)

        print("Monthly Hours Check:")
        print(
            f"  Working days: {gap_data['working_days']}  |  "
            f"Expected: {gap_data['expected']:.1f}h  |  "
            f"Actual: {gap_data['actual']:.1f}h"
        )

        if gap_data["gaps"]:
            total_gap = sum(g["gap"] for g in gap_data["gaps"])
            print(
                f"  [!] SHORTFALL: {total_gap:.1f}h missing across {len(gap_data['gaps'])} day(s)\n"
            )
            print(f"  {'Date':<12} {'Day':<10} {'Logged':>7} {'Expected':>8} {'Gap':>6}")
            print(f"  {'-' * 46}")
            for g in gap_data["gaps"]:
                print(
                    f"  {g['date']:<12} {g['day']:<10} "
                    f"{g['logged']:>6.1f}h "
                    f"{g['expected']:>7.1f}h "
                    f"{g['gap']:>5.1f}h"
                )
            print()

            # Save shortfall data for tray app
            self._save_shortfall_data(gap_data)
            print(f"  [INFO] Shortfall saved to {SHORTFALL_FILE.name}")

            # Send notification
            first_day_str = today.replace(day=1).strftime("%Y-%m-%d")
            self._send_shortfall_notification(
                "monthly",
                first_day_str,
                today.strftime("%Y-%m-%d"),
                gap_data["expected"],
                gap_data["actual"],
            )

            # DO NOT submit
            print("\n  [!] Timesheet NOT submitted due to shortfall.")
            print("      Fix gaps via tray menu or --fix-shortfall, then --submit again.")
            logger.info(
                f"Submission blocked: {len(gap_data['gaps'])} "
                f"days with shortfall ({total_gap:.1f}h)"
            )
            return

        # --- No shortfall ---
        print("  [OK] Hours complete -- no shortfalls detected\n")

        # Clean up stale shortfall file
        if SHORTFALL_FILE.exists():
            SHORTFALL_FILE.unlink(missing_ok=True)
            logger.info("Stale shortfall file removed")

        if not is_last_day and not early_submit_eligible:
            print(
                f"  [INFO] No shortfalls. Auto-submission will "
                f"happen on {today.replace(day=last_day_num)}."
            )
            return

        # --- Last day (or early eligible), no shortfall: submit ---
        if self.dry_run:
            print(f"[DRY RUN] Would submit timesheet for {period}")
            print("[DRY RUN] Gap detection ran above -- no API call made.")
            return

        print(f"Submitting timesheet for {period}...")
        success = self.tempo_client.submit_timesheet(period)

        if success:
            print(f"[OK] Timesheet submitted successfully for {period}")
            self._save_submitted_marker(period)
            self.notifier.send_submission_confirmation(period)
            if SHORTFALL_FILE.exists():
                SHORTFALL_FILE.unlink(missing_ok=True)
        else:
            print(f"[FAIL] Failed to submit timesheet for {period}")

        print()
        logger.info(f"Timesheet submission {'successful' if success else 'failed'}")

    # ------------------------------------------------------------------
    # Monthly gap detection & shortfall fix
    # ------------------------------------------------------------------

    def _detect_monthly_gaps(self, year: int, month: int) -> dict:
        """Detect per-day hour shortfalls for a given month.

        Fetches all worklogs for the month in one API call, groups by
        date, then compares each working day against daily_hours.

        Args:
            year: Calendar year (e.g. 2026)
            month: Calendar month (1-12)

        Returns:
            Dict with keys: period, expected, actual, gaps (list of
            shortfall days), working_days, day_details (all working days)
        """
        last_day_num = calendar.monthrange(year, month)[1]
        first_date = date(year, month, 1)
        last_date = date(year, month, last_day_num)
        today = date.today()

        # Don't check future days
        end_date = min(last_date, today)

        first_str = first_date.strftime("%Y-%m-%d")
        end_str = end_date.strftime("%Y-%m-%d")

        # Fetch worklogs -- Tempo is source of truth (catches manual entries)
        hours_by_date = {}
        if self.tempo_client.account_id:
            tempo_worklogs = self.tempo_client.get_user_worklogs(first_str, end_str)
            for twl in tempo_worklogs:
                d = twl.get("startDate", "")
                hours_by_date[d] = hours_by_date.get(d, 0) + (twl.get("timeSpentSeconds", 0) / 3600)
        elif self.jira_client:
            # Fallback: Jira API (PO/Sales without Tempo account_id)
            jira_worklogs = self.jira_client.get_my_worklogs(first_str, end_str)
            for wl in jira_worklogs:
                d = wl["started"]
                hours_by_date[d] = hours_by_date.get(d, 0) + (wl["time_spent_seconds"] / 3600)
        else:
            logger.error("No API client available for gap detection")
            print(
                "[ERROR] Cannot detect gaps: no Tempo or Jira client available. Check API tokens."
            )

        daily_hours = self.schedule_mgr.daily_hours
        gaps = []
        day_details = []
        total_expected = 0.0
        total_actual = 0.0
        working_day_count = 0

        total_days = (end_date - first_date).days + 1
        current = first_date
        day_index = 0
        while current <= end_date:
            day_index += 1
            day_str = current.strftime("%Y-%m-%d")
            logger.debug(f"[{day_index}/{total_days}] Checking {day_str}")
            is_working, reason = self.schedule_mgr.is_working_day(day_str)

            if is_working:
                working_day_count += 1
                logged = hours_by_date.get(day_str, 0.0)
                total_expected += daily_hours
                total_actual += logged
                gap = daily_hours - logged

                detail = {
                    "date": day_str,
                    "day": current.strftime("%A"),
                    "logged": round(logged, 2),
                    "expected": daily_hours,
                    "gap": round(max(0, gap), 2),
                }
                day_details.append(detail)

                if gap > 0.5:
                    gaps.append(detail)

            current += timedelta(days=1)

        period = f"{year}-{month:02d}"
        return {
            "period": period,
            "expected": round(total_expected, 1),
            "actual": round(total_actual, 1),
            "gaps": gaps,
            "working_days": working_day_count,
            "day_details": day_details,
        }

    def _save_shortfall_data(self, gap_data: dict) -> None:
        """Save monthly shortfall data to JSON file."""
        payload = {
            "period": gap_data["period"],
            "detected_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "expected": gap_data["expected"],
            "actual": gap_data["actual"],
            "gaps": gap_data["gaps"],
        }
        with open(SHORTFALL_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        logger.info(
            f"Shortfall data saved: {len(gap_data['gaps'])} gap days for {gap_data['period']}"
        )

    def _save_submitted_marker(self, period: str) -> None:
        """Record that the timesheet was successfully submitted."""
        payload = {"period": period, "submitted_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}
        with open(SUBMITTED_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        logger.info(f"Submission marker saved for {period}")

    def _is_already_submitted(self, period: str) -> bool:
        """Check if timesheet was already submitted for this period."""
        if not SUBMITTED_FILE.exists():
            return False
        try:
            with open(SUBMITTED_FILE, encoding="utf-8") as f:
                data = json.load(f)
            return data.get("period") == period
        except (OSError, json.JSONDecodeError):
            return False

    def view_monthly_hours(self, month_str: str = "current"):
        """Display per-day hours table for a month.

        Args:
            month_str: 'current' or 'YYYY-MM' format
        """
        if month_str == "current":
            today = date.today()
            year, month = today.year, today.month
        else:
            try:
                parts = month_str.split("-")
                year, month = int(parts[0]), int(parts[1])
                if not (1 <= month <= 12):
                    raise ValueError(f"Month must be 1-12, got {month}")
            except (ValueError, IndexError):
                print(f"[ERROR] Invalid month format: {month_str}")
                print("        Use YYYY-MM (e.g., 2026-03)")
                return

        gap_data = self._detect_monthly_gaps(year, month)
        month_name = calendar.month_name[month]

        print(f"\n{'=' * 60}")
        print(f"MONTHLY HOURS REPORT - {month_name} {year}")
        print(f"{'=' * 60}\n")

        print(f"  {'Date':<12} {'Day':<10} {'Logged':>7} {'Expected':>8} {'Status':>10}")
        print(f"  {'-' * 50}")

        for d in gap_data["day_details"]:
            if d["gap"] > 0.5:
                status = f"-{d['gap']:.1f}h"
            elif d["logged"] > d["expected"]:
                over = d["logged"] - d["expected"]
                status = f"+{over:.1f}h"
            else:
                status = "[OK]"
            print(
                f"  {d['date']:<12} {d['day']:<10} "
                f"{d['logged']:>6.1f}h "
                f"{d['expected']:>7.1f}h "
                f"{status:>10}"
            )

        print(f"  {'-' * 50}")
        print(f"  {'TOTAL':<12} {'':10} {gap_data['actual']:>6.1f}h {gap_data['expected']:>7.1f}h")

        shortfall = gap_data["expected"] - gap_data["actual"]
        if shortfall > 0.5:
            print(f"\n  [!] Shortfall: {shortfall:.1f}h across {len(gap_data['gaps'])} day(s)")
            # Save shortfall file so tray app can show Fix option
            self._save_shortfall_data(gap_data)
            print("\n  [->] To fix gaps, close this window and use the tray menu:")
            print(
                "\n       Right-click tray icon"
                "\n         -> Log and Reports"
                "\n           -> Fix Monthly Shortfall"
            )
        else:
            print("\n  [OK] All hours accounted for")
            # Clean up stale shortfall file if no gaps remain
            if SHORTFALL_FILE.exists():
                SHORTFALL_FILE.unlink(missing_ok=True)
                logger.info("Stale shortfall file removed by view_monthly")
        print()

    def fix_shortfall(self):
        """Interactive fix for monthly shortfall gaps."""
        # Always re-detect gaps from Tempo (don't trust stale file)
        today = date.today()
        gap_data = self._detect_monthly_gaps(today.year, today.month)
        gaps = gap_data.get("gaps", [])

        if not gaps:
            print("\n[OK] No shortfall detected. All hours accounted for.")
            # Clean up stale shortfall file if it exists
            if SHORTFALL_FILE.exists():
                SHORTFALL_FILE.unlink(missing_ok=True)
                logger.info("Stale shortfall file removed")
            return

        period = gap_data.get("period", "unknown")
        total_gap = sum(g["gap"] for g in gaps)

        print(f"\n{'=' * 60}")
        print(f"FIX MONTHLY SHORTFALL - {period}")
        print(f"{'=' * 60}\n")
        print(f"  Total shortfall: {total_gap:.1f}h across {len(gaps)} day(s)")
        print()
        print(f"  #   {'Date':<12} {'Day':<10} {'Logged':>7} {'Expected':>8} {'Gap':>6}")
        print(f"  {'-' * 48}")
        for i, g in enumerate(gaps, 1):
            print(
                f"  {i:<3} {g['date']:<12} {g['day']:<10} "
                f"{g['logged']:>6.1f}h "
                f"{g['expected']:>7.1f}h "
                f"{g['gap']:>5.1f}h"
            )

        print()
        print("  Options:")
        print("    A       = Fix ALL gap days")
        print("    1,3,5   = Fix specific days (comma-separated)")
        print("    Q       = Quit without fixing")
        print()

        try:
            choice = input("  Enter choice: ").strip().upper()
        except (EOFError, KeyboardInterrupt):
            print("\n  Cancelled.")
            return

        if choice == "Q" or not choice:
            print("  No changes made.")
            return

        # Determine which days to fix
        if choice == "A":
            to_fix = list(gaps)
        else:
            try:
                indices = [int(x.strip()) for x in choice.split(",")]
                to_fix = []
                for idx in indices:
                    if 1 <= idx <= len(gaps):
                        to_fix.append(gaps[idx - 1])
                    else:
                        print(f"  [!] Ignoring invalid index: {idx}")
            except ValueError:
                print("[ERROR] Invalid input. Use A, Q, or numbers like 1,3,5")
                return

        if not to_fix:
            print("  No valid days selected.")
            return

        print(f"\n  Fixing {len(to_fix)} day(s)...\n")

        fixed_count = 0
        for g in to_fix:
            print(f"  --- Syncing {g['date']} ({g['day']}) ---")
            try:
                self.sync_daily(g["date"])
                fixed_count += 1
                print(f"  [OK] {g['date']} synced\n")
            except Exception as e:
                print(f"  [FAIL] Error syncing {g['date']}: {e}\n")
                logger.error(f"Fix shortfall failed for {g['date']}: {e}", exc_info=True)

        print(f"{'=' * 60}")
        print(f"  Fixed {fixed_count}/{len(to_fix)} days.")

        # Update or remove shortfall file
        if fixed_count == len(gaps):
            if SHORTFALL_FILE.exists():
                SHORTFALL_FILE.unlink(missing_ok=True)
            print("  [OK] All gaps fixed. Shortfall file removed.")
            print("\n  You can now submit your timesheet from the tray menu.")
        elif fixed_count > 0:
            # Partial fix -- re-detect and update
            parts = period.split("-")
            updated = self._detect_monthly_gaps(int(parts[0]), int(parts[1]))
            if updated["gaps"]:
                self._save_shortfall_data(updated)
                remaining = len(updated["gaps"])
                print(f"  [INFO] {remaining} gap(s) remaining. Shortfall file updated.")
            else:
                SHORTFALL_FILE.unlink(missing_ok=True)
                print("  [OK] All gaps now fixed. Shortfall file removed.")
                print("\n  You can now submit your timesheet from the tray menu.")

        print(f"{'=' * 60}\n")

        try:
            input("  Press any key to close...")
        except (EOFError, KeyboardInterrupt):
            pass

    # ------------------------------------------------------------------
    # Date-range backfill
    # ------------------------------------------------------------------

    def backfill_range(self, from_date: str, to_date: str):
        """Backfill worklogs for a date range.

        Iterates through each day in the range, syncing working days
        and skipping non-working days (weekends, holidays, PTO).

        Args:
            from_date: Start date (YYYY-MM-DD).
            to_date: End date (YYYY-MM-DD).
        """
        start = datetime.strptime(from_date, "%Y-%m-%d").date()
        end = datetime.strptime(to_date, "%Y-%m-%d").date()

        if start > end:
            print("[FAIL] --from-date must be before --to-date")
            return

        # Pre-sync health check
        if not self._pre_sync_health_check():
            print("[FAIL] Aborting backfill due to API health check failure.")
            return

        print(f"\n{'=' * 60}")
        print(f"BACKFILL: {from_date} to {to_date}")
        print(f"{'=' * 60}\n")

        total_days = (end - start).days + 1
        synced = 0
        skipped = 0
        failed = 0
        skip_reasons = []

        current = start
        day_num = 0
        while current <= end:
            day_num += 1
            date_str = current.strftime("%Y-%m-%d")
            is_working, reason = self.schedule_mgr.is_working_day(date_str)

            if not is_working:
                print(f"  [{day_num}/{total_days}] {date_str}: SKIP ({reason})")
                skipped += 1
                skip_reasons.append(reason)
                current += timedelta(days=1)
                continue

            print(f"  [{day_num}/{total_days}] {date_str}: Syncing...")
            try:
                self.sync_daily(date_str)
                synced += 1
            except Exception as e:
                print(f"  [{day_num}/{total_days}] {date_str}: FAILED ({e})")
                logger.error(f"Backfill failed for {date_str}: {e}", exc_info=True)
                failed += 1
            current += timedelta(days=1)

        print(f"\n{'=' * 60}")
        print("BACKFILL COMPLETE")
        print(f"  Synced: {synced}/{total_days} days")
        if skipped > 0:
            unique_reasons = ", ".join(sorted(set(skip_reasons)))
            print(f"  Skipped: {skipped} ({unique_reasons})")
        if failed > 0:
            print(f"  Failed: {failed}")
        print(f"{'=' * 60}\n")

    # ------------------------------------------------------------------
    # Approval status tracking
    # ------------------------------------------------------------------

    def check_forge(self):
        """Run Forge migration diagnostic checks.

        Tests all Tempo API endpoints, reports response times and
        headers, and prints migration status.
        """
        import time as _time

        print(f"\n{'=' * 60}")
        print("TEMPO FORGE MIGRATION DIAGNOSTICS")
        print(f"{'=' * 60}\n")

        # 1. Platform detection
        print("[1/4] Detecting Tempo platform...")
        forge_status = self.tempo_client.check_forge_status()
        platform = forge_status["platform"]
        latency = forge_status["latency_ms"]
        healthy = forge_status["healthy"]

        if healthy:
            print(f"  Platform: {platform.upper()}")
            print(f"  Latency:  {latency}ms")
            if forge_status["headers"]:
                print("  Headers:")
                for k, v in forge_status["headers"].items():
                    print(f"    {k}: {v}")
        else:
            print("  [FAIL] Could not reach Tempo API")
            print("  Check your API token and network connectivity.")

        # 2. Endpoint tests
        print("\n[2/4] Testing Tempo API endpoints...")
        today = date.today().strftime("%Y-%m-%d")
        endpoints = [
            (
                "GET /work-attributes",
                f"{self.tempo_client.base_url}/work-attributes",
            ),
            (
                "GET /periods",
                f"{self.tempo_client.base_url}/periods?from={today}&to={today}",
            ),
        ]

        # Add user-specific endpoints if account_id available
        if self.tempo_client.account_id:
            today_obj = date.today()
            today = today_obj.strftime("%Y-%m-%d")
            month_start = today_obj.replace(day=1).strftime("%Y-%m-%d")
            import calendar as _cal

            last_day = _cal.monthrange(today_obj.year, today_obj.month)[1]
            month_end = today_obj.replace(day=last_day).strftime("%Y-%m-%d")
            endpoints.append(
                (
                    "GET /worklogs/user/{id}",
                    f"{self.tempo_client.base_url}/worklogs/user/"
                    f"{self.tempo_client.account_id}"
                    f"?from={today}&to={today}",
                )
            )
            endpoints.append(
                (
                    "GET /timesheet-approvals/user/{id}",
                    f"{self.tempo_client.base_url}"
                    f"/timesheet-approvals/user/"
                    f"{self.tempo_client.account_id}"
                    f"?from={month_start}&to={month_end}",
                )
            )

        for name, url in endpoints:
            try:
                start = _time.monotonic()
                response = self.tempo_client.session.get(url, timeout=15)
                elapsed = int((_time.monotonic() - start) * 1000)
                status = response.status_code
                if status < 400:
                    print(f"  [OK]   {name} -> {status} ({elapsed}ms)")
                else:
                    print(f"  [FAIL] {name} -> {status} ({elapsed}ms)")
            except requests.exceptions.RequestException as e:
                print(f"  [FAIL] {name} -> {e}")

        # 3. Network connectivity
        print("\n[3/4] Checking network connectivity...")
        import socket

        hosts = [
            ("api.tempo.io", 443),
            ("api.atlassian.com", 443),
            ("lmsportal.atlassian.net", 443),
        ]
        for host, port in hosts:
            try:
                start = _time.monotonic()
                sock = socket.create_connection((host, port), timeout=5)
                elapsed = int((_time.monotonic() - start) * 1000)
                sock.close()
                print(f"  [OK]   {host}:{port} ({elapsed}ms)")
            except (TimeoutError, OSError) as e:
                print(f"  [FAIL] {host}:{port} -> {e}")

        # 4. Jira->Tempo sync check (read-only)
        print("\n[4/4] Checking Jira->Tempo sync consistency...")
        today = date.today().strftime("%Y-%m-%d")
        jira_count = 0
        tempo_count = 0
        if self.jira_client:
            try:
                jira_wls = self.jira_client.get_my_worklogs(today, today)
                jira_count = len(jira_wls)
                print(f"  Jira worklogs today:  {jira_count}")
            except Exception as e:
                print(f"  [FAIL] Jira worklog fetch: {e}")
        else:
            print("  [SKIP] No Jira client (non-developer role)")

        if self.tempo_client.account_id:
            try:
                tempo_wls = self.tempo_client.get_user_worklogs(today, today)
                tempo_count = len(tempo_wls)
                print(f"  Tempo worklogs today: {tempo_count}")
            except Exception as e:
                print(f"  [FAIL] Tempo worklog fetch: {e}")

        if self.jira_client and self.tempo_client.account_id:
            if jira_count == tempo_count:
                print("  [OK] Jira and Tempo worklog counts match")
            elif tempo_count >= jira_count:
                print("  [OK] Tempo has >= Jira worklogs (Tempo may include manual entries)")
            else:
                print(
                    f"  [!] Tempo has fewer worklogs than Jira "
                    f"({tempo_count} vs {jira_count}). "
                    "Sync may be delayed."
                )

        # Summary
        print(f"\n{'=' * 60}")
        print("SUMMARY")
        print(f"{'=' * 60}")
        print(f"  Platform:   {platform.upper()}")
        print(f"  API health: {'[OK]' if healthy else '[FAIL]'}")

        delay = self.config.get("tempo", {}).get("forge_sync_delay_seconds", 0)
        if delay:
            print(f"  Sync delay: {delay}s (forge_sync_delay_seconds)")
        else:
            print("  Sync delay: none (set tempo.forge_sync_delay_seconds if needed)")

        if platform == "forge":
            print(
                "\n  [INFO] Tempo is running on Forge. "
                "If you experience issues,\n"
                "  regenerate your API token and check "
                "firewall settings."
            )
        elif platform == "connect":
            print(
                "\n  [INFO] Tempo is still on Connect (legacy). "
                "Migration has not\n"
                "  reached this instance yet. No action needed."
            )
        else:
            print("\n  [!] Could not determine platform. Check API token and connectivity.")
        print()

    def check_approval_status(self, month_str: str = "current"):
        """Check Tempo timesheet approval status for a month.

        Args:
            month_str: Month string 'YYYY-MM' or 'current' for
                       the current month.
        """
        if month_str == "current":
            today = date.today()
            year, month = today.year, today.month
        else:
            try:
                parts = month_str.split("-")
                year, month = int(parts[0]), int(parts[1])
            except (ValueError, IndexError):
                print(f"[FAIL] Invalid month format: '{month_str}'. Use YYYY-MM.")
                return

        from_date = f"{year}-{month:02d}-01"
        last_day = calendar.monthrange(year, month)[1]
        to_date = f"{year}-{month:02d}-{last_day:02d}"

        # Get periods from Tempo
        periods = self.tempo_client.get_timesheet_periods(from_date, to_date)

        print(f"\n{'=' * 60}")
        print(f"APPROVAL STATUS - {year}-{month:02d}")
        print(f"{'=' * 60}\n")

        if not periods:
            print("  No timesheet periods found.")
            print()
            return

        status_map = {
            "OPEN": "OPEN (not submitted)",
            "WAITING_FOR_APPROVAL": "Awaiting approval",
            "APPROVED": "APPROVED",
            "REJECTED": "REJECTED",
        }

        for period in periods:
            status = period.get("status", "UNKNOWN")
            display = status_map.get(status, status)
            period_from = period.get("dateFrom", "?")
            period_to = period.get("dateTo", "?")
            print(f"  Period: {period_from} to {period_to}")
            print(f"  Status: {display}")
            # Show reviewer if available
            reviewer = period.get("reviewer", {})
            reviewer_name = reviewer.get("displayName", "")
            if reviewer_name:
                print(f"  Reviewer: {reviewer_name}")
            print()

    # ------------------------------------------------------------------
    # Weekly verification
    # ------------------------------------------------------------------

    def verify_week(self):
        """Verify and backfill current week (Mon-Fri)."""
        # Pre-sync health check
        if not self._pre_sync_health_check():
            print("[FAIL] Aborting weekly verification due to API health check failure.")
            return

        today = date.today()
        # Calculate Monday of current week
        monday = today - timedelta(days=today.weekday())

        now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n{'=' * 60}")
        print(f"TEMPO WEEKLY VERIFICATION (started {now_ts})")
        print(f"Week of {monday.strftime('%B %d, %Y')}")
        print(f"{'=' * 60}")

        day_results = []
        total_created = 0
        total_added_hours = 0.0

        for i in range(5):  # Mon-Fri
            day = monday + timedelta(days=i)
            day_str = day.strftime("%Y-%m-%d")
            day_name = day.strftime("%A")

            print(f"\n--- [Day {i + 1}/5] {day_name} ({day_str}) ---")

            # Skip future dates
            if day > today:
                print("  [SKIP] Future date")
                day_results.append(
                    {
                        "day_name": day_name,
                        "date": day_str,
                        "status": "[--] Future",
                        "existing_hours": 0.0,
                        "added_hours": 0.0,
                    }
                )
                continue

            # Check if working day
            is_working, reason = self.schedule_mgr.is_working_day(day_str)
            if not is_working:
                # Case 3: PTO/Holiday -- check/log overhead hours
                is_off_day = reason != "Weekend"
                if is_off_day and self._is_overhead_configured() and self.jira_client:
                    result = self._check_day_hours(day_str)
                    daily_secs = int(self.schedule_mgr.daily_hours * 3600)
                    if result["gap_hours"] > 0:
                        print(f"  [INFO] {reason} -- logging overhead hours")
                        oh = self._get_overhead_config()
                        pto_key = oh.get("pto_story_key", "")
                        # Delete partial entries and re-log
                        for wl in result["worklogs"]:
                            self.jira_client.delete_worklog(wl["issue_key"], wl["worklog_id"])
                        created = self._log_overhead_hours(
                            day_str,
                            daily_secs,
                            [{"issue_key": pto_key, "summary": pto_key}] if pto_key else None,
                            "single" if pto_key else None,
                        )
                        added_h = sum(c["time_spent_seconds"] for c in created) / 3600
                        total_created += len(created)
                        total_added_hours += added_h
                        status = f"[+] {reason} (overhead logged)"
                    else:
                        existing_h = result["existing_hours"]
                        status = f"[OK] {reason} ({existing_h:.2f}h)"
                        added_h = 0.0
                    day_results.append(
                        {
                            "day_name": day_name,
                            "date": day_str,
                            "status": status,
                            "existing_hours": result["existing_hours"],
                            "added_hours": added_h,
                        }
                    )
                    continue
                print(f"  [SKIP] {reason}")
                day_results.append(
                    {
                        "day_name": day_name,
                        "date": day_str,
                        "status": f"[--] {reason}",
                        "existing_hours": 0.0,
                        "added_hours": 0.0,
                    }
                )
                continue

            # Check hours for this day
            result = self._check_day_hours(day_str)
            existing_h = result["existing_hours"]
            gap_h = result["gap_hours"]

            if result["worklogs"]:
                print(f"  Existing: {existing_h:.2f}h ({len(result['worklogs'])} worklogs)")
                for wl in result["worklogs"]:
                    wl_h = wl["time_spent_seconds"] / 3600
                    print(f"    - {wl['issue_key']}: {wl_h:.2f}h")

            added_h = 0.0
            status = "[OK] Complete"

            if gap_h > 0:
                print(
                    f"  [!] Gap: {gap_h:.2f}h needed "
                    f"(have {existing_h:.2f}h / "
                    f"{self.schedule_mgr.daily_hours}h)"
                )
                backfill = self._backfill_day(day_str, int(gap_h * 3600), result["existing_keys"])
                added_h = backfill["hours_added"]
                total_created += backfill["created_count"]
                total_added_hours += added_h
                if backfill["created_count"] > 0:
                    status = f"[+] Backfilled ({backfill['method']})"
                else:
                    status = "[!] Gap (no stories found)"
            else:
                print(f"  [OK] Complete ({existing_h:.2f}h / {self.schedule_mgr.daily_hours}h)")

            day_results.append(
                {
                    "day_name": day_name,
                    "date": day_str,
                    "status": status,
                    "existing_hours": existing_h,
                    "added_hours": added_h,
                }
            )

        # Print weekly summary
        print(f"\n{'=' * 60}")
        print("WEEKLY SUMMARY")
        print(f"{'=' * 60}")
        print(f"{'Day':<12} {'Date':<12} {'Status':<28} {'Existing':>8} {'Added':>8}")
        print("-" * 72)

        total_expected = 0.0
        total_actual = 0.0
        for r in day_results:
            print(
                f"{r['day_name']:<12} {r['date']:<12} "
                f"{r['status']:<28} "
                f"{r['existing_hours']:>7.2f}h "
                f"{r['added_hours']:>7.2f}h"
            )
            if "[--]" not in r["status"]:
                total_expected += self.schedule_mgr.daily_hours
                total_actual += r["existing_hours"] + r["added_hours"]

        print("-" * 72)
        working = sum(1 for r in day_results if "[--]" not in r["status"])
        print(
            f"Working days: {working}  |  "
            f"Expected: {total_expected:.2f}h  |  "
            f"Actual: {total_actual:.2f}h"
        )
        if total_created > 0:
            print(
                f"Worklogs created: {total_created}  |  Hours backfilled: {total_added_hours:.2f}h"
            )

        shortfall = total_expected - total_actual
        if shortfall > 0.5:
            print(f"Status: [!] SHORTFALL {shortfall:.2f}h")
            self._send_shortfall_notification(
                "weekly",
                monday.strftime("%Y-%m-%d"),
                (monday + timedelta(days=4)).strftime("%Y-%m-%d"),
                total_expected,
                total_actual,
            )
        else:
            print("Status: [OK] All hours accounted for")

        print(f"{'=' * 60}\n")

    def _check_day_hours(self, target_date: str) -> dict:
        """Check if a day has sufficient hours logged."""
        worklogs = []
        existing_keys = set()
        jira_seconds = 0

        if self.jira_client:
            worklogs = self.jira_client.get_my_worklogs(target_date, target_date)
            jira_seconds = sum(wl["time_spent_seconds"] for wl in worklogs)
            existing_keys = {wl["issue_key"] for wl in worklogs}

        # Tempo is source of truth (catches manual Tempo entries)
        tempo_seconds = 0
        if self.tempo_client.account_id:
            tempo_worklogs = self.tempo_client.get_user_worklogs(target_date, target_date)
            tempo_seconds = sum(twl.get("timeSpentSeconds", 0) for twl in tempo_worklogs)

        # Use higher of Jira vs Tempo (protects against API failure)
        existing_seconds = max(jira_seconds, tempo_seconds)
        existing_hours = existing_seconds / 3600
        expected_seconds = int(self.schedule_mgr.daily_hours * 3600)
        gap_seconds = max(0, expected_seconds - existing_seconds)
        gap_hours = gap_seconds / 3600

        return {
            "existing_hours": existing_hours,
            "gap_hours": gap_hours,
            "worklogs": worklogs,
            "existing_keys": existing_keys,
        }

    def _backfill_day(self, target_date: str, gap_seconds: int, existing_keys: set) -> dict:
        """
        Backfill a day with missing hours using historical stories.

        Finds stories that were in IN DEVELOPMENT / CODE REVIEW on that
        date and distributes gap_seconds across them.
        """
        result = {"created_count": 0, "hours_added": 0.0, "method": "none"}

        if not self.jira_client:
            return result

        # Find stories that were active on that date
        issues = self.jira_client.get_issues_in_status_on_date(
            target_date, statuses=self._get_active_statuses()
        )

        # Filter out already-logged issues
        unlogged = [i for i in issues if i["issue_key"] not in existing_keys]

        if not unlogged:
            # Try overhead stories as fallback (Case 1)
            if self._is_overhead_configured():
                print("  No unlogged stories found -- using overhead stories")
                created = self._log_overhead_hours(target_date, gap_seconds)
                result["created_count"] = len(created)
                result["hours_added"] = sum(c["time_spent_seconds"] for c in created) / 3600
                result["method"] = "overhead"
                return result
            print("  No unlogged stories found for this date")
            return result

        print(f"  Found {len(unlogged)} unlogged story(ies) for {target_date}:")
        for issue in unlogged:
            print(f"    - {issue['issue_key']}: {issue['issue_summary']}")

        # Distribute gap_seconds across unlogged stories
        num = len(unlogged)
        per_ticket = gap_seconds // num
        remainder = gap_seconds - (per_ticket * num)

        for i, issue in enumerate(unlogged):
            ticket_seconds = per_ticket + (remainder if i == num - 1 else 0)
            ticket_hours = ticket_seconds / 3600

            comment = self._generate_work_summary(issue["issue_key"], issue["issue_summary"])
            success = self.jira_client.create_worklog(
                issue_key=issue["issue_key"],
                time_spent_seconds=ticket_seconds,
                started=target_date,
                comment=comment,
            )

            if success:
                print(
                    f"  [{i + 1}/{num}] [OK] Backfilled {ticket_hours:.2f}h on {issue['issue_key']}"
                )
                result["created_count"] += 1
                result["hours_added"] += ticket_hours
            else:
                print(f"  [{i + 1}/{num}] [FAIL] {issue['issue_key']}")

        result["method"] = "stories"
        return result

    def _send_shortfall_notification(
        self, period_type: str, start: str, end: str, expected: float, actual: float
    ):
        """Send shortfall notification via Teams and/or email."""
        shortfall = expected - actual
        notify = self.config.get("notifications", {}).get("notify_on_shortfall", True)
        if not notify:
            return

        title = f"Tempo Hours Shortfall - {period_type.title()}"
        body = (
            f"Period: {start} to {end}\n"
            f"Expected: {expected:.1f}h | "
            f"Actual: {actual:.1f}h | "
            f"Missing: {shortfall:.1f}h"
        )

        print("\n  Sending shortfall notification...")
        # Teams webhook disabled — pending Graph API integration
        # self.notifier.send_teams_notification(title, body, facts)
        self.notifier.send_windows_notification(title, body)
        # Email disabled — Office 365 requires OAuth2 (Basic Auth blocked)
        # self.notifier.send_shortfall_email(title, body, facts)


# ============================================================================
# COMMAND LINE INTERFACE
# ============================================================================


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Tempo Timesheet Automation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python tempo_automation.py                    # Sync today's timesheet
  python tempo_automation.py --submit           # Submit monthly timesheet
  python tempo_automation.py --date 2026-02-01  # Sync specific date
  python tempo_automation.py --setup            # Run setup wizard again
  python tempo_automation.py --verify-week      # Verify & backfill this week
  python tempo_automation.py --show-schedule    # Show current month schedule
  python tempo_automation.py --show-schedule 2026-03  # Show March schedule
  python tempo_automation.py --manage           # Interactive schedule menu
  python tempo_automation.py --add-pto 2026-03-10,2026-03-11
  python tempo_automation.py --remove-pto 2026-03-10
  python tempo_automation.py --add-holiday 2026-04-01
  python tempo_automation.py --remove-holiday 2026-04-01
  python tempo_automation.py --add-workday 2026-03-15
  python tempo_automation.py --remove-workday 2026-03-15
  python tempo_automation.py --select-overhead   # Select overhead stories for PI
  python tempo_automation.py --show-overhead      # Show overhead configuration
  python tempo_automation.py --view-monthly       # Show current month hours
  python tempo_automation.py --view-monthly 2026-01  # Show January hours
  python tempo_automation.py --fix-shortfall      # Fix monthly hour shortfalls
  python tempo_automation.py --backfill --from-date 2026-03-01 --to-date 2026-03-10
  python tempo_automation.py --approval-status    # Current month approval status
  python tempo_automation.py --approval-status 2026-02  # February approval status
  python tempo_automation.py --dry-run             # Preview today's sync (no changes)
  python tempo_automation.py --dry-run --date 2026-03-10  # Preview specific date
  python tempo_automation.py --check-forge          # Run Forge migration diagnostics
        """,
    )

    # Core operations
    parser.add_argument("--submit", action="store_true", help="Submit monthly timesheet")

    def valid_date(s: str) -> str:
        """Validate YYYY-MM-DD date format."""
        try:
            datetime.strptime(s, "%Y-%m-%d")
            return s
        except ValueError:
            raise argparse.ArgumentTypeError(f"Invalid date: '{s}'. Use YYYY-MM-DD format.")

    parser.add_argument("--date", type=valid_date, help="Target date (YYYY-MM-DD)")
    parser.add_argument("--setup", action="store_true", help="Run setup wizard")
    parser.add_argument("--logfile", type=str, help="Also write output to this log file (appends)")
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview worklog operations without making changes"
    )

    # Weekly verification
    parser.add_argument(
        "--verify-week", action="store_true", help="Verify and backfill current week (Mon-Fri)"
    )

    # Schedule management
    parser.add_argument(
        "--show-schedule",
        nargs="?",
        const="current",
        metavar="YYYY-MM",
        help="Show month schedule calendar (default: current month)",
    )
    parser.add_argument(
        "--manage", action="store_true", help="Interactive schedule management menu"
    )

    # PTO management
    parser.add_argument(
        "--add-pto", type=str, metavar="DATES", help="Add PTO day(s), comma-separated (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--remove-pto",
        type=str,
        metavar="DATES",
        help="Remove PTO day(s), comma-separated (YYYY-MM-DD)",
    )

    # Extra holiday management
    parser.add_argument(
        "--add-holiday",
        type=str,
        metavar="DATES",
        help="Add extra holiday(s), comma-separated (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--remove-holiday",
        type=str,
        metavar="DATES",
        help="Remove extra holiday(s), comma-separated (YYYY-MM-DD)",
    )

    # Compensatory working day management
    parser.add_argument(
        "--add-workday",
        type=str,
        metavar="DATES",
        help="Add compensatory working day(s), comma-separated",
    )
    parser.add_argument(
        "--remove-workday",
        type=str,
        metavar="DATES",
        help="Remove compensatory working day(s), comma-separated",
    )

    # Overhead story management
    parser.add_argument(
        "--select-overhead", action="store_true", help="Select overhead stories for current PI"
    )
    parser.add_argument(
        "--show-overhead", action="store_true", help="Show current overhead story configuration"
    )

    # Monthly reporting / shortfall fix
    parser.add_argument(
        "--view-monthly",
        nargs="?",
        const="current",
        metavar="YYYY-MM",
        help="Show per-day hours for a month (default: current)",
    )
    parser.add_argument(
        "--fix-shortfall", action="store_true", help="Interactive fix for monthly hour shortfalls"
    )

    # Date-range backfill
    parser.add_argument(
        "--backfill", action="store_true", help="Backfill worklogs for a date range"
    )
    parser.add_argument("--from-date", type=valid_date, help="Start date for backfill (YYYY-MM-DD)")
    parser.add_argument("--to-date", type=valid_date, help="End date for backfill (YYYY-MM-DD)")

    # Approval status
    parser.add_argument(
        "--approval-status",
        nargs="?",
        const="current",
        metavar="YYYY-MM",
        help="Check Tempo timesheet approval status",
    )

    # Forge migration diagnostics
    parser.add_argument(
        "--check-forge", action="store_true", help="Run Tempo Forge migration diagnostics"
    )

    # Output format
    parser.add_argument(
        "--log-format",
        type=str,
        choices=["text", "json"],
        default="text",
        help="Log output format (default: text)",
    )

    args = parser.parse_args()

    # Set up dual output if --logfile is provided
    if args.logfile:
        sys.stdout = DualWriter(sys.stdout, args.logfile)

    # Apply JSON log format if requested
    if args.log_format == "json":
        json_formatter = JsonLogFormatter()
        for handler in logging.getLogger().handlers:
            handler.setFormatter(json_formatter)

    # Suppress console INFO logs for user-facing commands -- the
    # initialization messages (config loaded, holidays parsed, etc.)
    # are noise when the user just wants to see a calendar or report.
    quiet_console = (
        args.show_schedule is not None
        or args.view_monthly is not None
        or args.show_overhead
        or args.select_overhead
        or args.fix_shortfall
        or args.add_pto
        or args.remove_pto
        or args.add_holiday
        or args.remove_holiday
        or args.add_workday
        or args.remove_workday
        or args.manage
        or args.dry_run
        or args.approval_status is not None
        or args.check_forge
    )
    if quiet_console:
        for h in logging.getLogger().handlers:
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
                h.setLevel(logging.WARNING)

    try:
        # Run setup if requested
        if args.setup:
            config_manager = ConfigManager.__new__(ConfigManager)
            config_manager.config_path = CONFIG_FILE
            config = config_manager.setup_wizard()
            if config is None:
                raise SystemExit(1)
            config_manager.config = config
            return

        # Schedule management commands that only need ScheduleManager
        # (no full automation init required)
        schedule_cmds = [
            args.show_schedule,
            args.manage,
            args.add_pto,
            args.remove_pto,
            args.add_holiday,
            args.remove_holiday,
            args.add_workday,
            args.remove_workday,
        ]
        if any(cmd is not None and cmd is not False for cmd in schedule_cmds):
            config_mgr = ConfigManager()
            schedule_mgr = ScheduleManager(config_mgr.config)

            if args.show_schedule is not None:
                schedule_mgr.print_month_calendar(args.show_schedule)
            elif args.manage:
                schedule_mgr.interactive_menu()
            elif args.add_pto:
                dates = [d.strip() for d in args.add_pto.split(",")]
                print("Adding PTO day(s):")
                schedule_mgr.add_pto(dates)
            elif args.remove_pto:
                dates = [d.strip() for d in args.remove_pto.split(",")]
                print("Removing PTO day(s):")
                schedule_mgr.remove_pto(dates)
            elif args.add_holiday:
                dates = [d.strip() for d in args.add_holiday.split(",")]
                print("Adding extra holiday(s):")
                schedule_mgr.add_extra_holidays(dates)
            elif args.remove_holiday:
                dates = [d.strip() for d in args.remove_holiday.split(",")]
                print("Removing extra holiday(s):")
                schedule_mgr.remove_extra_holidays(dates)
            elif args.add_workday:
                dates = [d.strip() for d in args.add_workday.split(",")]
                print("Adding compensatory working day(s):")
                schedule_mgr.add_working_days(dates)
            elif args.remove_workday:
                dates = [d.strip() for d in args.remove_workday.split(",")]
                print("Removing compensatory working day(s):")
                schedule_mgr.remove_working_days(dates)
            return

        # Initialize full automation
        automation = TempoAutomation(dry_run=getattr(args, "dry_run", False))

        # Overhead management
        if args.select_overhead:
            automation.select_overhead_stories()
        elif args.show_overhead:
            automation.show_overhead_config()
        # Monthly hours / shortfall
        elif args.view_monthly is not None:
            automation.view_monthly_hours(args.view_monthly)
        elif args.fix_shortfall:
            automation.fix_shortfall()
        # Backfill date range
        elif args.backfill:
            if not args.from_date or not args.to_date:
                print("[FAIL] --backfill requires both --from-date and --to-date")
                sys.exit(1)
            automation.backfill_range(args.from_date, args.to_date)
        # Approval status
        elif args.approval_status is not None:
            automation.check_approval_status(args.approval_status)
        # Forge migration diagnostics
        elif args.check_forge:
            automation.check_forge()
        # Submit timesheet
        elif args.submit:
            automation.submit_timesheet()
        # Weekly verification
        elif args.verify_week:
            automation.verify_week()
        # Daily sync (default)
        else:
            automation.sync_daily(args.date)

    except KeyboardInterrupt:
        print("\n\nOperation cancelled by user.")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        print(f"\n[ERROR] {e}")
        print(f"See {LOG_FILE} for details")
        sys.exit(1)


if __name__ == "__main__":
    main()
