"""
Date and config edge case tests.

Verifies correct behavior at date boundaries (leap years, year/month
boundaries), unusual config values, and CLI input validation.

Tests (8):
- test_leap_year_feb_29
- test_year_boundary_dec_31_to_jan_1
- test_month_boundary_last_day
- test_future_date_sync
- test_invalid_date_format_cli
- test_config_missing_overhead_section
- test_config_daily_hours_zero
- test_config_pto_duplicate_dates
"""

import json
import calendar
from datetime import date, datetime
from unittest.mock import MagicMock, patch
import pytest
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tempo_automation import TempoAutomation, ScheduleManager, main


# ---------------------------------------------------------------------------
# Helper: build a TempoAutomation without triggering __init__
# ---------------------------------------------------------------------------

def _dev_config(
    daily_hours: float = 8.0,
    overhead_hours: float = 0.0,
    pto_days: list = None,
) -> dict:
    """Minimal developer config for edge case tests."""
    return {
        "user": {"email": "dev@example.com", "role": "developer"},
        "jira": {
            "url": "test.atlassian.net",
            "email": "dev@example.com",
            "api_token": "tok",
        },
        "tempo": {"api_token": "ttok"},
        "schedule": {
            "daily_hours": daily_hours,
            "pto_days": pto_days or [],
            "extra_holidays": [],
            "working_days": [],
            "country_code": "US",
            "state": "",
        },
        "overhead": {
            "current_pi": {
                "pi_identifier": "",
                "pi_end_date": "",
                "stories": [],
                "distribution": "single",
            },
            "planning_pi": {},
            "pto_story_key": "OVERHEAD-2",
            "daily_overhead_hours": overhead_hours,
            "fallback_issue_key": "DEFAULT-1",
            "project_prefix": "OVERHEAD-",
        },
        "manual_activities": [],
        "notifications": {
            "email_enabled": False,
            "teams_webhook_url": "",
            "notify_on_shortfall": True,
        },
        "options": {"auto_submit": True, "require_confirmation": False},
    }


def _make_automation(config: dict) -> TempoAutomation:
    """Create a TempoAutomation instance without calling __init__."""
    ta = object.__new__(TempoAutomation)

    ta.config = config
    ta.config_manager = MagicMock()
    ta.dry_run = False

    sm = MagicMock()
    sm.daily_hours = config.get("schedule", {}).get("daily_hours", 8.0)
    sm.is_working_day.return_value = (True, "")
    ta.schedule_mgr = sm

    jc = MagicMock()
    jc.account_id = "712020:test-uuid"
    jc.get_my_worklogs.return_value = []
    jc.get_my_active_issues.return_value = []
    jc.get_issue_details.return_value = None
    jc.create_worklog.return_value = "12345"
    jc.delete_worklog.return_value = True
    ta.jira_client = jc

    tc = MagicMock()
    tc.account_id = "712020:test-uuid"
    tc.get_user_worklogs.return_value = []
    tc.submit_timesheet.return_value = True
    ta.tempo_client = tc

    ta.notifier = MagicMock()

    return ta


# ===========================================================================
# TestDateEdgeCases
# ===========================================================================

