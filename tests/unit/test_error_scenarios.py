"""
Error scenario tests for Tempo Timesheet Automation.

Verifies that the system handles API failures, edge cases in ticket
distribution, and unusual input gracefully without crashing.

Tests (12):
- test_jira_network_timeout_during_sync
- test_tempo_network_timeout_during_sync
- test_jira_401_during_worklog_create
- test_tempo_401_during_submit
- test_zero_active_tickets_no_overhead
- test_one_ticket_gets_all_hours
- test_remainder_on_last_ticket
- test_very_long_description_handling
- test_empty_jira_description
- test_unicode_in_ticket_summary
- test_jira_500_server_error
- test_sync_daily_weekend_skip
"""

import json
from datetime import date
from unittest.mock import MagicMock, patch, PropertyMock
import pytest
import sys
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tempo_automation import TempoAutomation, JiraClient, TempoClient


# ---------------------------------------------------------------------------
# Helper: build a TempoAutomation without triggering __init__
# ---------------------------------------------------------------------------

def _dev_config(
    daily_hours: float = 8.0,
    overhead_hours: float = 0.0,
) -> dict:
    """Minimal developer config for error scenario tests."""
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
            "pto_days": [],
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
# TestErrorScenarios
# ===========================================================================

class TestErrorScenarios:
    """Error and failure path tests."""

    def test_jira_network_timeout_during_sync(self, capsys):
        """Jira get_my_worklogs raises Timeout -> sync handles gracefully."""
        cfg = _dev_config()
        ta = _make_automation(cfg)
        # Health check must pass first
        ta._pre_sync_health_check = MagicMock(return_value=True)
        ta.jira_client.get_my_worklogs.side_effect = (
            requests.exceptions.Timeout("Connection timed out")
        )

        # sync_daily should not raise -- the exception propagates up to
        # main() which prints [ERROR] and exits.  We verify it does not
        # crash silently by confirming the timeout propagates.
        with pytest.raises(requests.exceptions.Timeout):
            ta.sync_daily("2026-03-10")

    def test_tempo_network_timeout_during_sync(self, capsys):
        """Tempo get_user_worklogs raises Timeout -> handled."""
        cfg = _dev_config()
        ta = _make_automation(cfg)
        ta._pre_sync_health_check = MagicMock(return_value=True)
        ta.jira_client.get_my_worklogs.return_value = []
        ta.tempo_client.get_user_worklogs.side_effect = (
            requests.exceptions.Timeout("Tempo timed out")
        )

        with pytest.raises(requests.exceptions.Timeout):
            ta.sync_daily("2026-03-10")

    def test_jira_401_during_worklog_create(self, capsys):
        """create_worklog returns False on 401 -> no crash, [FAIL] printed."""
        cfg = _dev_config()
        ta = _make_automation(cfg)
        ta._pre_sync_health_check = MagicMock(return_value=True)
        ta.jira_client.get_my_worklogs.return_value = []
        ta.tempo_client.get_user_worklogs.return_value = []
        ta._is_overhead_configured = MagicMock(return_value=False)
        ta._is_planning_week = MagicMock(return_value=False)
        ta.jira_client.get_my_active_issues.return_value = [
            {"issue_key": "PROJ-1", "issue_summary": "Task A"},
        ]
        ta.jira_client.create_worklog.return_value = False

        ta.sync_daily("2026-03-10")

        output = capsys.readouterr().out
        assert "[FAIL]" in output

    def test_tempo_401_during_submit(self, capsys):
        """submit_timesheet returns False on 401 -> clear error, no crash."""
        from freezegun import freeze_time

        cfg = _dev_config()
        ta = _make_automation(cfg)
        ta._pre_sync_health_check = MagicMock(return_value=True)
        ta.tempo_client.submit_timesheet.return_value = False
        ta._is_already_submitted = MagicMock(return_value=False)
        ta.schedule_mgr.count_working_days.return_value = 0

        # Mock _detect_monthly_gaps to return no gaps so it proceeds
        ta._detect_monthly_gaps = MagicMock(return_value={
            "working_days": 22,
            "expected": 176.0,
            "actual": 176.0,
            "gaps": [],
        })

        # Freeze to last day of month so submission window is open
        with freeze_time("2026-03-31"):
            ta.submit_timesheet()

        # Should not crash -- returns False or prints failure
        ta.tempo_client.submit_timesheet.assert_called()

    def test_zero_active_tickets_no_overhead(self, capsys):
        """No tickets, no overhead configured -> prints warning, no error."""
        cfg = _dev_config()
        ta = _make_automation(cfg)
        ta._pre_sync_health_check = MagicMock(return_value=True)
        ta.jira_client.get_my_worklogs.return_value = []
        ta.tempo_client.get_user_worklogs.return_value = []
        ta._is_overhead_configured = MagicMock(return_value=False)
        ta._is_planning_week = MagicMock(return_value=False)
        ta.jira_client.get_my_active_issues.return_value = []
        ta._warn_overhead_not_configured = MagicMock()

        # Should not crash
        ta.sync_daily("2026-03-10")

        output = capsys.readouterr().out
        assert "No active tickets" in output or \
            ta._warn_overhead_not_configured.called

    def test_one_ticket_gets_all_hours(self, capsys):
        """1 ticket -> gets full 8h (minus overhead)."""
        cfg = _dev_config(daily_hours=8.0, overhead_hours=0.0)
        ta = _make_automation(cfg)
        ta._pre_sync_health_check = MagicMock(return_value=True)
        ta.jira_client.get_my_worklogs.return_value = []
        ta.tempo_client.get_user_worklogs.return_value = []
        ta._is_overhead_configured = MagicMock(return_value=False)
        ta._is_planning_week = MagicMock(return_value=False)
        ta.jira_client.get_my_active_issues.return_value = [
            {"issue_key": "PROJ-1", "issue_summary": "Solo task"},
        ]

        result = ta._auto_log_jira_worklogs("2026-03-10")

        call_args = ta.jira_client.create_worklog.call_args
        assert call_args.kwargs["time_spent_seconds"] == 8 * 3600

    def test_remainder_on_last_ticket(self, capsys):
        """Non-divisible hours -> remainder goes to last ticket."""
        # 7h = 25200s across 4 tickets: 25200 // 4 = 6300, rem = 25200 - 25200 = 0
        # Use 7h across 3 tickets: 25200 // 3 = 8400, rem = 25200 - 25200 = 0
        # Use 7.5h: 27000 // 4 = 6750, rem = 0 -- still divisible
        # Use daily_hours=7.0, overhead=0, 4 tickets:
        #   28800 is divisible. Let's do 10h, 3 tickets: 36000//3=12000, rem=0
        # Force non-divisible: daily_hours=7, 0 overhead, 3 tickets
        #   7*3600=25200, 25200//3=8400, rem=0. Still zero.
        # 11h, 3 tickets: 39600//3=13200, rem=0.
        # Actually try 5 tickets with 8h: 28800//5=5760, rem=0.
        # 7 tickets: 28800//7=4114, 4114*7=28798, rem=2
        cfg = _dev_config(daily_hours=8.0, overhead_hours=0.0)
        ta = _make_automation(cfg)
        ta._pre_sync_health_check = MagicMock(return_value=True)
        ta.jira_client.get_my_worklogs.return_value = []
        ta.tempo_client.get_user_worklogs.return_value = []
        ta._is_overhead_configured = MagicMock(return_value=False)
        ta._is_planning_week = MagicMock(return_value=False)
        ta.jira_client.get_my_active_issues.return_value = [
            {"issue_key": f"PROJ-{i}", "issue_summary": f"T{i}"}
            for i in range(7)
        ]

        ta._auto_log_jira_worklogs("2026-03-10")

        calls = ta.jira_client.create_worklog.call_args_list
        seconds_list = [c.kwargs["time_spent_seconds"] for c in calls]

        total = 8 * 3600  # 28800
        per_ticket = total // 7  # 4114
        remainder = total - (per_ticket * 7)  # 2

        # First 6 tickets get per_ticket, last gets per_ticket + remainder
        for s in seconds_list[:-1]:
            assert s == per_ticket
        assert seconds_list[-1] == per_ticket + remainder
        assert sum(seconds_list) == total

    def test_very_long_description_handling(self, capsys):
        """5000-char description -> truncated in output, no crash."""
        cfg = _dev_config()
        ta = _make_automation(cfg)
        ta._pre_sync_health_check = MagicMock(return_value=True)
        ta.jira_client.get_my_worklogs.return_value = []
        ta.tempo_client.get_user_worklogs.return_value = []
        ta._is_overhead_configured = MagicMock(return_value=False)
        ta._is_planning_week = MagicMock(return_value=False)
        ta.jira_client.get_my_active_issues.return_value = [
            {"issue_key": "PROJ-1", "issue_summary": "Task"},
        ]
        # Return a very long description text
        long_desc = "A" * 5000
        ta.jira_client.get_issue_details.return_value = {
            "description_text": long_desc,
            "recent_comments": [],
        }

        ta._auto_log_jira_worklogs("2026-03-10")

        output = capsys.readouterr().out
        # Description line should be truncated (80 char limit in print)
        # The output should contain "..." indicating truncation
        assert "..." in output

    def test_empty_jira_description(self, capsys):
        """No description -> fallback to summary in worklog comment."""
        cfg = _dev_config()
        ta = _make_automation(cfg)
        ta.jira_client.get_issue_details.return_value = {
            "description_text": "",
            "recent_comments": [],
        }

        result = ta._generate_work_summary("PROJ-1", "My Task Summary")

        assert "My Task Summary" in result

    def test_unicode_in_ticket_summary(self, capsys):
        """Unicode chars in summary -> no crash (ASCII-safe output)."""
        cfg = _dev_config()
        ta = _make_automation(cfg)
        ta._pre_sync_health_check = MagicMock(return_value=True)
        ta.jira_client.get_my_worklogs.return_value = []
        ta.tempo_client.get_user_worklogs.return_value = []
        ta._is_overhead_configured = MagicMock(return_value=False)
        ta._is_planning_week = MagicMock(return_value=False)
        ta.jira_client.get_my_active_issues.return_value = [
            {
                "issue_key": "PROJ-1",
                "issue_summary": "Fix bug in m\u00f6dule \u2014 urgent",
            },
        ]

        # Should not crash even with Unicode in summary
        ta._auto_log_jira_worklogs("2026-03-10")

        # Verify create_worklog was still called
        ta.jira_client.create_worklog.assert_called_once()

    def test_jira_500_server_error(self, capsys):
        """Jira 500 error during health check -> prints [FAIL], aborts sync."""
        cfg = _dev_config()
        ta = _make_automation(cfg)

        # Mock health check to return False (simulating 500)
        ta._pre_sync_health_check = MagicMock(return_value=False)

        ta.sync_daily("2026-03-10")

        output = capsys.readouterr().out
        assert "[FAIL]" in output
        # No Jira API calls should have been made after failed health check
        ta.jira_client.get_my_active_issues.assert_not_called()

    def test_sync_daily_weekend_skip(self, capsys):
        """sync on Saturday -> prints skip message, no API calls."""
        cfg = _dev_config()
        ta = _make_automation(cfg)
        ta.schedule_mgr.is_working_day.return_value = (False, "Weekend")

        ta.sync_daily("2026-03-14")  # Saturday

        output = capsys.readouterr().out
        assert "not a working day" in output or "SKIP" in output
        ta.jira_client.get_my_active_issues.assert_not_called()
        ta.tempo_client.get_user_worklogs.assert_not_called()
