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

import os
import sys
import io
import json
import logging
import argparse
import calendar
from datetime import datetime, timedelta, date
from pathlib import Path
import requests
from typing import Dict, List, Optional, Tuple
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Force UTF-8 output to avoid UnicodeEncodeError on Windows when redirecting to file.
# Under pythonw.exe (no console), sys.stdout/stderr are None -- redirect to devnull.
if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w', encoding='utf-8')
elif sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w', encoding='utf-8')
elif sys.stderr.encoding != 'utf-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')


class DualWriter:
    """Writes to both the console (original stdout) and an external log file."""

    def __init__(self, console, logfile_path: str):
        self.console = console
        self.logfile = open(logfile_path, 'a', encoding='utf-8')

    def write(self, text):
        self.console.write(text)
        self.logfile.write(text)
        self.logfile.flush()

    def flush(self):
        self.console.flush()
        self.logfile.flush()

    def close(self):
        self.logfile.close()

# ============================================================================
# CONFIGURATION
# ============================================================================

# Script directory
SCRIPT_DIR = Path(__file__).parent
CONFIG_FILE = SCRIPT_DIR / "config.json"
LOG_FILE = SCRIPT_DIR / "tempo_automation.log"

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


# ============================================================================
# CREDENTIAL MANAGER (DPAPI encryption for Windows)
# ============================================================================

class CredentialManager:
    """Encrypt/decrypt sensitive config values using Windows DPAPI.

    Encrypted values are stored as 'ENC:<base64>' in config.json.
    Plain-text values are accepted for backward compatibility and
    will be returned as-is by decrypt().
    DPAPI ties encryption to the current Windows user account —
    the encrypted value can only be decrypted by the same user
    on the same machine.
    """

    PREFIX = "ENC:"

    @staticmethod
    def encrypt(plain_text: str) -> str:
        """Encrypt a string using Windows DPAPI.

        Returns 'ENC:<base64>' on Windows, plain text otherwise.
        """
        if not plain_text:
            return plain_text
        if sys.platform != 'win32':
            return plain_text

        try:
            import ctypes
            import ctypes.wintypes as wt

            class BLOB(ctypes.Structure):
                _fields_ = [
                    ("cbData", wt.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_byte)),
                ]

            raw = plain_text.encode('utf-8')
            inp = BLOB()
            inp.cbData = len(raw)
            inp.pbData = (ctypes.c_byte * len(raw))(*raw)

            out = BLOB()
            if ctypes.windll.crypt32.CryptProtectData(
                ctypes.byref(inp), None, None, None, None,
                0, ctypes.byref(out)
            ):
                enc = bytes(
                    (ctypes.c_byte * out.cbData)
                    .from_address(
                        ctypes.addressof(out.pbData.contents)
                    )
                )
                ctypes.windll.kernel32.LocalFree(out.pbData)
                import base64
                return (
                    f"{CredentialManager.PREFIX}"
                    f"{base64.b64encode(enc).decode('ascii')}"
                )
            return plain_text
        except Exception as e:
            logger.warning(f"DPAPI encrypt failed: {e}")
            return plain_text

    @staticmethod
    def decrypt(value: str) -> str:
        """Decrypt an 'ENC:<base64>' string using Windows DPAPI.

        Returns plain text. If value is not encrypted (no ENC:
        prefix), returns it unchanged.
        """
        if not value or not value.startswith(CredentialManager.PREFIX):
            return value
        if sys.platform != 'win32':
            logger.warning("Cannot decrypt DPAPI value on non-Windows")
            return value

        try:
            import ctypes
            import ctypes.wintypes as wt
            import base64

            class BLOB(ctypes.Structure):
                _fields_ = [
                    ("cbData", wt.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_byte)),
                ]

            raw = base64.b64decode(
                value[len(CredentialManager.PREFIX):]
            )
            inp = BLOB()
            inp.cbData = len(raw)
            inp.pbData = (ctypes.c_byte * len(raw))(*raw)

            out = BLOB()
            if ctypes.windll.crypt32.CryptUnprotectData(
                ctypes.byref(inp), None, None, None, None,
                0, ctypes.byref(out)
            ):
                dec = bytes(
                    (ctypes.c_byte * out.cbData)
                    .from_address(
                        ctypes.addressof(out.pbData.contents)
                    )
                )
                ctypes.windll.kernel32.LocalFree(out.pbData)
                return dec.decode('utf-8')
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
    
    def load_config(self) -> Dict:
        """Load configuration from file or create new one."""
        if not self.config_path.exists():
            logger.info("No configuration found. Starting setup wizard...")
            return self.setup_wizard()
        
        try:
            with open(self.config_path, 'r') as f:
                config = json.load(f)
            logger.info("Configuration loaded successfully")
            return config
        except Exception as e:
            logger.error(f"Error loading configuration: {e}")
            raise
    
    def setup_wizard(self) -> Dict:
        """Interactive setup wizard for first-time configuration."""
        print("\n" + "="*60)
        print("TEMPO AUTOMATION - FIRST TIME SETUP")
        print("="*60)
        print("\nThis wizard will help you set up the automation.")
        print("Your credentials will be stored locally and encrypted.\n")
        
        # User information
        print("--- USER INFORMATION ---")
        user_email = input("Enter your email address: ").strip()
        user_name = input("Enter your full name: ").strip()
        user_role = self._select_role()
        
        # Jira/Tempo configuration
        print("\n--- JIRA/TEMPO CONFIGURATION ---")
        jira_url = input("Enter your Jira URL (e.g., yourcompany.atlassian.net): ").strip()
        jira_url = jira_url.replace('https://', '').replace('http://', '')
        
        print("\n[INFO] To get your Tempo API token:")
        print("   1. Go to https://app.tempo.io/")
        print("   2. Settings -> API Integration")
        print("   3. Click 'New Token'")
        tempo_token = input("\nEnter your Tempo API token: ").strip()
        
        if user_role == "developer":
            print("\n[INFO] To get your Jira API token:")
            print("   1. Go to https://id.atlassian.com/manage-profile/security/api-tokens")
            print("   2. Click 'Create API token'")
            jira_token = input("\nEnter your Jira API token: ").strip()
            jira_email = input("Enter your Jira account email: ").strip()
        else:
            jira_token = ""
            jira_email = ""
        
        # Work schedule & location
        print("\n--- WORK SCHEDULE & LOCATION ---")
        daily_hours = float(
            input("Standard work hours per day (default 8): ").strip()
            or "8"
        )

        country_code, state_code = self._select_location()

        # Organization holidays URL
        print("\n--- ORGANIZATION HOLIDAYS ---")
        print(
            "Enter the URL for your org's central holiday file "
            "(or Enter to skip):"
        )
        holidays_url = input("Holidays URL: ").strip()

        # Teams webhook for notifications (disabled — pending Graph API)
        # print("\n--- MS TEAMS NOTIFICATIONS (OPTIONAL) ---")
        # print("Enter your MS Teams incoming webhook URL (or Enter to skip):")
        # print("[INFO] To create a webhook: Teams channel -> ...")
        # teams_webhook = input("Teams Webhook URL: ").strip()
        teams_webhook = ""

        # Email notifications (Office 365 SMTP)
        print("\n--- EMAIL NOTIFICATIONS ---")
        print("SMTP server: smtp.office365.com (auto-configured)")
        enable_email = input(
            "Enable email notifications? (yes/no, default: yes): "
        ).strip().lower()
        enable_email = enable_email in ['yes', 'y', '']

        smtp_server = "smtp.office365.com"
        smtp_port = 587
        smtp_user = user_email
        smtp_password = ""
        if enable_email:
            print(
                f"\nSMTP login will use your email: {user_email}"
            )
            print(
                "[INFO] If MFA is enabled, create an App Password at"
            )
            print(
                "   https://mysignins.microsoft.com/security-info"
            )
            raw_password = input(
                "Enter your email/app password: "
            ).strip()
            smtp_password = CredentialManager.encrypt(raw_password)
            print("[OK] Password encrypted and saved securely")
        
        # Manual activities (for non-developers)
        manual_activities = []
        if user_role in ["product_owner", "sales"]:
            print("\n--- DEFAULT ACTIVITIES ---")
            print("Set up your typical daily activities (optional)")
            while True:
                add_activity = input("\nAdd a default activity? (yes/no): ").strip().lower()
                if add_activity not in ['yes', 'y']:
                    break
                
                activity = input("Activity name (e.g., 'Stakeholder Meetings'): ").strip()
                hours = float(input("Typical hours per day: ").strip())
                manual_activities.append({
                    "activity": activity,
                    "hours": hours
                })
        
        # Build configuration
        config = {
            "user": {
                "email": user_email,
                "name": user_name,
                "role": user_role
            },
            "jira": {
                "url": jira_url,
                "email": jira_email,
                "api_token": jira_token
            },
            "tempo": {
                "api_token": tempo_token
            },
            "organization": {
                "holidays_url": holidays_url
            },
            "schedule": {
                "daily_hours": daily_hours,
                "daily_sync_time": "18:00",
                "monthly_submit_day": "last",
                "country_code": country_code,
                "state": state_code,
                "pto_days": [],
                "extra_holidays": [],
                "working_days": []
            },
            "notifications": {
                "email_enabled": enable_email,
                "smtp_server": smtp_server,
                "smtp_port": smtp_port,
                "smtp_user": smtp_user,
                "smtp_password": smtp_password,
                "notification_email": user_email,
                "teams_webhook_url": teams_webhook,
                "notify_on_shortfall": True
            },
            "manual_activities": manual_activities,
            "options": {
                "auto_submit": True,
                "require_confirmation": False,
                "sync_on_startup": False
            }
        }
        
        # Save configuration
        self.save_config(config)
        
        print("\n" + "="*60)
        print("[OK] SETUP COMPLETE!")
        print("="*60)
        print(f"\nConfiguration saved to: {self.config_path}")
        print("You can edit this file manually if needed.\n")
        
        return config
    
    def _select_role(self) -> str:
        """Helper to select user role."""
        print("\nSelect your role:")
        print("  1. Developer (works with Jira tickets)")
        print("  2. Product Owner")
        print("  3. Sales Team")
        
        while True:
            choice = input("Enter choice (1-3): ").strip()
            if choice == "1":
                return "developer"
            elif choice == "2":
                return "product_owner"
            elif choice == "3":
                return "sales"
            else:
                print("Invalid choice. Please enter 1, 2, or 3.")
    
    def _select_location(self) -> Tuple[str, str]:
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
                cc = input(
                    "Enter ISO country code (e.g., US, IN, GB): "
                ).strip().upper()
                st = input(
                    "Enter state/province code (or Enter to skip): "
                ).strip().upper()
                return cc, st
            else:
                print("Invalid choice. Please enter 1-5.")

    def save_config(self, config: Dict):
        """Save configuration to file."""
        try:
            with open(self.config_path, 'w') as f:
                json.dump(config, f, indent=2)
            logger.info("Configuration saved successfully")
        except Exception as e:
            logger.error(f"Error saving configuration: {e}")
            raise
    
    def get_account_id(self) -> str:
        """Get Tempo account ID for current user."""
        try:
            url = "https://api.tempo.io/4/user"
            headers = {
                'Authorization': f"Bearer {self.config['tempo']['api_token']}"
            }
            
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            
            user_data = response.json()
            account_id = user_data.get('accountId')
            
            if account_id:
                logger.info(f"Retrieved Tempo account ID: {account_id}")
                return account_id
            else:
                logger.warning("Account ID not found in Tempo response, using email")
                return self.config['user']['email']
                
        except Exception as e:
            logger.error(f"Error fetching Tempo account ID: {e}")
            logger.error(f"Falling back to email as account ID")
            return self.config['user']['email']