class TestDateEdgeCases:
    """Date boundary and config edge case tests."""

    def test_leap_year_feb_29(self):
        """Feb 29 2028 is a Tuesday -- recognized as a weekday."""
        # 2028 is a leap year; Feb 29 is a Tuesday
        d = date(2028, 2, 29)
        assert d.weekday() == 1  # Tuesday = 1

        cfg = _dev_config()
        ta = _make_automation(cfg)
        ta._pre_sync_health_check = MagicMock(return_value=True)
        ta._is_overhead_configured = MagicMock(return_value=False)
        ta._is_planning_week = MagicMock(return_value=False)
        ta.jira_client.get_my_active_issues.return_value = [
            {"issue_key": "PROJ-1", "issue_summary": "Task"},
        ]

        # schedule_mgr says it's a working day
        ta.schedule_mgr.is_working_day.return_value = (True, "")

        # Should not crash
        ta.sync_daily("2028-02-29")

        ta.jira_client.create_worklog.assert_called_once()

    def test_year_boundary_dec_31_to_jan_1(self):
        """Date range crossing year boundary works without error."""
        cfg = _dev_config()
        ta = _make_automation(cfg)

        # Verify that _check_day_hours works for both Dec 31 and Jan 1
        ta.jira_client.get_my_worklogs.return_value = []
        ta.tempo_client.get_user_worklogs.return_value = []

        result_dec = ta._check_day_hours("2026-12-31")
        result_jan = ta._check_day_hours("2027-01-01")

        # Both should return valid dicts without error
        assert "existing_hours" in result_dec
        assert "gap_hours" in result_dec
        assert "existing_hours" in result_jan
        assert "gap_hours" in result_jan

    def test_month_boundary_last_day(self):
        """Last day of month correctly identified via calendar.monthrange."""
        # Verify the pattern used in the codebase
        test_cases = [
            (2026, 2, 28),   # Feb non-leap
            (2028, 2, 29),   # Feb leap
            (2026, 4, 30),   # April
            (2026, 12, 31),  # December
        ]
        for year, month, expected_last in test_cases:
            _, last_day = calendar.monthrange(year, month)
            assert last_day == expected_last, (
                f"Expected last day of {year}-{month:02d} to be "
                f"{expected_last}, got {last_day}"
            )

    def test_future_date_sync(self, capsys):
        """Syncing a future date does not crash."""
        cfg = _dev_config()
        ta = _make_automation(cfg)
        ta._pre_sync_health_check = MagicMock(return_value=True)
        ta._is_overhead_configured = MagicMock(return_value=False)
        ta._is_planning_week = MagicMock(return_value=False)
        ta.jira_client.get_my_active_issues.return_value = [
            {"issue_key": "PROJ-1", "issue_summary": "Future task"},
        ]

        # Tomorrow's date
        ta.sync_daily("2027-06-15")

        # Should proceed without error
        ta.jira_client.create_worklog.assert_called_once()

    def test_invalid_date_format_cli(self):
        """'2026/03/13' raises SystemExit from argparse validation."""
        with patch('sys.argv', ['prog', '--date', '2026/03/13']):
            with pytest.raises(SystemExit) as exc_info:
                main()
            # argparse exits with code 2 on invalid arguments
            assert exc_info.value.code == 2

    def test_config_missing_overhead_section(self, capsys):
        """Config without overhead section uses defaults -- no crash."""
        cfg = _dev_config()
        # Remove overhead section entirely
        del cfg["overhead"]
        ta = _make_automation(cfg)

        # _get_overhead_config should return empty dict
        result = ta._get_overhead_config()
        assert result == {}

        # _is_overhead_configured should return False
        assert ta._is_overhead_configured() is False

    def test_config_daily_hours_zero(self, capsys):
        """daily_hours: 0 -> total_seconds is 0, no worklogs created."""
        cfg = _dev_config(daily_hours=0.0)
        ta = _make_automation(cfg)
        ta._pre_sync_health_check = MagicMock(return_value=True)
        ta.jira_client.get_my_worklogs.return_value = []
        ta.tempo_client.get_user_worklogs.return_value = []
        ta._is_overhead_configured = MagicMock(return_value=False)
        ta._is_planning_week = MagicMock(return_value=False)
        ta.jira_client.get_my_active_issues.return_value = [
            {"issue_key": "PROJ-1", "issue_summary": "Task"},
        ]

        result = ta._auto_log_jira_worklogs("2026-03-10")

        # With 0 daily hours, remaining_seconds <= 0, should return early
        # No worklogs should be created for active tickets
        ta.jira_client.create_worklog.assert_not_called()

    def test_config_pto_duplicate_dates(self):
        """Same date twice in PTO list -> no crash, behaves correctly."""
        cfg = _dev_config(pto_days=["2026-03-10", "2026-03-10"])
        ta = _make_automation(cfg)

        pto_list = cfg["schedule"]["pto_days"]
        # The config stores duplicates -- ScheduleManager should handle
        # them gracefully. Verify the config at least loads.
        assert len(pto_list) == 2
        assert pto_list[0] == pto_list[1]

        # sync_daily on a PTO day should skip -- no crash from duplicates
        ta.schedule_mgr.is_working_day.return_value = (False, "PTO")
        ta.sync_daily("2026-03-10")

        # No active issue lookups on PTO
        ta.jira_client.get_my_active_issues.assert_not_called()
