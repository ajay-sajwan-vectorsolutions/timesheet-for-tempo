"""
End-to-end flow integration tests.

These tests exercise multi-step flows through TempoAutomation, verifying
that collaborator methods are called in the correct order with the correct
arguments.  All collaborators are Mocks -- no real HTTP calls.

Tests (6):
- test_e2e_developer_daily_sync
- test_e2e_weekly_verify_backfill
- test_e2e_monthly_submit_flow
- test_e2e_pto_then_return
- test_e2e_overhead_default_daily
- test_e2e_no_active_tickets_overhead_fallback
"""

import json
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tempo_automation import TempoAutomation  # noqa: E402


# ---------------------------------------------------------------------------
# Helper: build a TempoAutomation without triggering __init__
# ---------------------------------------------------------------------------

def _dev_config(
    daily_hours: float = 8.0,
    overhead_hours: float = 0.0,
    pi_identifier: str = "",
    stories: list = None,
) -> dict:
    """Minimal developer config for integration tests."""
    return {
        "user": {"email": "dev@example.com", "role": "developer",
                 "name": "Test Dev"},
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
                "pi_identifier": pi_identifier,
                "pi_end_date": "",
                "stories": stories or [],
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
    ta.config_manager.config = config
    ta.config_manager.config_path = Path("/fake/config.json")
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
# TestEndToEndFlows
# ===========================================================================

@pytest.mark.integration
class TestEndToEndFlows:
    """End-to-end flow integration tests."""

    def test_e2e_developer_daily_sync(self, capsys):
        """Full developer daily sync: health check, fetch, distribute, notify."""
        cfg = _dev_config(daily_hours=8.0)
        ta = _make_automation(cfg)
        ta._pre_sync_health_check = MagicMock(return_value=True)
        ta._is_overhead_configured = MagicMock(return_value=False)
        ta._is_planning_week = MagicMock(return_value=False)

        ta.jira_client.get_my_active_issues.return_value = [
            {"issue_key": "PROJ-101", "issue_summary": "Auth module"},
            {"issue_key": "PROJ-102", "issue_summary": "Search feature"},
        ]

        ta.sync_daily("2026-03-10")

        # Health check called
        ta._pre_sync_health_check.assert_called_once()

        # Worklogs fetched for the target date
        ta.jira_client.get_my_worklogs.assert_called_with(
            "2026-03-10", "2026-03-10"
        )

        # Active issues fetched
        ta.jira_client.get_my_active_issues.assert_called_once()

        # 2 worklogs created (one per ticket)
        assert ta.jira_client.create_worklog.call_count == 2

        # Each ticket gets 4h (8h / 2 tickets = 14400s each)
        for c in ta.jira_client.create_worklog.call_args_list:
            assert c.kwargs["time_spent_seconds"] == 14400

        # Notification sent
        ta.notifier.send_daily_summary.assert_called_once()

    def test_e2e_weekly_verify_backfill(self, capsys):
        """Verify week with gaps -> _backfill_day called for gap days."""
        cfg = _dev_config(daily_hours=8.0)
        ta = _make_automation(cfg)

        # Mock _check_day_hours to return a gap on Wednesday
        def check_day(d):
            if d == "2026-03-11":  # Wednesday with gap
                return {
                    "existing_hours": 4.0,
                    "gap_hours": 4.0,
                    "worklogs": [],
                    "existing_keys": set(),
                }
            return {
                "existing_hours": 8.0,
                "gap_hours": 0.0,
                "worklogs": [],
                "existing_keys": set(),
            }

        ta._check_day_hours = MagicMock(side_effect=check_day)

        # Mock _backfill_day
        ta._backfill_day = MagicMock(return_value={
            "created_count": 1,
            "hours_added": 4.0,
            "method": "stories",
        })

        # Mock schedule_mgr for each weekday
        def is_working(d):
            return (True, "")

        ta.schedule_mgr.is_working_day.side_effect = is_working

        with patch("tempo_automation.date") as mock_date:
            mock_date.today.return_value = date(2026, 3, 13)  # Friday
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            ta.verify_week()

        # _backfill_day should be called for the gap day
        backfill_calls = ta._backfill_day.call_args_list
        backfill_dates = [c.args[0] for c in backfill_calls]
        assert "2026-03-11" in backfill_dates

    def test_e2e_monthly_submit_flow(self, capsys):
        """View monthly -> submit: _detect_monthly_gaps checked, submit called."""
        from freezegun import freeze_time

        cfg = _dev_config(daily_hours=8.0)
        ta = _make_automation(cfg)
        ta._pre_sync_health_check = MagicMock(return_value=True)
        ta._is_already_submitted = MagicMock(return_value=False)
        ta.schedule_mgr.count_working_days.return_value = 0

        # Mock _detect_monthly_gaps to return no gaps
        ta._detect_monthly_gaps = MagicMock(return_value={
            "working_days": 22,
            "expected": 176.0,
            "actual": 176.0,
            "gaps": [],
        })

        # Mock the period lookup
        ta.tempo_client.get_periods.return_value = [{
            "key": "2026-03",
            "dateFrom": "2026-03-01",
            "dateTo": "2026-03-31",
            "status": "OPEN",
        }]

        # Freeze to last day of month so submission window is open
        with freeze_time("2026-03-31"):
            ta.submit_timesheet()

        # submit_timesheet on tempo_client should be called
        ta.tempo_client.submit_timesheet.assert_called()

    def test_e2e_pto_then_return(self, capsys):
        """Add PTO -> sync skips, remove PTO -> sync works."""
        cfg = _dev_config(daily_hours=8.0)
        ta = _make_automation(cfg)
        ta._pre_sync_health_check = MagicMock(return_value=True)
        ta._is_overhead_configured = MagicMock(return_value=False)
        ta._is_planning_week = MagicMock(return_value=False)

        # Day 1: PTO -- should skip
        ta.schedule_mgr.is_working_day.return_value = (False, "PTO")
        ta.sync_daily("2026-03-10")

        # No worklogs created on PTO day (developer + no overhead)
        ta.jira_client.create_worklog.assert_not_called()

        # Day 2: Back to work -- should sync normally
        ta.schedule_mgr.is_working_day.return_value = (True, "")
        ta.jira_client.get_my_active_issues.return_value = [
            {"issue_key": "PROJ-1", "issue_summary": "Back to work"},
        ]

        ta.sync_daily("2026-03-11")

        ta.jira_client.create_worklog.assert_called_once()

    def test_e2e_overhead_default_daily(self, capsys):
        """Daily overhead (2h) logged before distributing to active tickets."""
        cfg = _dev_config(
            daily_hours=8.0,
            overhead_hours=2.0,
            pi_identifier="PI.26.2.APR.17",
            stories=[
                {"issue_key": "OVERHEAD-10", "summary": "Ceremonies",
                 "hours": 2},
            ],
        )
        ta = _make_automation(cfg)
        ta._pre_sync_health_check = MagicMock(return_value=True)
        ta._is_planning_week = MagicMock(return_value=False)

        # _log_overhead_hours returns the overhead worklog
        ta._log_overhead_hours = MagicMock(return_value=[
            {"issue_key": "OVERHEAD-10", "issue_summary": "Ceremonies",
             "time_spent_seconds": 7200},
        ])

        ta.jira_client.get_my_active_issues.return_value = [
            {"issue_key": "PROJ-1", "issue_summary": "Active task"},
        ]

        result = ta._auto_log_jira_worklogs("2026-03-10")

        # Overhead logged first (2h = 7200s)
        ta._log_overhead_hours.assert_called()
        oh_call = ta._log_overhead_hours.call_args
        assert oh_call.args[1] == 7200  # gap_seconds for 2h overhead

        # Active ticket gets remaining 6h = 21600s
        create_call = ta.jira_client.create_worklog.call_args
        assert create_call.kwargs["time_spent_seconds"] == 21600

        # Total result includes both overhead and active
        total_seconds = sum(wl["time_spent_seconds"] for wl in result)
        assert total_seconds == 8 * 3600

    def test_e2e_no_active_tickets_overhead_fallback(self, capsys):
        """No active tickets -> all hours go to overhead stories."""
        cfg = _dev_config(
            daily_hours=8.0,
            overhead_hours=0.0,
            pi_identifier="PI.26.2.APR.17",
            stories=[
                {"issue_key": "OVERHEAD-10", "summary": "Ceremonies",
                 "hours": 2},
            ],
        )
        ta = _make_automation(cfg)
        ta._pre_sync_health_check = MagicMock(return_value=True)
        ta._is_planning_week = MagicMock(return_value=False)

        ta.jira_client.get_my_worklogs.return_value = []
        ta.tempo_client.get_user_worklogs.return_value = []
        ta.jira_client.get_my_active_issues.return_value = []

        ta._log_overhead_hours = MagicMock(return_value=[
            {"issue_key": "OVERHEAD-10", "issue_summary": "Ceremonies",
             "time_spent_seconds": 28800},
        ])

        result = ta._auto_log_jira_worklogs("2026-03-10")

        # No create_worklog calls for active tickets
        ta.jira_client.create_worklog.assert_not_called()

        # All hours went to overhead
        ta._log_overhead_hours.assert_called()
        assert any(
            wl["issue_key"] == "OVERHEAD-10" for wl in result
        )