# ============================================================================
# SCHEDULE MANAGER
# ============================================================================

ORG_HOLIDAYS_FILE = SCRIPT_DIR / "org_holidays.json"


class ScheduleManager:
    """Manages working days, holidays, PTO, and schedule overrides."""

    def __init__(self, config: Dict):
        self.config = config
        self.schedule = config.get('schedule', {})
        self.daily_hours = self.schedule.get('daily_hours', 8)
        self.country_code = self.schedule.get('country_code', 'US')
        self.state = self.schedule.get('state', '')
        self.pto_days = set(self.schedule.get('pto_days', []))
        self.extra_holidays = set(self.schedule.get('extra_holidays', []))
        self.working_days = set(self.schedule.get('working_days', []))
        self._org_holidays_data = {}
        self._org_holidays = {}  # flat {date_str: name}
        self._country_holidays = None
        self._load_org_holidays()
        self._load_country_holidays()

    # ------------------------------------------------------------------
    # Holiday loading
    # ------------------------------------------------------------------

    def _load_org_holidays(self):
        """Load org holidays from local file, auto-fetch from URL if configured."""
        # Try auto-fetch first
        self._fetch_remote_org_holidays()

        # Load local file
        if ORG_HOLIDAYS_FILE.exists():
            try:
                with open(ORG_HOLIDAYS_FILE, 'r') as f:
                    self._org_holidays_data = json.load(f)
                logger.info("Org holidays loaded from local file")
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
        holidays_data = self._org_holidays_data.get('holidays', {})
        country_data = holidays_data.get(self.country_code, {})

        # Load current year and next year (for year-end boundary)
        today = date.today()
        for year in [str(today.year), str(today.year + 1)]:
            year_data = country_data.get(year, {})

            # Common holidays (apply to all in this country)
            common = year_data.get('common', [])
            for h in common:
                self._org_holidays[h['date']] = h['name']

            # State-specific holidays
            if self.state:
                state_holidays = year_data.get(self.state, [])
                for h in state_holidays:
                    self._org_holidays[h['date']] = h['name']

        count = len(self._org_holidays)
        logger.info(
            f"Parsed {count} org holidays for "
            f"{self.country_code}/{self.state or 'all'}"
        )

    def _fetch_remote_org_holidays(self):
        """Fetch org_holidays.json from central URL if version is newer."""
        holidays_url = self.config.get(
            'organization', {}
        ).get('holidays_url', '')
        if not holidays_url:
            return

        try:
            response = requests.get(holidays_url, timeout=10)
            response.raise_for_status()
            remote_data = response.json()
            remote_version = remote_data.get('version', '')

            # Load local version for comparison
            local_version = ''
            if ORG_HOLIDAYS_FILE.exists():
                with open(ORG_HOLIDAYS_FILE, 'r') as f:
                    local_data = json.load(f)
                    local_version = local_data.get('version', '')

            if remote_version != local_version:
                with open(ORG_HOLIDAYS_FILE, 'w') as f:
                    json.dump(remote_data, f, indent=2)
                logger.info(
                    f"Org holidays updated: {local_version} -> "
                    f"{remote_version}"
                )
                print(
                    f"[INFO] Org holidays updated to version "
                    f"{remote_version}"
                )
            else:
                logger.debug(
                    f"Org holidays up to date: {local_version}"
                )
        except Exception as e:
            logger.warning(f"Could not fetch remote org holidays: {e}")

    def _load_country_holidays(self):
        """Load country holidays from holidays library."""
        try:
            import holidays as holidays_lib
            state_arg = self.state if self.state else None
            self._country_holidays = holidays_lib.country_holidays(
                self.country_code, state=state_arg
            )
            logger.info(
                f"Country holidays loaded: {self.country_code}"
                f"/{state_arg or 'national'}"
            )
        except ImportError:
            logger.warning(
                "holidays library not installed. "
                "Install with: pip install holidays"
            )
            self._country_holidays = None
        except Exception as e:
            logger.warning(f"Could not load country holidays: {e}")
            self._country_holidays = None

    # ------------------------------------------------------------------
    # Day classification
    # ------------------------------------------------------------------

    def is_working_day(self, target_date: str) -> Tuple[bool, str]:
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
        dt = datetime.strptime(target_date, '%Y-%m-%d').date()

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
            return False, f"Extra holiday"

        # 7. Default -- working day
        return True, "Working day"

    def get_holiday_name(self, target_date: str) -> Optional[str]:
        """Get the holiday name for a date, or None if not a holiday."""
        if target_date in self._org_holidays:
            return self._org_holidays[target_date]
        dt = datetime.strptime(target_date, '%Y-%m-%d').date()
        if self._country_holidays is not None and dt in self._country_holidays:
            return self._country_holidays.get(dt)
        return None

    def count_working_days(self, start_date: str, end_date: str) -> int:
        """Count working days in a date range (inclusive)."""
        start = datetime.strptime(start_date, '%Y-%m-%d').date()
        end = datetime.strptime(end_date, '%Y-%m-%d').date()
        count = 0
        current = start
        while current <= end:
            is_working, _ = self.is_working_day(
                current.strftime('%Y-%m-%d')
            )
            if is_working:
                count += 1
            current += timedelta(days=1)
        return count

    def get_expected_hours(self, start_date: str, end_date: str) -> float:
        """Calculate expected hours for a date range."""
        return self.count_working_days(start_date, end_date) * self.daily_hours

    def check_year_end_warning(self) -> Optional[str]:
        """Warn if next year's holiday data is missing in December."""
        today = date.today()
        if today.month != 12:
            return None
        next_year = str(today.year + 1)
        holidays_data = self._org_holidays_data.get('holidays', {})
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

    def get_month_calendar(self, year: int, month: int) -> List[Dict]:
        """Generate calendar data for a month."""
        _, num_days = calendar.monthrange(year, month)
        days = []
        for day in range(1, num_days + 1):
            dt = date(year, month, day)
            date_str = dt.strftime('%Y-%m-%d')
            is_working, reason = self.is_working_day(date_str)
            # Determine display status
            if date_str in self.working_days:
                status = 'comp_working'
                label = 'CW'
            elif date_str in self.pto_days:
                status = 'pto'
                label = 'PTO'
            elif dt.weekday() >= 5:
                status = 'weekend'
                label = '.'
            elif not is_working:
                status = 'holiday'
                label = 'H'
            else:
                status = 'working'
                label = 'W'
            days.append({
                'date': date_str,
                'day': day,
                'weekday': dt.weekday(),
                'day_name': dt.strftime('%A'),
                'status': status,
                'label': label,
                'reason': reason
            })
        return days

    def print_month_calendar(self, month_str: str = 'current'):
        """Print month calendar with day classifications."""
        if month_str == 'current':
            today = date.today()
            year, month = today.year, today.month
        else:
            try:
                parts = month_str.split('-')
                year, month = int(parts[0]), int(parts[1])
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
        first_weekday = days[0]['weekday']
        line_dates = '     ' * first_weekday
        line_labels = '     ' * first_weekday

        holidays_list = []
        pto_list = []
        cw_list = []
        working_count = 0

        for day_info in days:
            wd = day_info['weekday']
            day_num = day_info['day']
            label = day_info['label']

            # Separator before Sat column
            if wd == 5:
                line_dates += '| '
                line_labels += '| '

            line_dates += f"{day_num:>2}   "
            line_labels += f"{label:>2}   "

            # Track stats
            if day_info['status'] == 'working':
                working_count += 1
            elif day_info['status'] == 'comp_working':
                working_count += 1
                cw_list.append(day_info)
            elif day_info['status'] == 'holiday':
                holidays_list.append(day_info)
            elif day_info['status'] == 'pto':
                pto_list.append(day_info)

            # End of week (Sunday) or last day
            if wd == 6 or day_info == days[-1]:
                print(line_dates.rstrip())
                print(line_labels.rstrip())
                line_dates = ''
                line_labels = ''

        print()
        print(
            "Legend: W=Working  H=Holiday  PTO=PTO  "
            "CW=Comp. Working  .=Weekend"
        )
        print()
        expected = working_count * self.daily_hours
        print(f"Summary:")
        print(
            f"  Working days: {working_count}  |  "
            f"Expected hours: {expected:.1f}h"
        )
        if holidays_list:
            names = [
                f"{h['reason'].replace('Holiday: ', '')} - "
                f"{month_name[:3]} {h['day']}"
                for h in holidays_list
            ]
            print(f"  Holidays: {len(holidays_list)} ({', '.join(names)})")
        if pto_list:
            pto_dates = [str(p['day']) for p in pto_list]
            print(
                f"  PTO days: {len(pto_list)} "
                f"({month_name[:3]} {', '.join(pto_dates)})"
            )
        if cw_list:
            cw_dates = [str(c['day']) for c in cw_list]
            print(
                f"  Comp. working days: {len(cw_list)} "
                f"({month_name[:3]} {', '.join(cw_dates)})"
            )
        print()

    # ------------------------------------------------------------------
    # Config modification (PTO, holidays, working days)
    # ------------------------------------------------------------------

    def add_pto(self, dates: List[str]) -> Tuple[List[str], List[str]]:
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
            dt = datetime.strptime(d, '%Y-%m-%d').date()
            if dt.weekday() >= 5:
                day_name = dt.strftime('%A')
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

    def remove_pto(self, dates: List[str]) -> List[str]:
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

    def add_extra_holidays(self, dates: List[str]) -> List[str]:
        """Add extra holiday dates to config."""
        added = []
        for d in dates:
            d = d.strip()
            if not self._validate_date(d):
                continue
            if d not in self.extra_holidays:
                self.extra_holidays.add(d)
                dt = datetime.strptime(d, '%Y-%m-%d').date()
                print(f"  [OK] {d} ({dt.strftime('%A')})")
                added.append(d)
            else:
                print(f"  [SKIP] {d} already in extra holidays")
        if added:
            self._save_schedule_to_config()
            print(
                f"\n[OK] Added {len(added)} extra holiday(s). Config saved."
            )
        return added

    def remove_extra_holidays(self, dates: List[str]) -> List[str]:
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
            print(
                f"\n[OK] Removed {len(removed)} extra holiday(s). "
                f"Config saved."
            )
        return removed

    def add_working_days(self, dates: List[str]) -> List[str]:
        """Add compensatory working day dates to config."""
        added = []
        for d in dates:
            d = d.strip()
            if not self._validate_date(d):
                continue
            if d not in self.working_days:
                self.working_days.add(d)
                dt = datetime.strptime(d, '%Y-%m-%d').date()
                print(f"  [OK] {d} ({dt.strftime('%A')})")
                added.append(d)
            else:
                print(f"  [SKIP] {d} already in working days")
        if added:
            self._save_schedule_to_config()
            print(
                f"\n[OK] Added {len(added)} working day(s). Config saved."
            )
        return added

    def remove_working_days(self, dates: List[str]) -> List[str]:
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
            print(
                f"\n[OK] Removed {len(removed)} working day(s). "
                f"Config saved."
            )
        return removed

    def _save_schedule_to_config(self):
        """Persist schedule changes back to config.json."""
        self.config.setdefault('schedule', {})
        self.config['schedule']['pto_days'] = sorted(self.pto_days)
        self.config['schedule']['extra_holidays'] = sorted(
            self.extra_holidays
        )
        self.config['schedule']['working_days'] = sorted(self.working_days)
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(self.config, f, indent=2)
            logger.info("Schedule config saved")
        except Exception as e:
            logger.error(f"Error saving schedule config: {e}")

    def _validate_date(self, date_str: str) -> bool:
        """Validate date string format and allowed characters."""
        import re
        if not re.match(r'^[\d\-]+$', date_str):
            print(
                f"  [ERROR] Invalid characters in: {date_str} "
                f"(only digits and '-' allowed)"
            )
            return False
        try:
            datetime.strptime(date_str, '%Y-%m-%d')
            return True
        except ValueError:
            print(
                f"  [ERROR] Invalid date: {date_str} "
                f"(use YYYY-MM-DD format)"
            )
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

            if choice == '0':
                break
            elif choice == '1':
                raw = input(
                    "Enter date(s) (YYYY-MM-DD, comma-separated): "
                ).strip()
                dates = [d.strip() for d in raw.split(',') if d.strip()]
                self.add_pto(dates)
            elif choice == '2':
                raw = input(
                    "Enter date(s) to remove (YYYY-MM-DD, "
                    "comma-separated): "
                ).strip()
                dates = [d.strip() for d in raw.split(',') if d.strip()]
                self.remove_pto(dates)
            elif choice == '3':
                raw = input(
                    "Enter date(s) (YYYY-MM-DD, comma-separated): "
                ).strip()
                dates = [d.strip() for d in raw.split(',') if d.strip()]
                self.add_extra_holidays(dates)
            elif choice == '4':
                raw = input(
                    "Enter date(s) to remove (YYYY-MM-DD, "
                    "comma-separated): "
                ).strip()
                dates = [d.strip() for d in raw.split(',') if d.strip()]
                self.remove_extra_holidays(dates)
            elif choice == '5':
                raw = input(
                    "Enter date(s) (YYYY-MM-DD, comma-separated): "
                ).strip()
                dates = [d.strip() for d in raw.split(',') if d.strip()]
                self.add_working_days(dates)
            elif choice == '6':
                raw = input(
                    "Enter date(s) to remove (YYYY-MM-DD, "
                    "comma-separated): "
                ).strip()
                dates = [d.strip() for d in raw.split(',') if d.strip()]
                self.remove_working_days(dates)
            elif choice == '7':
                raw = input(
                    "Month (YYYY-MM, or Enter for current): "
                ).strip()
                self.print_month_calendar(raw or 'current')
            elif choice == '8':
                self._list_dates("PTO days", self.pto_days)
            elif choice == '9':
                self._list_dates("Extra holidays", self.extra_holidays)
            elif choice == '10':
                self._list_dates(
                    "Compensatory working days", self.working_days
                )
            else:
                print("[ERROR] Invalid choice. Enter 0-10.")

    def _list_dates(self, label: str, date_set: set):
        """Print a sorted list of dates."""
        if not date_set:
            print(f"\n{label}: (none)")
            return
        print(f"\n{label}:")
        for d in sorted(date_set):
            dt = datetime.strptime(d, '%Y-%m-%d').date()
            print(f"  {d} ({dt.strftime('%A')})")
        print(f"Total: {len(date_set)}")

    # ------------------------------------------------------------------
    # Locations (for setup wizard)
    # ------------------------------------------------------------------

    def get_locations(self) -> Dict:
        """Return locations map from org_holidays.json."""
        return self._org_holidays_data.get('locations', {})


# ============================================================================
# JIRA API CLIENT
# ============================================================================

class JiraClient:
    """Handles Jira API interactions."""
    
    def __init__(self, config: Dict):
        self.config = config
        self.base_url = f"https://{config['jira']['url']}"
        self.email = config['jira']['email']
        self.api_token = config['jira']['api_token']
        self.session = requests.Session()
        self.session.auth = (self.email, self.api_token)
        self.session.headers.update({'Content-Type': 'application/json'})
    
    def get_my_worklogs(self, date_from: str, date_to: str) -> List[Dict]:
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
            params = {
                'jql': jql,
                'fields': 'worklog,summary,key',
                'maxResults': 100
            }

            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()

            issues = response.json().get('issues', [])
            
            worklogs = []
            for issue in issues:
                issue_key = issue['key']
                issue_summary = issue['fields']['summary']
                
                # Get worklogs for this issue
                worklog_url = f"{self.base_url}/rest/api/3/issue/{issue_key}/worklog"
                worklog_response = self.session.get(worklog_url)
                worklog_response.raise_for_status()
                
                issue_worklogs = worklog_response.json().get('worklogs', [])
                
                # Filter worklogs by date and current user
                for wl in issue_worklogs:
                    started = wl['started'][:10]  # Extract date part
                    if date_from <= started <= date_to:
                        if wl['author']['emailAddress'] == self.email:
                            worklogs.append({
                                'worklog_id': wl['id'],
                                'issue_key': issue_key,
                                'issue_summary': issue_summary,
                                'time_spent_seconds': wl['timeSpentSeconds'],
                                'started': started,
                                'comment': wl.get('comment', '')
                            })
            
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

    def get_my_active_issues(self) -> List[Dict]:
        """
        Fetch issues assigned to current user that are In Progress or Code Review.

        Returns:
            List of dicts with issue_key and issue_summary
        """
        try:
            jql = 'assignee = currentUser() AND status IN ("IN DEVELOPMENT", "CODE REVIEW")'

            url = f"{self.base_url}/rest/api/3/search/jql"
            params = {
                'jql': jql,
                'fields': 'summary',
                'maxResults': 50
            }

            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()

            issues = response.json().get('issues', [])

            result = []
            for issue in issues:
                result.append({
                    'issue_key': issue['key'],
                    'issue_summary': issue['fields']['summary']
                })

            logger.info(f"Found {len(result)} active issues assigned to current user")
            return result

        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching active issues: {e}")
            return []

    def get_issues_in_status_on_date(self, target_date: str) -> List[Dict]:
        """
        Fetch issues that were IN DEVELOPMENT or CODE REVIEW on a past date.

        Uses historical JQL (status WAS ... ON ...) to find tickets that
        were active on a specific date, even if their status has since changed.

        Args:
            target_date: Date string (YYYY-MM-DD)

        Returns:
            List of dicts with issue_key and issue_summary
        """
        try:
            jql = (
                f'assignee = currentUser() AND ('
                f'status WAS "IN DEVELOPMENT" ON "{target_date}" OR '
                f'status WAS "CODE REVIEW" ON "{target_date}"'
                f')'
            )

            url = f"{self.base_url}/rest/api/3/search/jql"
            params = {
                'jql': jql,
                'fields': 'summary',
                'maxResults': 50
            }

            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()

            issues = response.json().get('issues', [])

            result = []
            for issue in issues:
                result.append({
                    'issue_key': issue['key'],
                    'issue_summary': issue['fields']['summary']
                })

            logger.info(
                f"Found {len(result)} issues in status on {target_date}"
            )
            return result

        except requests.exceptions.RequestException as e:
            logger.error(
                f"Error fetching historical issues for {target_date}: {e}"
            )
            return []

    def get_issue_details(self, issue_key: str) -> Optional[Dict]:
        """
        Fetch issue description and recent comments.

        Args:
            issue_key: Jira issue key (e.g., "TS-1234")

        Returns:
            Dict with summary, description_text, and recent_comments, or None on failure
        """
        try:
            url = f"{self.base_url}/rest/api/3/issue/{issue_key}"
            params = {'fields': 'summary,description,comment'}

            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()

            data = response.json()
            fields = data.get('fields', {})

            # Extract plain text from ADF description
            description_text = self._extract_adf_text(fields.get('description'))

            # Extract recent comments (latest 3, most recent first)
            comment_body = fields.get('comment', {})
            raw_comments = comment_body.get('comments', [])
            recent_comments = []
            for c in raw_comments[-3:]:
                text = self._extract_adf_text(c.get('body'))
                if text:
                    recent_comments.append(text)

            return {
                'summary': fields.get('summary', ''),
                'description_text': description_text,
                'recent_comments': recent_comments
            }

        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching issue details for {issue_key}: {e}")
            return None

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
                if node.get('type') == 'text':
                    parts.append(node.get('text', ''))
                for child in node.get('content', []):
                    _walk(child)
            elif isinstance(node, list):
                for item in node:
                    _walk(item)

        _walk(adf_content)
        return ' '.join(parts).strip()

    def create_worklog(self, issue_key: str, time_spent_seconds: int,
                       started: str, comment: str = "") -> bool:
        """
        Create a worklog on a Jira issue.

        Args:
            issue_key: Jira issue key (e.g., "TS-1234")
            time_spent_seconds: Time spent in seconds
            started: Date string (YYYY-MM-DD), will be formatted to ISO datetime
            comment: Optional comment text

        Returns:
            True if successful, False otherwise
        """
        try:
            url = f"{self.base_url}/rest/api/3/issue/{issue_key}/worklog"

            # Format started as ISO datetime (Jira v3 requires full datetime)
            started_dt = f"{started}T09:00:00.000+0000"

            payload = {
                'timeSpentSeconds': time_spent_seconds,
                'started': started_dt,
            }

            # Jira v3 API requires comment in ADF (Atlassian Document Format)
            # Each line becomes its own paragraph for multi-line descriptions
            if comment:
                paragraphs = []
                for line in comment.split('\n'):
                    line = line.strip()
                    if line:
                        paragraphs.append({
                            'type': 'paragraph',
                            'content': [
                                {'type': 'text', 'text': line}
                            ]
                        })
                if paragraphs:
                    payload['comment'] = {
                        'type': 'doc',
                        'version': 1,
                        'content': paragraphs
                    }

            response = self.session.post(url, json=payload, timeout=30)
            response.raise_for_status()

            logger.info(f"Created Jira worklog: {issue_key} - {time_spent_seconds}s on {started}")
            return True

        except requests.exceptions.RequestException as e:
            logger.error(f"Error creating Jira worklog for {issue_key}: {e}")
            return False


# ============================================================================
# TEMPO API CLIENT
# ============================================================================

class TempoClient:
    """Handles Tempo API interactions."""
    
    def __init__(self, config: Dict):
        self.config = config
        self.api_token = config['tempo']['api_token']
        self.base_url = "https://api.tempo.io/4"
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {self.api_token}',
            'Content-Type': 'application/json'
        })
    
    def get_user_worklogs(self, date_from: str, date_to: str) -> List[Dict]:
        """
        Fetch Tempo worklogs for current user.
        
        Args:
            date_from: Start date (YYYY-MM-DD)
            date_to: End date (YYYY-MM-DD)
        
        Returns:
            List of worklog dictionaries
        """
        try:
            url = f"{self.base_url}/worklogs/user/{self.config['user']['email']}"
            params = {
                'from': date_from,
                'to': date_to
            }
            
            response = self.session.get(url, params=params)
            response.raise_for_status()
            
            worklogs = response.json().get('results', [])
            logger.info(f"Fetched {len(worklogs)} worklogs from Tempo")
            return worklogs
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching Tempo worklogs: {e}")
            return []
    
    def create_worklog(self, issue_key: str, time_seconds: int, 
                       start_date: str, description: str = "") -> bool:
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
                'issueKey': issue_key,
                'timeSpentSeconds': time_seconds,
                'startDate': start_date,
                'startTime': '09:00:00',
                'authorAccountId': self.config['user']['email'],
                'description': description
            }
            
            response = self.session.post(url, json=data)
            response.raise_for_status()
            
            logger.info(f"Created Tempo worklog: {issue_key} - {time_seconds}s on {start_date}")
            return True
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error creating Tempo worklog: {e}")
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
            
            data = {
                'worker': {
                    'accountId': self.config['user']['email']
                },
                'period': {
                    'key': period_key
                }
            }
            
            response = self.session.post(url, json=data)
            response.raise_for_status()
            
            logger.info(f"Successfully submitted timesheet for period: {period_key}")
            return True
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error submitting timesheet: {e}")
            return False
    
    def _get_current_period(self) -> str:
        """Get current timesheet period key."""
        try:
            url = "https://api.tempo.io/4/timesheet-approvals/periods"
            
            # Get current date to find matching period
            today = date.today().strftime('%Y-%m-%d')
            
            response = self.session.get(url)
            response.raise_for_status()
            
            periods = response.json().get('results', [])
            
            # Find period containing today's date
            for period in periods:
                period_from = period.get('dateFrom')
                period_to = period.get('dateTo')
                
                if period_from and period_to:
                    if period_from <= today <= period_to:
                        period_key = period.get('key')
                        logger.info(f"Found current period: {period_key}")
                        return period_key
            
            # Fallback to simplified format
            logger.warning("No period found in API, using simplified format")
            today_obj = date.today()
            return f"{today_obj.year}-{today_obj.month:02d}"
            
        except Exception as e:
            logger.error(f"Error fetching Tempo period: {e}")
            # Fallback to simplified format
            today_obj = date.today()
            return f"{today_obj.year}-{today_obj.month:02d}"


# ============================================================================
# NOTIFICATION MANAGER
# ============================================================================

class NotificationManager:
    """Handles email notifications."""
    
    def __init__(self, config: Dict):
        self.config = config
        self.enabled = config['notifications']['email_enabled']
    
    def send_daily_summary(self, worklogs: List[Dict], total_hours: float):
        """Send daily timesheet summary email."""
        if not self.enabled:
            return
        
        subject = f"Tempo Daily Summary - {date.today().strftime('%Y-%m-%d')}"
        
        body = f"""
        <html>
        <body>
        <h2>Daily Tempo Timesheet Summary</h2>
        <p><strong>Date:</strong> {date.today().strftime('%B %d, %Y')}</p>
        <p><strong>Total Hours Logged:</strong> {total_hours:.2f} / {self.config['schedule']['daily_hours']}</p>
        
        <h3>Entries:</h3>
        <ul>
        """
        
        for wl in worklogs:
            hours = wl['time_spent_seconds'] / 3600
            body += f"<li>{wl['issue_key']}: {hours:.2f}h - {wl.get('issue_summary', '')}</li>\n"
        
        status = "[OK] Complete" if total_hours >= self.config['schedule']['daily_hours'] else "[!] Incomplete"
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
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = self.config['notifications']['smtp_user']
            msg['To'] = self.config['notifications']['notification_email']
            
            msg.attach(MIMEText(body, 'html'))
            
            server = smtplib.SMTP(
                self.config['notifications']['smtp_server'],
                self.config['notifications']['smtp_port']
            )
            server.starttls()
            smtp_password = CredentialManager.decrypt(
                self.config['notifications'].get('smtp_password', '')
            )
            server.login(
                self.config['notifications']['smtp_user'],
                smtp_password
            )
            server.send_message(msg)
            server.quit()
            
            logger.info(f"Email sent: {subject}")
            
        except Exception as e:
            logger.error(f"Error sending email: {e}")

    def send_teams_notification(self, title: str, body: str,
                                facts: List[Dict] = None):
        """
        Send notification to MS Teams via incoming webhook.

        Uses Adaptive Card format for rich display.
        Silently skips if webhook URL not configured.
        """
        webhook_url = self.config.get(
            'notifications', {}
        ).get('teams_webhook_url', '')
        if not webhook_url:
            logger.info("Teams webhook not configured, skipping")
            return

        try:
            card_body = [
                {
                    "type": "TextBlock",
                    "text": title,
                    "weight": "Bolder",
                    "size": "Medium"
                },
                {
                    "type": "TextBlock",
                    "text": body,
                    "wrap": True
                }
            ]

            if facts:
                fact_set = {
                    "type": "FactSet",
                    "facts": [
                        {"title": f["title"], "value": f["value"]}
                        for f in facts
                    ]
                }
                card_body.append(fact_set)

            payload = {
                "type": "message",
                "attachments": [{
                    "contentType":
                        "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "$schema":
                            "http://adaptivecards.io/schemas/"
                            "adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.4",
                        "body": card_body
                    }
                }]
            }

            response = requests.post(
                webhook_url, json=payload, timeout=30
            )
            response.raise_for_status()
            logger.info(f"Teams notification sent: {title}")
            print("  [OK] Teams notification sent")

        except Exception as e:
            logger.error(f"Error sending Teams notification: {e}")
            print(f"  [!] Teams notification failed: {e}")

    def send_windows_notification(self, title: str, body: str):
        """Show a Windows toast notification (Action Center)."""
        if sys.platform != 'win32':
            return
        try:
            from winotify import Notification, audio
            toast = Notification(
                app_id="Tempo Automation",
                title=title,
                msg=body,
                duration="long"
            )
            toast.set_audio(audio.Default, loop=False)
            toast.show()
            logger.info(f"Toast notification shown: {title}")
            print("  [OK] Desktop notification sent")
        except ImportError:
            # Fallback to MessageBox if winotify not installed
            try:
                from ctypes import windll, c_int, c_wchar_p
                windll.user32.MessageBoxW(
                    c_int(0), c_wchar_p(body), c_wchar_p(title),
                    0x00000030 | 0x00001000
                )
                logger.info(f"MessageBox notification shown: {title}")
                print("  [OK] Desktop notification shown")
            except Exception as e2:
                logger.warning(f"Notification failed: {e2}")
        except Exception as e:
            logger.warning(f"Toast notification failed: {e}")

    def send_shortfall_email(self, title: str, body: str,
                             facts: List[Dict] = None):
        """Send shortfall notification via email."""
        if not self.enabled:
            return
        facts_html = ""
        if facts:
            rows = "".join(
                f"<tr><td><strong>{f['title']}</strong></td>"
                f"<td>{f['value']}</td></tr>"
                for f in facts
            )
            facts_html = (
                f"<table border='1' cellpadding='6' "
                f"cellspacing='0'>{rows}</table>"
            )
        html_body = f"""
        <html><body>
        <h2>[!] {title}</h2>
        <p>{body.replace(chr(10), '<br>')}</p>
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
    
    def __init__(self, config_path: Path = CONFIG_FILE):
        self.config_manager = ConfigManager(config_path)
        self.config = self.config_manager.config
        self.schedule_mgr = ScheduleManager(self.config)

        self.jira_client = None
        if self.config.get('user', {}).get('role') == 'developer':
            self.jira_client = JiraClient(self.config)

        self.tempo_client = TempoClient(self.config)
        self.notifier = NotificationManager(self.config)

        # Check for year-end holiday warnings
        self.schedule_mgr.check_year_end_warning()

    def sync_daily(self, target_date: str = None):
        """
        Sync daily timesheet entries.

        Args:
            target_date: Date to sync (YYYY-MM-DD), defaults to today
        """
        if not target_date:
            target_date = date.today().strftime('%Y-%m-%d')

        # --- Schedule guard ---
        is_working, reason = self.schedule_mgr.is_working_day(target_date)
        if not is_working:
            print(f"\n[SKIP] {target_date} is not a working day: {reason}")
            print(
                "       Use --add-workday to override if this day "
                "should be worked."
            )
            logger.info(f"Skipped {target_date}: {reason}")
            return
        # --- End guard ---

        logger.info(f"Starting daily sync for {target_date}")
        now_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"\n{'='*60}")
        print(f"TEMPO DAILY SYNC - {target_date} (started {now_ts})")
        print(f"{'='*60}\n")
        
        worklogs_created = []
        
        if self.config['user']['role'] == 'developer':
            # Auto-log time across active Jira tickets
            worklogs_created = self._auto_log_jira_worklogs(target_date)
        else:
            # Use manual configuration
            worklogs_created = self._sync_manual_activities(target_date)
        
        # Calculate total hours
        total_hours = sum(wl['time_spent_seconds'] for wl in worklogs_created) / 3600
        
        # Send notification
        self.notifier.send_daily_summary(worklogs_created, total_hours)
        
        # Print summary
        done_ts = datetime.now().strftime('%H:%M:%S')
        print(f"\n{'='*60}")
        print(f"[OK] SYNC COMPLETE ({done_ts})")
        print(f"{'='*60}")
        print(f"Total entries: {len(worklogs_created)}")
        print(f"Total hours: {total_hours:.2f} / {self.config['schedule']['daily_hours']}")

        if total_hours >= self.config['schedule']['daily_hours']:
            print("Status: [OK] Complete")
        else:
            print(f"Status: [!] Incomplete ({total_hours:.2f}h logged)")
        print()
        
        logger.info(f"Daily sync completed: {len(worklogs_created)} entries, {total_hours:.2f}h")
    
    def _sync_jira_worklogs(self, target_date: str) -> List[Dict]:
        """Sync Jira worklogs to Tempo."""
        # Fetch Jira worklogs
        jira_worklogs = self.jira_client.get_my_worklogs(target_date, target_date)
        
        if not jira_worklogs:
            logger.info("No Jira worklogs found for today")
            return []
        
        # Check which ones already exist in Tempo
        tempo_worklogs = self.tempo_client.get_user_worklogs(target_date, target_date)
        tempo_issue_keys = {wl.get('issue', {}).get('key') for wl in tempo_worklogs}
        
        # Create missing entries
        created = []
        for wl in jira_worklogs:
            if wl['issue_key'] not in tempo_issue_keys:
                success = self.tempo_client.create_worklog(
                    issue_key=wl['issue_key'],
                    time_seconds=wl['time_spent_seconds'],
                    start_date=target_date,
                    description=wl.get('comment', '')
                )
                
                if success:
                    print(f"  [OK] Created: {wl['issue_key']} - {wl['time_spent_seconds']/3600:.2f}h")
                    created.append(wl)
                else:
                    print(f"  [FAIL] {wl['issue_key']}")
            else:
                print(f"  [SKIP] Exists: {wl['issue_key']}")
        
        return created

    def _auto_log_jira_worklogs(self, target_date: str) -> List[Dict]:
        """
        Auto-log worklogs by distributing daily hours across active Jira tickets.

        Finds all IN DEVELOPMENT / CODE REVIEW tickets assigned to the current user
        and splits the configured daily hours equally across them.
        """
        # Delete existing worklogs for target date first
        existing = self.jira_client.get_my_worklogs(target_date, target_date)
        if existing:
            print(f"Removing {len(existing)} existing worklog(s) for {target_date}...")
            for wl in existing:
                deleted = self.jira_client.delete_worklog(wl['issue_key'], wl['worklog_id'])
                if deleted:
                    print(f"  [OK] Removed {wl['time_spent_seconds']/3600:.2f}h from {wl['issue_key']}")
                else:
                    print(f"  [FAIL] Could not remove worklog from {wl['issue_key']}")
            print()

        active_issues = self.jira_client.get_my_active_issues()

        if not active_issues:
            logger.warning("No active issues found (IN DEVELOPMENT / CODE REVIEW)")
            print("[!] No active tickets found. Make sure you have tickets IN DEVELOPMENT or CODE REVIEW.")
            return []

        daily_hours = self.config.get('schedule', {}).get('daily_hours', 8)
        total_seconds = int(daily_hours * 3600)
        num_tickets = len(active_issues)
        seconds_per_ticket = total_seconds // num_tickets
        # Give remainder to the last ticket so total is exactly daily_hours
        remainder_seconds = total_seconds - (seconds_per_ticket * num_tickets)

        print(f"Found {num_tickets} active ticket(s):")
        for issue in active_issues:
            print(f"  - {issue['issue_key']}: {issue['issue_summary']}")
        print(f"\n{daily_hours}h / {num_tickets} tickets = {seconds_per_ticket/3600:.2f}h each\n")

        created = []
        for i, issue in enumerate(active_issues):
            # Last ticket gets the remainder so total adds up exactly
            ticket_seconds = seconds_per_ticket + (remainder_seconds if i == num_tickets - 1 else 0)
            ticket_hours = ticket_seconds / 3600

            # Generate a meaningful description from ticket content
            comment = self._generate_work_summary(issue['issue_key'], issue['issue_summary'])
            success = self.jira_client.create_worklog(
                issue_key=issue['issue_key'],
                time_spent_seconds=ticket_seconds,
                started=target_date,
                comment=comment
            )

            if success:
                print(f"  [OK] Logged {ticket_hours:.2f}h on {issue['issue_key']}")
                print(f"    Description: {comment[:80]}{'...' if len(comment) > 80 else ''}")
                created.append({
                    'issue_key': issue['issue_key'],
                    'issue_summary': issue['issue_summary'],
                    'time_spent_seconds': ticket_seconds
                })
            else:
                print(f"  [FAIL] {issue['issue_key']}")

        return created

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
        desc = details.get('description_text', '')
        if desc:
            # Take the first sentence or up to 120 chars from description
            first_sentence = desc.split('.')[0].strip()
            if len(first_sentence) > 120:
                first_sentence = first_sentence[:117] + '...'
            lines.append(first_sentence)
        else:
            lines.append(issue_summary)

        # Lines 2-3: What was actually done (from recent comments)
        comments = details.get('recent_comments', [])
        for c in reversed(comments):  # most recent first
            # Take first meaningful line from the comment
            c_line = c.split('\n')[0].strip()
            if c_line and len(c_line) > 5:
                if len(c_line) > 120:
                    c_line = c_line[:117] + '...'
                lines.append(c_line)
            if len(lines) >= 3:
                break

        return '\n'.join(lines)

    def _sync_manual_activities(self, target_date: str) -> List[Dict]:
        """Sync manual activities from configuration."""
        manual_activities = self.config.get('manual_activities', [])
        
        if not manual_activities:
            logger.warning("No manual activities configured")
            print("[!] No manual activities configured. Please edit config.json")
            return []
        
        # Check existing entries
        tempo_worklogs = self.tempo_client.get_user_worklogs(target_date, target_date)
        
        if tempo_worklogs:
            logger.info("Manual entries already exist for today")
            print("[SKIP] Timesheet entries already exist for today")
            return tempo_worklogs
        
        # Create entries from configuration
        created = []
        for activity in manual_activities:
            # Get issue key from config, or use default
            # Ask your Jira admin what issue key to use for general time tracking
            issue_key = self.config.get('organization', {}).get('default_issue_key', 'GENERAL-001')
            
            time_seconds = int(activity['hours'] * 3600)
            
            success = self.tempo_client.create_worklog(
                issue_key=issue_key,
                time_seconds=time_seconds,
                start_date=target_date,
                description=activity['activity']
            )
            
            if success:
                print(f"  [OK] Created: {activity['activity']} - {activity['hours']}h")
                created.append({
                    'issue_key': issue_key,
                    'issue_summary': activity['activity'],
                    'time_spent_seconds': time_seconds
                })
        
        return created
    
    def submit_timesheet(self):
        """Submit monthly timesheet with hours verification."""
        today = date.today()
        last_day = calendar.monthrange(today.year, today.month)[1]

        if today.day != last_day:
            logger.info(
                f"Skipping submission -- today is {today}, "
                f"last day is {today.replace(day=last_day)}"
            )
            print(
                f"[SKIP] Not the last day of the month "
                f"({today.day}/{last_day}). Skipping submission."
            )
            return

        logger.info("Starting timesheet submission")
        now_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"\n{'='*60}")
        print(f"TEMPO MONTHLY TIMESHEET SUBMISSION ({now_ts})")
        print(f"{'='*60}\n")

        # --- Monthly hours verification ---
        first_day_str = today.replace(day=1).strftime('%Y-%m-%d')
        last_day_str = today.strftime('%Y-%m-%d')
        working_days = self.schedule_mgr.count_working_days(
            first_day_str, last_day_str
        )
        expected = self.schedule_mgr.get_expected_hours(
            first_day_str, last_day_str
        )

        # Get actual hours from Jira
        actual = 0.0
        if self.jira_client:
            worklogs = self.jira_client.get_my_worklogs(
                first_day_str, last_day_str
            )
            actual = sum(
                wl['time_spent_seconds'] for wl in worklogs
            ) / 3600

        print("Monthly Hours Check:")
        print(
            f"  Expected: {expected:.1f}h "
            f"({working_days} working days x "
            f"{self.schedule_mgr.daily_hours}h)"
        )
        print(f"  Actual:   {actual:.1f}h")

        shortfall = expected - actual
        if shortfall > 0.5:
            print(f"  [!] SHORTFALL: {shortfall:.1f}h missing")
            self._send_shortfall_notification(
                'monthly', first_day_str, last_day_str,
                expected, actual
            )
        else:
            print("  [OK] Hours complete")
        print()

        # --- Submit ---
        period = f"{today.year}-{today.month:02d}"
        success = self.tempo_client.submit_timesheet(period)

        if success:
            print(f"[OK] Timesheet submitted successfully for {period}")
            self.notifier.send_submission_confirmation(period)
        else:
            print(f"[FAIL] Failed to submit timesheet for {period}")

        print()
        logger.info(
            f"Timesheet submission "
            f"{'successful' if success else 'failed'}"
        )

    # ------------------------------------------------------------------
    # Weekly verification
    # ------------------------------------------------------------------

    def verify_week(self):
        """Verify and backfill current week (Mon-Fri)."""
        today = date.today()
        # Calculate Monday of current week
        monday = today - timedelta(days=today.weekday())

        now_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"\n{'='*60}")
        print(f"TEMPO WEEKLY VERIFICATION (started {now_ts})")
        print(f"Week of {monday.strftime('%B %d, %Y')}")
        print(f"{'='*60}")

        day_results = []
        total_created = 0
        total_added_hours = 0.0

        for i in range(5):  # Mon-Fri
            day = monday + timedelta(days=i)
            day_str = day.strftime('%Y-%m-%d')
            day_name = day.strftime('%A')

            print(f"\n--- {day_name} ({day_str}) ---")

            # Skip future dates
            if day > today:
                print("  [SKIP] Future date")
                day_results.append({
                    'day_name': day_name,
                    'date': day_str,
                    'status': '[--] Future',
                    'existing_hours': 0.0,
                    'added_hours': 0.0
                })
                continue

            # Check if working day
            is_working, reason = self.schedule_mgr.is_working_day(day_str)
            if not is_working:
                print(f"  [SKIP] {reason}")
                day_results.append({
                    'day_name': day_name,
                    'date': day_str,
                    'status': f'[--] {reason}',
                    'existing_hours': 0.0,
                    'added_hours': 0.0
                })
                continue

            # Check hours for this day
            result = self._check_day_hours(day_str)
            existing_h = result['existing_hours']
            gap_h = result['gap_hours']

            if result['worklogs']:
                print(f"  Existing: {existing_h:.2f}h "
                      f"({len(result['worklogs'])} worklogs)")
                for wl in result['worklogs']:
                    wl_h = wl['time_spent_seconds'] / 3600
                    print(f"    - {wl['issue_key']}: {wl_h:.2f}h")

            added_h = 0.0
            status = '[OK] Complete'

            if gap_h > 0:
                print(
                    f"  [!] Gap: {gap_h:.2f}h needed "
                    f"(have {existing_h:.2f}h / "
                    f"{self.schedule_mgr.daily_hours}h)"
                )
                backfill = self._backfill_day(
                    day_str,
                    int(gap_h * 3600),
                    result['existing_keys']
                )
                added_h = backfill['hours_added']
                total_created += backfill['created_count']
                total_added_hours += added_h
                if backfill['created_count'] > 0:
                    status = f"[+] Backfilled ({backfill['method']})"
                else:
                    status = '[!] Gap (no stories found)'
            else:
                print(
                    f"  [OK] Complete "
                    f"({existing_h:.2f}h / "
                    f"{self.schedule_mgr.daily_hours}h)"
                )

            day_results.append({
                'day_name': day_name,
                'date': day_str,
                'status': status,
                'existing_hours': existing_h,
                'added_hours': added_h
            })

        # Print weekly summary
        print(f"\n{'='*60}")
        print("WEEKLY SUMMARY")
        print(f"{'='*60}")
        print(
            f"{'Day':<12} {'Date':<12} {'Status':<28} "
            f"{'Existing':>8} {'Added':>8}"
        )
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
            if '[--]' not in r['status']:
                total_expected += self.schedule_mgr.daily_hours
                total_actual += r['existing_hours'] + r['added_hours']

        print("-" * 72)
        working = sum(
            1 for r in day_results if '[--]' not in r['status']
        )
        print(
            f"Working days: {working}  |  "
            f"Expected: {total_expected:.2f}h  |  "
            f"Actual: {total_actual:.2f}h"
        )
        if total_created > 0:
            print(
                f"Worklogs created: {total_created}  |  "
                f"Hours backfilled: {total_added_hours:.2f}h"
            )

        shortfall = total_expected - total_actual
        if shortfall > 0.5:
            print(f"Status: [!] SHORTFALL {shortfall:.2f}h")
            self._send_shortfall_notification(
                'weekly',
                monday.strftime('%Y-%m-%d'),
                (monday + timedelta(days=4)).strftime('%Y-%m-%d'),
                total_expected, total_actual
            )
        else:
            print("Status: [OK] All hours accounted for")

        print(f"{'='*60}\n")

    def _check_day_hours(self, target_date: str) -> Dict:
        """Check if a day has sufficient hours logged."""
        worklogs = []
        existing_keys = set()
        existing_seconds = 0

        if self.jira_client:
            worklogs = self.jira_client.get_my_worklogs(
                target_date, target_date
            )
            existing_seconds = sum(
                wl['time_spent_seconds'] for wl in worklogs
            )
            existing_keys = {wl['issue_key'] for wl in worklogs}

        existing_hours = existing_seconds / 3600
        expected_seconds = int(self.schedule_mgr.daily_hours * 3600)
        gap_seconds = max(0, expected_seconds - existing_seconds)
        gap_hours = gap_seconds / 3600

        return {
            'existing_hours': existing_hours,
            'gap_hours': gap_hours,
            'worklogs': worklogs,
            'existing_keys': existing_keys
        }

    def _backfill_day(self, target_date: str, gap_seconds: int,
                      existing_keys: set) -> Dict:
        """
        Backfill a day with missing hours using historical stories.

        Finds stories that were in IN DEVELOPMENT / CODE REVIEW on that
        date and distributes gap_seconds across them.
        """
        result = {
            'created_count': 0,
            'hours_added': 0.0,
            'method': 'none'
        }

        if not self.jira_client:
            return result

        # Find stories that were active on that date
        issues = self.jira_client.get_issues_in_status_on_date(
            target_date
        )

        # Filter out already-logged issues
        unlogged = [
            i for i in issues if i['issue_key'] not in existing_keys
        ]

        if not unlogged:
            print("  No unlogged stories found for this date")
            return result

        print(
            f"  Found {len(unlogged)} unlogged story(ies) "
            f"for {target_date}:"
        )
        for issue in unlogged:
            print(
                f"    - {issue['issue_key']}: "
                f"{issue['issue_summary']}"
            )

        # Distribute gap_seconds across unlogged stories
        num = len(unlogged)
        per_ticket = gap_seconds // num
        remainder = gap_seconds - (per_ticket * num)

        for i, issue in enumerate(unlogged):
            ticket_seconds = per_ticket + (
                remainder if i == num - 1 else 0
            )
            ticket_hours = ticket_seconds / 3600

            comment = self._generate_work_summary(
                issue['issue_key'], issue['issue_summary']
            )
            success = self.jira_client.create_worklog(
                issue_key=issue['issue_key'],
                time_spent_seconds=ticket_seconds,
                started=target_date,
                comment=comment
            )

            if success:
                print(
                    f"  [OK] Backfilled {ticket_hours:.2f}h on "
                    f"{issue['issue_key']}"
                )
                result['created_count'] += 1
                result['hours_added'] += ticket_hours
            else:
                print(f"  [FAIL] {issue['issue_key']}")

        result['method'] = 'stories'
        return result

    def _send_shortfall_notification(self, period_type: str,
                                     start: str, end: str,
                                     expected: float, actual: float):
        """Send shortfall notification via Teams and/or email."""
        shortfall = expected - actual
        notify = self.config.get(
            'notifications', {}
        ).get('notify_on_shortfall', True)
        if not notify:
            return

        title = f"Tempo Hours Shortfall - {period_type.title()}"
        body = (
            f"Period: {start} to {end}\n"
            f"Expected: {expected:.1f}h | "
            f"Actual: {actual:.1f}h | "
            f"Missing: {shortfall:.1f}h"
        )
        facts = [
            {"title": "Period", "value": f"{start} to {end}"},
            {"title": "Expected", "value": f"{expected:.1f}h"},
            {"title": "Actual", "value": f"{actual:.1f}h"},
            {"title": "Shortfall", "value": f"{shortfall:.1f}h"}
        ]

        print(f"\n  Sending shortfall notification...")
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
        description='Tempo Timesheet Automation',
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
        """
    )

    # Core operations
    parser.add_argument(
        '--submit', action='store_true',
        help='Submit monthly timesheet'
    )
    parser.add_argument(
        '--date', type=str,
        help='Target date (YYYY-MM-DD)'
    )
    parser.add_argument(
        '--setup', action='store_true',
        help='Run setup wizard'
    )
    parser.add_argument(
        '--logfile', type=str,
        help='Also write output to this log file (appends)'
    )

    # Weekly verification
    parser.add_argument(
        '--verify-week', action='store_true',
        help='Verify and backfill current week (Mon-Fri)'
    )

    # Schedule management
    parser.add_argument(
        '--show-schedule', nargs='?', const='current',
        metavar='YYYY-MM',
        help='Show month schedule calendar (default: current month)'
    )
    parser.add_argument(
        '--manage', action='store_true',
        help='Interactive schedule management menu'
    )

    # PTO management
    parser.add_argument(
        '--add-pto', type=str, metavar='DATES',
        help='Add PTO day(s), comma-separated (YYYY-MM-DD)'
    )
    parser.add_argument(
        '--remove-pto', type=str, metavar='DATES',
        help='Remove PTO day(s), comma-separated (YYYY-MM-DD)'
    )

    # Extra holiday management
    parser.add_argument(
        '--add-holiday', type=str, metavar='DATES',
        help='Add extra holiday(s), comma-separated (YYYY-MM-DD)'
    )
    parser.add_argument(
        '--remove-holiday', type=str, metavar='DATES',
        help='Remove extra holiday(s), comma-separated (YYYY-MM-DD)'
    )

    # Compensatory working day management
    parser.add_argument(
        '--add-workday', type=str, metavar='DATES',
        help='Add compensatory working day(s), comma-separated'
    )
    parser.add_argument(
        '--remove-workday', type=str, metavar='DATES',
        help='Remove compensatory working day(s), comma-separated'
    )

    args = parser.parse_args()

    # Set up dual output if --logfile is provided
    if args.logfile:
        sys.stdout = DualWriter(sys.stdout, args.logfile)

    try:
        # Run setup if requested
        if args.setup:
            config_manager = ConfigManager()
            config_manager.setup_wizard()
            return

        # Schedule management commands that only need ScheduleManager
        # (no full automation init required)
        schedule_cmds = [
            args.show_schedule, args.manage, args.add_pto,
            args.remove_pto, args.add_holiday, args.remove_holiday,
            args.add_workday, args.remove_workday
        ]
        if any(cmd is not None and cmd is not False for cmd in schedule_cmds):
            config_mgr = ConfigManager()
            schedule_mgr = ScheduleManager(config_mgr.config)

            if args.show_schedule is not None:
                schedule_mgr.print_month_calendar(args.show_schedule)
            elif args.manage:
                schedule_mgr.interactive_menu()
            elif args.add_pto:
                dates = [d.strip() for d in args.add_pto.split(',')]
                print("Adding PTO day(s):")
                schedule_mgr.add_pto(dates)
            elif args.remove_pto:
                dates = [d.strip() for d in args.remove_pto.split(',')]
                print("Removing PTO day(s):")
                schedule_mgr.remove_pto(dates)
            elif args.add_holiday:
                dates = [d.strip() for d in args.add_holiday.split(',')]
                print("Adding extra holiday(s):")
                schedule_mgr.add_extra_holidays(dates)
            elif args.remove_holiday:
                dates = [
                    d.strip() for d in args.remove_holiday.split(',')
                ]
                print("Removing extra holiday(s):")
                schedule_mgr.remove_extra_holidays(dates)
            elif args.add_workday:
                dates = [d.strip() for d in args.add_workday.split(',')]
                print("Adding compensatory working day(s):")
                schedule_mgr.add_working_days(dates)
            elif args.remove_workday:
                dates = [
                    d.strip() for d in args.remove_workday.split(',')
                ]
                print("Removing compensatory working day(s):")
                schedule_mgr.remove_working_days(dates)
            return

        # Initialize full automation
        automation = TempoAutomation()

        # Submit timesheet
        if args.submit:
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
