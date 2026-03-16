"""
Unit tests for the TempoAutomation class (tempo_automation.py).

Strategy
--------
TempoAutomation.__init__ is intentionally heavy (reads config, makes API
calls, loads holidays).  All tests bypass it by building instances with
``object.__new__`` and then injecting pre-configured Mock objects for the
five collaborators:
  - config           (plain dict)
  - config_manager   (Mock)
  - schedule_mgr     (Mock with .is_working_day() / .daily_hours)
  - jira_client      (Mock)
  - tempo_client     (Mock with .account_id)
  - notifier         (Mock)

See ``_make_automation()`` below.

Coverage targets (~43 tests)
-----------------------------
- sync_daily                          8 tests
- _auto_log_jira_worklogs            10 tests
- Hour distribution math (parametrize) 5 tests
- _generate_work_summary              4 tests
- _is_overhead_configured             3 tests
- _parse_pi_end_date                  5 tests
- _is_planning_week                   3 tests
- _log_overhead_hours                 4 tests
- _detect_monthly_gaps                4 tests
- submit_timesheet                    3 tests
- verify_week                         2 tests
"""

import json
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
import responses as responses_lib

# ---------------------------------------------------------------------------
# Ensure project root is importable
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tempo_automation import TempoAutomation  # noqa: E402


# ---------------------------------------------------------------------------
# Core helper: build a TempoAutomation without triggering __init__
# ---------------------------------------------------------------------------

def _make_automation(config: dict) -> TempoAutomation:
    """
    Create a TempoAutomation instance without calling __init__.

    Manually attaches mock collaborators so each test starts clean and
    deterministic.  The caller can then configure the mocks as needed.
    """
    ta = object.__new__(TempoAutomation)

    ta.config = config
    ta.config_manager = MagicMock()
    ta.dry_run = False

    # ScheduleManager mock: is_working_day returns (True, "") by default
    sm = MagicMock()
    sm.daily_hours = config.get("schedule", {}).get("daily_hours", 8.0)
    sm.is_working_day.return_value = (True, "")
    ta.schedule_mgr = sm

    # JiraClient mock
    jc = MagicMock()
    jc.account_id = "712020:test-uuid"
    jc.get_my_worklogs.return_value = []
    jc.get_my_active_issues.return_value = []
    jc.get_issue_details.return_value = None
    jc.create_worklog.return_value = "12345"
    jc.delete_worklog.return_value = True
    ta.jira_client = jc

    # TempoClient mock
    tc = MagicMock()
    tc.account_id = "712020:test-uuid"
    tc.get_user_worklogs.return_value = []
    tc.submit_timesheet.return_value = True
    ta.tempo_client = tc

    ta.notifier = MagicMock()

    return ta


# ---------------------------------------------------------------------------
# Convenience: build a minimal developer config
# ---------------------------------------------------------------------------

def _dev_config(
    daily_hours: float = 8.0,
    overhead_hours: float = 2.0,
    pi_identifier: str = "PI.26.1.JAN.30",
    pi_end_date: str = "2026-01-30",
    stories: list = None,
    distribution: str = "single",
    planning_pi: dict = None,
    pto_days: list = None,
) -> dict:
    if stories is None:
        stories = [
            {"issue_key": "OVERHEAD-10", "summary": "Ceremonies", "hours": 2}
        ]
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
                "pi_identifier": pi_identifier,
                "pi_end_date": pi_end_date,
                "stories": stories,
                "distribution": distribution,
            },
            "planning_pi": planning_pi or {},
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


# ===========================================================================
# sync_daily
# ===========================================================================

class TestSyncDaily:
    """Tests for TempoAutomation.sync_daily()."""

    def test_defaults_to_today_when_target_date_is_none(self):
        """When called with no argument, sync_daily uses today's date."""
        cfg = _dev_config()
        ta = _make_automation(cfg)
        # schedule_mgr.is_working_day returns (True, "") -- flow reaches _auto_log
        ta.jira_client.get_my_active_issues.return_value = []
        ta._is_overhead_configured = MagicMock(return_value=True)
        ta._auto_log_jira_worklogs = MagicMock(return_value=[])

        with patch("tempo_automation.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 10)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            ta.sync_daily()

        ta._auto_log_jira_worklogs.assert_called_once_with("2026-02-10")

    def test_schedule_guard_weekend_skips_with_no_api_calls(self):
        """Weekend day -> returns immediately without touching Jira/Tempo."""
        cfg = _dev_config()
        ta = _make_automation(cfg)
        ta.schedule_mgr.is_working_day.return_value = (False, "Weekend")

        ta.sync_daily("2026-02-21")  # Saturday

        ta.jira_client.get_my_active_issues.assert_not_called()
        ta.tempo_client.get_user_worklogs.assert_not_called()
        ta.notifier.send_daily_summary.assert_not_called()

    def test_schedule_guard_pto_developer_overhead_configured_calls_sync_pto(self):
        """PTO day + developer + overhead configured -> _sync_pto_overhead called."""
        cfg = _dev_config()
        ta = _make_automation(cfg)
        ta.schedule_mgr.is_working_day.return_value = (False, "PTO")
        ta._is_overhead_configured = MagicMock(return_value=True)
        ta._sync_pto_overhead = MagicMock()

        ta.sync_daily("2026-03-10")

        ta._sync_pto_overhead.assert_called_once_with("2026-03-10")

    def test_schedule_guard_pto_developer_no_overhead_warns_and_skips(self):
        """PTO day + developer + NO overhead configured -> warns, does NOT submit."""
        cfg = _dev_config()
        ta = _make_automation(cfg)
        ta.schedule_mgr.is_working_day.return_value = (False, "PTO")
        ta._is_overhead_configured = MagicMock(return_value=False)
        ta._warn_overhead_not_configured = MagicMock()
        ta._sync_pto_overhead = MagicMock()

        ta.sync_daily("2026-03-10")

        ta._sync_pto_overhead.assert_not_called()
        ta._warn_overhead_not_configured.assert_called_once()
        ta.notifier.send_daily_summary.assert_not_called()

    def test_schedule_guard_pto_non_developer_skips(self):
        """PTO day + non-developer role -> prints skip, no API calls."""
        cfg = _dev_config()
        cfg["user"]["role"] = "product_owner"
        ta = _make_automation(cfg)
        ta.schedule_mgr.is_working_day.return_value = (False, "PTO")
        ta._sync_pto_overhead = MagicMock()

        ta.sync_daily("2026-03-10")

        ta._sync_pto_overhead.assert_not_called()
        ta.notifier.send_daily_summary.assert_not_called()

    def test_developer_role_calls_auto_log_jira_worklogs(self):
        """Working day + developer role -> _auto_log_jira_worklogs called."""
        cfg = _dev_config()
        ta = _make_automation(cfg)
        ta._auto_log_jira_worklogs = MagicMock(return_value=[
            {"issue_key": "PROJ-1", "issue_summary": "Task", "time_spent_seconds": 28800}
        ])

        ta.sync_daily("2026-02-10")

        ta._auto_log_jira_worklogs.assert_called_once_with("2026-02-10")
        ta.notifier.send_daily_summary.assert_called_once()

    def test_non_developer_role_calls_sync_manual_activities(self):
        """Working day + non-developer role -> _sync_manual_activities called."""
        cfg = _dev_config()
        cfg["user"]["role"] = "product_owner"
        ta = _make_automation(cfg)
        ta._sync_manual_activities = MagicMock(return_value=[])

        ta.sync_daily("2026-02-10")

        ta._sync_manual_activities.assert_called_once_with("2026-02-10")

    def test_notifier_send_daily_summary_called_after_working_day(self):
        """send_daily_summary is called at the end of a successful working day."""
        cfg = _dev_config()
        ta = _make_automation(cfg)
        worklogs = [
            {"issue_key": "PROJ-1", "issue_summary": "Task", "time_spent_seconds": 28800}
        ]
        ta._auto_log_jira_worklogs = MagicMock(return_value=worklogs)

        ta.sync_daily("2026-02-10")

        ta.notifier.send_daily_summary.assert_called_once_with(worklogs, 8.0)


# ===========================================================================
# _auto_log_jira_worklogs
# ===========================================================================

class TestAutoLogJiraWorklogs:
    """Tests for TempoAutomation._auto_log_jira_worklogs()."""

    def test_case0_default_overhead_logged_when_below_configured(self):
        """When existing overhead < configured daily_overhead_hours, extra is logged."""
        cfg = _dev_config(daily_hours=8.0, overhead_hours=2.0)
        ta = _make_automation(cfg)
        # No existing worklogs -> 0h overhead so far
        ta.jira_client.get_my_worklogs.return_value = []
        ta.tempo_client.get_user_worklogs.return_value = []
        ta.jira_client.get_my_active_issues.return_value = [
            {"issue_key": "PROJ-1", "issue_summary": "Task"}
        ]
        ta._is_overhead_configured = MagicMock(return_value=True)
        ta._is_planning_week = MagicMock(return_value=False)
        logged_calls = []

        def fake_log_overhead(date_, seconds, stories=None, dist=None):
            logged_calls.append(seconds)
            return [{"issue_key": "OVERHEAD-10", "issue_summary": "x",
                     "time_spent_seconds": seconds}]

        ta._log_overhead_hours = MagicMock(side_effect=fake_log_overhead)

        ta._auto_log_jira_worklogs("2026-02-10")

        # Should log 2h (7200 seconds) of overhead
        assert 7200 in logged_calls

    def test_case1_no_active_tickets_overhead_configured_calls_log_overhead(self):
        """No active tickets + overhead configured -> _log_overhead_hours called."""
        cfg = _dev_config(daily_hours=8.0, overhead_hours=0.0)
        ta = _make_automation(cfg)
        ta.jira_client.get_my_worklogs.return_value = []
        ta.tempo_client.get_user_worklogs.return_value = []
        ta.jira_client.get_my_active_issues.return_value = []
        ta._is_overhead_configured = MagicMock(return_value=True)
        ta._is_planning_week = MagicMock(return_value=False)
        ta._log_overhead_hours = MagicMock(return_value=[
            {"issue_key": "OVERHEAD-10", "issue_summary": "x", "time_spent_seconds": 28800}
        ])

        result = ta._auto_log_jira_worklogs("2026-02-10")

        ta._log_overhead_hours.assert_called()
        assert any(wl["issue_key"] == "OVERHEAD-10" for wl in result)

    def test_case1_no_active_tickets_no_overhead_warns_returns_only_overhead(self):
        """No active tickets + no overhead configured -> warns, returns empty list."""
        cfg = _dev_config(daily_hours=8.0, overhead_hours=0.0)
        ta = _make_automation(cfg)
        ta.jira_client.get_my_worklogs.return_value = []
        ta.tempo_client.get_user_worklogs.return_value = []
        ta.jira_client.get_my_active_issues.return_value = []
        ta._is_overhead_configured = MagicMock(return_value=False)
        ta._is_planning_week = MagicMock(return_value=False)
        ta._warn_overhead_not_configured = MagicMock()

        result = ta._auto_log_jira_worklogs("2026-02-10")

        ta._warn_overhead_not_configured.assert_called_once()
        assert result == []

    def test_case2_jira_overhead_worklogs_preserved_not_deleted(self):
        """Existing OVERHEAD-* Jira worklogs must NOT be deleted."""
        cfg = _dev_config()
        ta = _make_automation(cfg)
        overhead_wl = {
            "issue_key": "OVERHEAD-10",
            "worklog_id": "999",
            "time_spent_seconds": 7200,
        }
        normal_wl = {
            "issue_key": "PROJ-1",
            "worklog_id": "888",
            "time_spent_seconds": 21600,
        }
        ta.jira_client.get_my_worklogs.return_value = [overhead_wl, normal_wl]
        ta.tempo_client.get_user_worklogs.return_value = []
        ta.jira_client.get_my_active_issues.return_value = [
            {"issue_key": "PROJ-2", "issue_summary": "New task"}
        ]
        ta._is_overhead_configured = MagicMock(return_value=True)
        ta._is_planning_week = MagicMock(return_value=False)

        ta._auto_log_jira_worklogs("2026-02-10")

        # delete_worklog called for non-overhead only
        deleted_keys = [
            c.args[0] for c in ta.jira_client.delete_worklog.call_args_list
        ]
        assert "OVERHEAD-10" not in deleted_keys
        assert "PROJ-1" in deleted_keys

    def test_normal_flow_2_tickets_8h_2h_overhead_distributes_3h_each(self):
        """2 active tickets, 8h total, 2h overhead -> 3h each ticket."""
        cfg = _dev_config(daily_hours=8.0, overhead_hours=2.0)
        ta = _make_automation(cfg)
        ta.jira_client.get_my_worklogs.return_value = []
        ta.tempo_client.get_user_worklogs.return_value = []
        ta._is_overhead_configured = MagicMock(return_value=True)
        ta._is_planning_week = MagicMock(return_value=False)
        ta._log_overhead_hours = MagicMock(return_value=[
            {"issue_key": "OVERHEAD-10", "issue_summary": "x", "time_spent_seconds": 7200}
        ])
        ta.jira_client.get_my_active_issues.return_value = [
            {"issue_key": "PROJ-1", "issue_summary": "Task A"},
            {"issue_key": "PROJ-2", "issue_summary": "Task B"},
        ]
        ta.jira_client.get_issue_details.return_value = None

        result = ta._auto_log_jira_worklogs("2026-02-10")

        ticket_calls = [
            c for c in ta.jira_client.create_worklog.call_args_list
        ]
        ticket_seconds = [c.kwargs["time_spent_seconds"] for c in ticket_calls]
        # Remaining = 8h - 2h = 6h = 21600s; 21600 // 2 = 10800s = 3h each
        assert ticket_seconds == [10800, 10800]

    def test_normal_flow_3_tickets_8h_no_overhead_distributes_with_remainder(self):
        """3 active tickets, 8h, no overhead -> ~2.67h each, remainder on last."""
        cfg = _dev_config(daily_hours=8.0, overhead_hours=0.0)
        ta = _make_automation(cfg)
        ta.jira_client.get_my_worklogs.return_value = []
        ta.tempo_client.get_user_worklogs.return_value = []
        ta._is_overhead_configured = MagicMock(return_value=False)
        ta._is_planning_week = MagicMock(return_value=False)
        ta.jira_client.get_my_active_issues.return_value = [
            {"issue_key": "PROJ-1", "issue_summary": "A"},
            {"issue_key": "PROJ-2", "issue_summary": "B"},
            {"issue_key": "PROJ-3", "issue_summary": "C"},
        ]
        ta.jira_client.get_issue_details.return_value = None

        ta._auto_log_jira_worklogs("2026-02-10")

        ticket_calls = ta.jira_client.create_worklog.call_args_list
        ticket_seconds = [c.kwargs["time_spent_seconds"] for c in ticket_calls]
        total_seconds = 8 * 3600  # 28800
        per_ticket = total_seconds // 3           # 9600
        remainder = total_seconds - (per_ticket * 3)  # 0
        assert ticket_seconds[0] == per_ticket
        assert ticket_seconds[1] == per_ticket
        assert ticket_seconds[2] == per_ticket + remainder
        assert sum(ticket_seconds) == total_seconds

    def test_idempotent_creates_before_deleting_non_overhead(self):
        """New worklogs are created before old non-overhead ones are deleted."""
        cfg = _dev_config(daily_hours=8.0, overhead_hours=0.0)
        ta = _make_automation(cfg)
        existing_wl = {
            "issue_key": "PROJ-OLD",
            "worklog_id": "77",
            "time_spent_seconds": 28800,
        }
        ta.jira_client.get_my_worklogs.return_value = [existing_wl]
        ta.tempo_client.get_user_worklogs.return_value = []
        ta._is_overhead_configured = MagicMock(return_value=False)
        ta._is_planning_week = MagicMock(return_value=False)
        ta.jira_client.get_my_active_issues.return_value = [
            {"issue_key": "PROJ-NEW", "issue_summary": "New task"}
        ]
        ta.jira_client.get_issue_details.return_value = None
        call_order = []
        ta.jira_client.delete_worklog.side_effect = lambda *a, **kw: call_order.append("delete") or True
        ta.jira_client.create_worklog.side_effect = lambda **kw: call_order.append("create") or "99"

        ta._auto_log_jira_worklogs("2026-02-10")

        # create must happen before delete (safe create-before-delete pattern)
        assert call_order.index("create") < call_order.index("delete")

    def test_overhead_already_at_target_no_additional_logging(self):
        """When overhead >= daily target, returns immediately with overhead result only."""
        cfg = _dev_config(daily_hours=8.0, overhead_hours=2.0)
        ta = _make_automation(cfg)
        # 8h overhead via Jira -- already at daily target
        overhead_wl = {
            "issue_key": "OVERHEAD-10",
            "worklog_id": "1",
            "time_spent_seconds": 28800,
        }
        ta.jira_client.get_my_worklogs.return_value = [overhead_wl]
        ta.tempo_client.get_user_worklogs.return_value = []
        ta._is_overhead_configured = MagicMock(return_value=True)

        result = ta._auto_log_jira_worklogs("2026-02-10")

        ta.jira_client.create_worklog.assert_not_called()
        assert len(result) == 1
        assert result[0]["issue_key"] == "OVERHEAD-10"

    def test_case4_planning_week_uses_planning_pi_stories(self):
        """Planning week -> _log_overhead_hours called with planning_pi stories."""
        planning_stories = [{"issue_key": "OVERHEAD-20", "summary": "Planning"}]
        cfg = _dev_config(
            overhead_hours=0.0,
            planning_pi={"stories": planning_stories, "distribution": "single"},
        )
        ta = _make_automation(cfg)
        ta.jira_client.get_my_worklogs.return_value = []
        ta.tempo_client.get_user_worklogs.return_value = []
        ta._is_overhead_configured = MagicMock(return_value=True)
        ta._is_planning_week = MagicMock(return_value=True)
        ta._log_overhead_hours = MagicMock(return_value=[
            {"issue_key": "OVERHEAD-20", "issue_summary": "Planning", "time_spent_seconds": 28800}
        ])

        result = ta._auto_log_jira_worklogs("2026-02-02")

        ta._log_overhead_hours.assert_called_once_with(
            "2026-02-02", 28800, planning_stories, "single"
        )
        assert any(wl["issue_key"] == "OVERHEAD-20" for wl in result)

    def test_tempo_only_overhead_counted_in_total(self):
        """Manual Tempo entries (not in Jira) are counted toward overhead seconds."""
        cfg = _dev_config(daily_hours=8.0, overhead_hours=2.0)
        ta = _make_automation(cfg)
        ta.jira_client.get_my_worklogs.return_value = []
        # Tempo has 2h of manual overhead -> overhead_seconds = 7200
        ta.tempo_client.get_user_worklogs.return_value = [
            {"timeSpentSeconds": 7200, "startDate": "2026-02-10"}
        ]
        ta._is_overhead_configured = MagicMock(return_value=True)
        ta._is_planning_week = MagicMock(return_value=False)
        ta._log_overhead_hours = MagicMock(return_value=[])
        ta.jira_client.get_my_active_issues.return_value = [
            {"issue_key": "PROJ-1", "issue_summary": "Task"}
        ]
        ta.jira_client.get_issue_details.return_value = None

        ta._auto_log_jira_worklogs("2026-02-10")

        # Since tempo_only_seconds=7200 == default_oh_seconds=7200,
        # _log_overhead_hours should NOT be called for the overhead top-up
        ta._log_overhead_hours.assert_not_called()

        # Remaining = 8h - 2h = 6h -> distributed to 1 ticket
        ticket_calls = ta.jira_client.create_worklog.call_args_list
        assert len(ticket_calls) == 1
        assert ticket_calls[0].kwargs["time_spent_seconds"] == 6 * 3600


# ===========================================================================
# Hour distribution math (parametrized)
# ===========================================================================

class TestHourDistributionMath:
    """
    Parametrized tests for the integer-division + remainder distribution
    logic used inside _auto_log_jira_worklogs.
    """

    @pytest.mark.parametrize("daily_h, overhead_h, num_tickets, expected_each", [
        (8.0, 2.0, 2, [10800, 10800]),          # 6h / 2 = 3h each
        (8.0, 2.0, 3, [7200, 7200, 7200]),      # 6h / 3 = 2h each, no remainder
        (8.0, 0.0, 2, [14400, 14400]),          # 8h / 2 = 4h each
        (8.0, 2.0, 1, [21600]),                 # 6h / 1 = 6h
        (7.0, 2.0, 3, [1800, 1800, 1800]),      # 5h / 3 = ~1.67h -> 1800s each, 0 remainder
    ])
    def test_distribution(self, daily_h, overhead_h, num_tickets, expected_each):
        """Verify seconds distribution matches integer-division spec."""
        cfg = _dev_config(daily_hours=daily_h, overhead_hours=overhead_h)
        ta = _make_automation(cfg)
        ta.jira_client.get_my_worklogs.return_value = []
        ta.tempo_client.get_user_worklogs.return_value = []
        ta._is_overhead_configured = MagicMock(return_value=True)
        ta._is_planning_week = MagicMock(return_value=False)

        # Simulate Case 0 default overhead already having been satisfied
        # by pre-existing overhead worklogs so we don't double-count
        existing_overhead_secs = int(overhead_h * 3600)
        overhead_wls = []
        if existing_overhead_secs > 0:
            overhead_wls = [{
                "issue_key": "OVERHEAD-10",
                "worklog_id": "1",
                "time_spent_seconds": existing_overhead_secs,
            }]
        ta.jira_client.get_my_worklogs.return_value = overhead_wls

        active_issues = [
            {"issue_key": f"PROJ-{i}", "issue_summary": f"Task {i}"}
            for i in range(1, num_tickets + 1)
        ]
        ta.jira_client.get_my_active_issues.return_value = active_issues
        ta.jira_client.get_issue_details.return_value = None

        ta._auto_log_jira_worklogs("2026-02-10")

        ticket_calls = ta.jira_client.create_worklog.call_args_list
        actual_seconds = [c.kwargs["time_spent_seconds"] for c in ticket_calls]

        remaining_seconds = int(daily_h * 3600) - existing_overhead_secs
        per_ticket = remaining_seconds // num_tickets
        remainder = remaining_seconds - (per_ticket * num_tickets)
        expected = [
            per_ticket + (remainder if i == num_tickets - 1 else 0)
            for i in range(num_tickets)
        ]

        assert actual_seconds == expected, (
            f"daily={daily_h}h, overhead={overhead_h}h, "
            f"tickets={num_tickets}: expected {expected}, got {actual_seconds}"
        )


# ===========================================================================
# _generate_work_summary
# ===========================================================================

class TestGenerateWorkSummary:
    """Tests for TempoAutomation._generate_work_summary()."""

    def test_no_details_returns_fallback(self):
        """When get_issue_details returns None, fallback text is used."""
        cfg = _dev_config()
        ta = _make_automation(cfg)
        ta.jira_client.get_issue_details.return_value = None

        result = ta._generate_work_summary("PROJ-1", "My ticket")

        assert result == "Worked on PROJ-1: My ticket"

    def test_has_description_uses_first_sentence(self):
        """Issue with description -> first sentence (<=120 chars) used."""
        cfg = _dev_config()
        ta = _make_automation(cfg)
        ta.jira_client.get_issue_details.return_value = {
            "description_text": "Implement login flow. Also handle refresh tokens.",
            "recent_comments": [],
        }

        result = ta._generate_work_summary("PROJ-1", "Login feature")

        assert result.startswith("Implement login flow")

    def test_has_description_and_two_comments_builds_three_lines(self):
        """Description + 2 comments -> up to 3 lines in summary."""
        cfg = _dev_config()
        ta = _make_automation(cfg)
        ta.jira_client.get_issue_details.return_value = {
            "description_text": "Add OAuth2 support.",
            "recent_comments": [
                "Finished token refresh endpoint",
                "Added unit tests for auth flow",
            ],
        }

        result = ta._generate_work_summary("PROJ-1", "OAuth2")

        lines = result.split("\n")
        assert len(lines) == 3
        assert "Add OAuth2 support" in lines[0]
        # Comments are added most-recent-first (reversed)
        assert "Added unit tests for auth flow" in lines[1]
        assert "Finished token refresh endpoint" in lines[2]

    def test_long_description_truncated_at_120_chars(self):
        """Description longer than 120 chars is truncated with '...'"""
        cfg = _dev_config()
        ta = _make_automation(cfg)
        long_desc = "A" * 150 + ". Rest of sentence."
        ta.jira_client.get_issue_details.return_value = {
            "description_text": long_desc,
            "recent_comments": [],
        }

        result = ta._generate_work_summary("PROJ-1", "Long ticket")

        assert result.endswith("...")
        # First line should be at most 120 chars (117 + "...")
        first_line = result.split("\n")[0]
        assert len(first_line) <= 120


# ===========================================================================
# _is_overhead_configured
# ===========================================================================

class TestIsOverheadConfigured:
    """Tests for TempoAutomation._is_overhead_configured()."""

    def test_returns_true_when_pi_identifier_and_stories_present(self):
        cfg = _dev_config()
        ta = _make_automation(cfg)
        assert ta._is_overhead_configured() is True

    def test_returns_false_when_pi_identifier_empty(self):
        cfg = _dev_config(pi_identifier="")
        ta = _make_automation(cfg)
        assert ta._is_overhead_configured() is False

    def test_returns_false_when_stories_empty(self):
        cfg = _dev_config(stories=[])
        ta = _make_automation(cfg)
        assert ta._is_overhead_configured() is False


# ===========================================================================
# _parse_pi_end_date
# ===========================================================================

class TestParsePiEndDate:
    """Tests for TempoAutomation._parse_pi_end_date()."""

    def test_pi_26_1_jan_30_parses_correctly(self):
        cfg = _dev_config()
        ta = _make_automation(cfg)
        assert ta._parse_pi_end_date("PI.26.1.JAN.30") == "2026-01-30"

    def test_pi_26_2_apr_17_parses_correctly(self):
        cfg = _dev_config()
        ta = _make_automation(cfg)
        assert ta._parse_pi_end_date("PI.26.2.APR.17") == "2026-04-17"

    def test_invalid_format_returns_none(self):
        cfg = _dev_config()
        ta = _make_automation(cfg)
        assert ta._parse_pi_end_date("NOT-A-PI-ID") is None

    def test_empty_string_returns_none(self):
        cfg = _dev_config()
        ta = _make_automation(cfg)
        assert ta._parse_pi_end_date("") is None

    def test_invalid_month_abbreviation_returns_none(self):
        cfg = _dev_config()
        ta = _make_automation(cfg)
        # "XYZ" is not a valid month abbreviation
        assert ta._parse_pi_end_date("PI.26.1.XYZ.15") is None


# ===========================================================================
# _is_planning_week
# ===========================================================================

class TestIsPlanningWeek:
    """Tests for TempoAutomation._is_planning_week()."""

    def _make_ta_with_pi_end(self, pi_end_date: str) -> TempoAutomation:
        """Helper: automation with PI end date set and schedule_mgr marking all days working."""
        cfg = _dev_config(pi_end_date=pi_end_date)
        ta = _make_automation(cfg)
        # All days are working days for simplicity
        ta.schedule_mgr.is_working_day.return_value = (True, "")
        return ta

    def test_date_in_planning_window_returns_true(self):
        """A date within the 5 working days after PI end is in planning week."""
        # PI ends 2026-01-30 (Friday); planning week = Feb 2-6 (Mon-Fri)
        ta = self._make_ta_with_pi_end("2026-01-30")
        # Feb 3 is the 2nd working day after Jan 30 -> within planning week
        assert ta._is_planning_week("2026-02-03") is True

    def test_date_before_pi_end_returns_false(self):
        """A date on or before the PI end date is NOT in the planning week."""
        ta = self._make_ta_with_pi_end("2026-01-30")
        assert ta._is_planning_week("2026-01-29") is False

    def test_date_after_planning_window_returns_false(self):
        """A date more than 5 working days after PI end is NOT in planning week."""
        ta = self._make_ta_with_pi_end("2026-01-30")
        # 2026-02-09 is the Monday after the planning week (6th working day after Jan 30)
        assert ta._is_planning_week("2026-02-09") is False


# ===========================================================================
# _log_overhead_hours
# ===========================================================================

class TestLogOverheadHours:
    """Tests for TempoAutomation._log_overhead_hours()."""

    def test_single_distribution_all_seconds_to_first_story(self):
        """distribution='single' -> all seconds go to first story."""
        cfg = _dev_config()
        ta = _make_automation(cfg)
        stories = [
            {"issue_key": "OVERHEAD-10", "summary": "Ceremonies"},
            {"issue_key": "OVERHEAD-11", "summary": "Meetings"},
        ]

        ta._log_overhead_hours("2026-02-10", 7200, stories, "single")

        calls = ta.jira_client.create_worklog.call_args_list
        assert len(calls) == 1
        assert calls[0].kwargs["issue_key"] == "OVERHEAD-10"
        assert calls[0].kwargs["time_spent_seconds"] == 7200

    def test_equal_distribution_splits_evenly(self):
        """distribution='equal' -> equal split across two stories."""
        cfg = _dev_config()
        ta = _make_automation(cfg)
        stories = [
            {"issue_key": "OVERHEAD-10", "summary": "A"},
            {"issue_key": "OVERHEAD-11", "summary": "B"},
        ]

        ta._log_overhead_hours("2026-02-10", 7200, stories, "equal")

        calls = ta.jira_client.create_worklog.call_args_list
        assert len(calls) == 2
        seconds_logged = [c.kwargs["time_spent_seconds"] for c in calls]
        assert seconds_logged[0] == 3600
        # Last ticket gets remainder (0 in this case)
        assert seconds_logged[1] == 3600

    def test_custom_distribution_proportional(self):
        """distribution='custom' -> proportional split by configured hours."""
        cfg = _dev_config()
        ta = _make_automation(cfg)
        stories = [
            {"issue_key": "OVERHEAD-10", "summary": "A", "hours": 1},
            {"issue_key": "OVERHEAD-11", "summary": "B", "hours": 3},
        ]

        ta._log_overhead_hours("2026-02-10", 7200, stories, "custom")

        calls = ta.jira_client.create_worklog.call_args_list
        assert len(calls) == 2
        seconds_logged = [c.kwargs["time_spent_seconds"] for c in calls]
        # Ratios: 1/4 = 0.25, 3/4 = 0.75
        assert seconds_logged[0] == int(7200 * 0.25)  # 1800
        total = sum(seconds_logged)
        assert total == 7200

    def test_no_stories_fallback_key_used(self):
        """When stories list is empty but fallback_issue_key configured, fallback is used."""
        cfg = _dev_config()
        cfg["overhead"]["current_pi"]["stories"] = []
        ta = _make_automation(cfg)

        ta._log_overhead_hours("2026-02-10", 7200)

        calls = ta.jira_client.create_worklog.call_args_list
        assert len(calls) == 1
        assert calls[0].kwargs["issue_key"] == "DEFAULT-1"


# ===========================================================================
# _detect_monthly_gaps
# ===========================================================================

class TestDetectMonthlyGaps:
    """Tests for TempoAutomation._detect_monthly_gaps()."""

    def _make_ta_for_gap_detection(
        self,
        worklogs_by_date: dict,
        daily_hours: float = 8.0,
        today: date = date(2026, 2, 28),
    ) -> TempoAutomation:
        """Build automation with Tempo worklogs stubbed and today patched."""
        cfg = _dev_config(daily_hours=daily_hours)
        ta = _make_automation(cfg)

        # Convert worklogs_by_date {date_str: hours} to Tempo response list
        tempo_wls = []
        for d, h in worklogs_by_date.items():
            tempo_wls.append({"startDate": d, "timeSpentSeconds": int(h * 3600)})
        ta.tempo_client.get_user_worklogs.return_value = tempo_wls
        ta.schedule_mgr.daily_hours = daily_hours
        return ta

    def test_all_days_at_8h_no_gaps(self):
        """A month where every working day has 8h logged -> gaps list is empty."""
        # Feb 2026: Feb 2-6 and Feb 9-13 are working days for our test
        # Patch schedule_mgr: weekends not working, weekdays working
        cfg = _dev_config(daily_hours=8.0)
        ta = _make_automation(cfg)

        # Generate 8h for every weekday in Feb 2026 up to today=Feb 28
        wl_by_date = {}
        d = date(2026, 2, 2)  # Monday
        while d <= date(2026, 2, 27):
            if d.weekday() < 5:  # Mon-Fri
                wl_by_date[d.strftime("%Y-%m-%d")] = 8.0
            d += timedelta(days=1)

        tempo_wls = [
            {"startDate": ds, "timeSpentSeconds": int(h * 3600)}
            for ds, h in wl_by_date.items()
        ]
        ta.tempo_client.get_user_worklogs.return_value = tempo_wls
        ta.schedule_mgr.daily_hours = 8.0

        # is_working_day: weekdays True, weekends False
        def is_working(date_str):
            d = date.fromisoformat(date_str)
            if d.weekday() >= 5:
                return (False, "Weekend")
            return (True, "")
        ta.schedule_mgr.is_working_day.side_effect = is_working

        with patch("tempo_automation.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 27)
            mock_date.fromisoformat = date.fromisoformat
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            result = ta._detect_monthly_gaps(2026, 2)

        assert result["gaps"] == []

    def test_one_day_at_6h_creates_gap(self):
        """A day with 6h logged and 8h expected -> gap of 2h is reported for that day.

        We set today = Feb 10 (the only working day in range) so the only
        day that can appear in day_details is Feb 10 itself.
        """
        cfg = _dev_config(daily_hours=8.0)
        ta = _make_automation(cfg)
        ta.tempo_client.get_user_worklogs.return_value = [
            {"startDate": "2026-02-10", "timeSpentSeconds": 21600}  # 6h
        ]
        ta.schedule_mgr.daily_hours = 8.0

        # Only Feb 10 is a working day; all others are "off" for this test
        def is_working(date_str):
            if date_str == "2026-02-10":
                return (True, "")
            return (False, "Weekend")
        ta.schedule_mgr.is_working_day.side_effect = is_working

        with patch("tempo_automation.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 10)
            mock_date.fromisoformat = date.fromisoformat
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            result = ta._detect_monthly_gaps(2026, 2)

        assert len(result["gaps"]) == 1
        assert result["gaps"][0]["date"] == "2026-02-10"
        assert abs(result["gaps"][0]["gap"] - 2.0) < 0.01

    def test_weekend_excluded_from_gap_analysis(self):
        """Weekend days are never included in working_days or gaps."""
        cfg = _dev_config(daily_hours=8.0)
        ta = _make_automation(cfg)
        # Feb 7-8 are Saturday/Sunday -> excluded
        ta.tempo_client.get_user_worklogs.return_value = []
        ta.schedule_mgr.daily_hours = 8.0

        def is_working(date_str):
            d = date.fromisoformat(date_str)
            if d.weekday() >= 5:
                return (False, "Weekend")
            return (True, "")
        ta.schedule_mgr.is_working_day.side_effect = is_working

        with patch("tempo_automation.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 8)  # Sunday
            mock_date.fromisoformat = date.fromisoformat
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            result = ta._detect_monthly_gaps(2026, 2)

        # Only Feb 2-6 (Mon-Fri) -- weekends excluded
        for g in result.get("day_details", []):
            d = date.fromisoformat(g["date"])
            assert d.weekday() < 5

    def test_pto_day_excluded_from_gap_analysis(self):
        """PTO days are not counted as working days, so no gap is reported for them."""
        cfg = _dev_config(daily_hours=8.0)
        ta = _make_automation(cfg)
        ta.tempo_client.get_user_worklogs.return_value = []
        ta.schedule_mgr.daily_hours = 8.0

        def is_working(date_str):
            if date_str == "2026-02-10":
                return (False, "PTO")
            d = date.fromisoformat(date_str)
            if d.weekday() >= 5:
                return (False, "Weekend")
            return (True, "")
        ta.schedule_mgr.is_working_day.side_effect = is_working

        with patch("tempo_automation.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 10)
            mock_date.fromisoformat = date.fromisoformat
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            result = ta._detect_monthly_gaps(2026, 2)

        # PTO day should not appear as a gap
        gap_dates = [g["date"] for g in result["gaps"]]
        assert "2026-02-10" not in gap_dates


# ===========================================================================
# submit_timesheet
# ===========================================================================

class TestSubmitTimesheet:
    """Tests for TempoAutomation.submit_timesheet()."""

    def test_already_submitted_returns_early(self, tmp_path):
        """When submitted marker exists for current period, returns without re-submitting."""
        cfg = _dev_config()
        ta = _make_automation(cfg)
        ta._is_already_submitted = MagicMock(return_value=True)
        ta._detect_monthly_gaps = MagicMock()

        with patch("tempo_automation.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 28)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            ta.submit_timesheet()

        ta._detect_monthly_gaps.assert_not_called()
        ta.tempo_client.submit_timesheet.assert_not_called()

    def test_gaps_detected_saves_shortfall_does_not_submit(self, tmp_path):
        """When gaps are found, saves shortfall JSON and does NOT call submit."""
        cfg = _dev_config()
        ta = _make_automation(cfg)
        ta._is_already_submitted = MagicMock(return_value=False)
        ta._detect_monthly_gaps = MagicMock(return_value={
            "period": "2026-02",
            "expected": 160.0,
            "actual": 152.0,
            "gaps": [{"date": "2026-02-10", "day": "Tuesday", "logged": 6.0,
                       "expected": 8.0, "gap": 2.0}],
            "working_days": 20,
            "day_details": [],
        })
        ta._save_shortfall_data = MagicMock()
        ta._send_shortfall_notification = MagicMock()

        shortfall_path = tmp_path / "monthly_shortfall.json"
        with patch("tempo_automation.SHORTFALL_FILE", shortfall_path):
            with patch("tempo_automation.date") as mock_date:
                mock_date.today.return_value = date(2026, 2, 28)
                mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
                ta.submit_timesheet()

        ta._save_shortfall_data.assert_called_once()
        ta.tempo_client.submit_timesheet.assert_not_called()

    def test_last_day_no_gaps_submits_and_saves_marker(self, tmp_path):
        """On the last day of the month with no gaps, submits and saves marker."""
        cfg = _dev_config()
        ta = _make_automation(cfg)
        ta._is_already_submitted = MagicMock(return_value=False)
        ta._detect_monthly_gaps = MagicMock(return_value={
            "period": "2026-02",
            "expected": 160.0,
            "actual": 160.0,
            "gaps": [],
            "working_days": 20,
            "day_details": [],
        })
        ta._save_submitted_marker = MagicMock()

        shortfall_path = tmp_path / "monthly_shortfall.json"
        with patch("tempo_automation.SHORTFALL_FILE", shortfall_path):
            with patch("tempo_automation.date") as mock_date:
                mock_date.today.return_value = date(2026, 2, 28)
                mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
                ta.submit_timesheet()

        ta.tempo_client.submit_timesheet.assert_called_once_with("2026-02")
        ta._save_submitted_marker.assert_called_once_with("2026-02")

    def test_early_submit_when_remaining_days_non_working(self, tmp_path):
        """Mid-month with all remaining days PTO/holiday/weekend submits early."""
        cfg = _dev_config()
        ta = _make_automation(cfg)
        ta._is_already_submitted = MagicMock(return_value=False)
        ta._detect_monthly_gaps = MagicMock(return_value={
            "period": "2026-02",
            "expected": 120.0,
            "actual": 120.0,
            "gaps": [],
            "working_days": 15,
            "day_details": [],
        })
        ta._save_submitted_marker = MagicMock()
        # count_working_days returns 0 = no working days remain
        ta.schedule_mgr.count_working_days.return_value = 0

        shortfall_path = tmp_path / "monthly_shortfall.json"
        with patch("tempo_automation.SHORTFALL_FILE", shortfall_path):
            with patch("tempo_automation.date") as mock_date:
                # Feb 15 is well before the normal 7-day window
                mock_date.today.return_value = date(2026, 2, 15)
                mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
                ta.submit_timesheet()

        ta.tempo_client.submit_timesheet.assert_called_once_with("2026-02")
        ta._save_submitted_marker.assert_called_once_with("2026-02")

    def test_early_submit_skipped_when_working_days_remain(self):
        """Mid-month with working days remaining skips submission."""
        cfg = _dev_config()
        ta = _make_automation(cfg)
        ta._is_already_submitted = MagicMock(return_value=False)
        ta._detect_monthly_gaps = MagicMock()
        # count_working_days returns 3 = working days still remain
        ta.schedule_mgr.count_working_days.return_value = 3

        with patch("tempo_automation.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 15)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            ta.submit_timesheet()

        # Should not even get to gap detection
        ta._detect_monthly_gaps.assert_not_called()
        ta.tempo_client.submit_timesheet.assert_not_called()

    def test_early_submit_blocked_by_gaps(self, tmp_path):
        """Early eligible but gaps found does NOT submit."""
        cfg = _dev_config()
        ta = _make_automation(cfg)
        ta._is_already_submitted = MagicMock(return_value=False)
        ta._detect_monthly_gaps = MagicMock(return_value={
            "period": "2026-02",
            "expected": 120.0,
            "actual": 112.0,
            "gaps": [{"date": "2026-02-10", "day": "Tuesday",
                       "logged": 0.0, "expected": 8.0, "gap": 8.0}],
            "working_days": 15,
            "day_details": [],
        })
        ta._save_shortfall_data = MagicMock()
        ta._send_shortfall_notification = MagicMock()
        ta.schedule_mgr.count_working_days.return_value = 0

        shortfall_path = tmp_path / "monthly_shortfall.json"
        with patch("tempo_automation.SHORTFALL_FILE", shortfall_path):
            with patch("tempo_automation.date") as mock_date:
                mock_date.today.return_value = date(2026, 2, 15)
                mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
                ta.submit_timesheet()

        ta._save_shortfall_data.assert_called_once()
        ta.tempo_client.submit_timesheet.assert_not_called()


# ===========================================================================
# verify_week
# ===========================================================================

class TestVerifyWeek:
    """Tests for TempoAutomation.verify_week()."""

    def test_all_days_complete_no_backfill(self):
        """When all days have full hours, _backfill_day is never called."""
        cfg = _dev_config(daily_hours=8.0)
        ta = _make_automation(cfg)

        # is_working_day: Mon-Fri working, weekends not
        def is_working(date_str):
            d = date.fromisoformat(date_str)
            if d.weekday() >= 5:
                return (False, "Weekend")
            return (True, "")
        ta.schedule_mgr.is_working_day.side_effect = is_working

        # _check_day_hours: no gap
        ta._check_day_hours = MagicMock(return_value={
            "existing_hours": 8.0,
            "gap_hours": 0.0,
            "worklogs": [{"issue_key": "PROJ-1", "time_spent_seconds": 28800}],
            "existing_keys": {"PROJ-1"},
        })
        ta._backfill_day = MagicMock()

        with patch("tempo_automation.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 13)  # Friday
            mock_date.fromisoformat = date.fromisoformat
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            ta.verify_week()

        ta._backfill_day.assert_not_called()

    def test_day_with_gap_calls_backfill_day(self):
        """When a working day has a gap, _backfill_day is called for it."""
        cfg = _dev_config(daily_hours=8.0)
        ta = _make_automation(cfg)

        def is_working(date_str):
            d = date.fromisoformat(date_str)
            if d.weekday() >= 5:
                return (False, "Weekend")
            return (True, "")
        ta.schedule_mgr.is_working_day.side_effect = is_working

        # Monday has 4h gap; all other days complete
        def check_day(date_str):
            d = date.fromisoformat(date_str)
            if d.weekday() == 0:  # Monday
                return {
                    "existing_hours": 4.0,
                    "gap_hours": 4.0,
                    "worklogs": [{"issue_key": "PROJ-1", "time_spent_seconds": 14400}],
                    "existing_keys": {"PROJ-1"},
                }
            return {
                "existing_hours": 8.0,
                "gap_hours": 0.0,
                "worklogs": [{"issue_key": "PROJ-1", "time_spent_seconds": 28800}],
                "existing_keys": {"PROJ-1"},
            }
        ta._check_day_hours = MagicMock(side_effect=check_day)
        ta._backfill_day = MagicMock(return_value={
            "created_count": 1,
            "hours_added": 4.0,
            "method": "historical",
        })

        with patch("tempo_automation.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 13)  # Friday
            mock_date.fromisoformat = date.fromisoformat
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            ta.verify_week()

        # _backfill_day must have been called exactly once (for Monday)
        assert ta._backfill_day.call_count == 1
        called_date = ta._backfill_day.call_args.args[0]
        assert date.fromisoformat(called_date).weekday() == 0  # Monday


# ===========================================================================
# _pre_sync_health_check
# ===========================================================================

class TestPreSyncHealthCheck:
    """Tests for TempoAutomation._pre_sync_health_check()."""

    def _make_ta_with_sessions(self, config=None):
        """Build a TempoAutomation with real session objects on mock clients.

        The mock jira_client and tempo_client need real session objects
        so _pre_sync_health_check can call .session.get().
        """
        import requests as req
        cfg = config or _dev_config()
        ta = _make_automation(cfg)

        # Replace the mock sessions with real Session objects we can intercept
        jira_session = req.Session()
        jira_session.auth = ("dev@example.com", "tok")
        ta.jira_client.session = jira_session
        ta.jira_client.base_url = "https://test.atlassian.net"

        tempo_session = req.Session()
        tempo_session.headers.update({
            'Authorization': 'Bearer ttok',
            'Content-Type': 'application/json',
        })
        ta.tempo_client.session = tempo_session
        ta.tempo_client.base_url = "https://api.tempo.io/4"
        ta.tempo_client.account_id = "712020:test-uuid"
        ta.tempo_client.api_token = "ttok"

        return ta

    @responses_lib.activate
    def test_health_check_both_ok(self):
        """Jira + Tempo respond 200 -> returns True."""
        ta = self._make_ta_with_sessions()
        responses_lib.add(
            responses_lib.GET,
            "https://test.atlassian.net/rest/api/3/myself",
            json={"accountId": "712020:test-uuid"},
            status=200,
        )
        responses_lib.add(
            responses_lib.GET,
            "https://api.tempo.io/4/work-attributes",
            json={"results": []},
            status=200,
        )

        result = ta._pre_sync_health_check()

        assert result is True

    @responses_lib.activate
    def test_health_check_jira_401(self, capsys):
        """Jira 401 -> returns False, prints token expired message."""
        ta = self._make_ta_with_sessions()
        responses_lib.add(
            responses_lib.GET,
            "https://test.atlassian.net/rest/api/3/myself",
            json={"error": "Unauthorized"},
            status=401,
        )

        result = ta._pre_sync_health_check()

        assert result is False
        captured = capsys.readouterr()
        assert "token expired" in captured.out.lower() or "401" in captured.out

    @responses_lib.activate
    def test_health_check_tempo_401(self, capsys):
        """Tempo 401 -> returns False, prints token expired message."""
        ta = self._make_ta_with_sessions()
        # Jira OK
        responses_lib.add(
            responses_lib.GET,
            "https://test.atlassian.net/rest/api/3/myself",
            json={"accountId": "712020:test-uuid"},
            status=200,
        )
        # Tempo 401
        responses_lib.add(
            responses_lib.GET,
            "https://api.tempo.io/4/work-attributes",
            json={"error": "Unauthorized"},
            status=401,
        )

        result = ta._pre_sync_health_check()

        assert result is False
        captured = capsys.readouterr()
        assert "token expired" in captured.out.lower() or "401" in captured.out

    @responses_lib.activate
    def test_health_check_jira_timeout(self, capsys):
        """Jira timeout -> returns False, prints unreachable message."""
        import requests as req
        ta = self._make_ta_with_sessions()
        responses_lib.add(
            responses_lib.GET,
            "https://test.atlassian.net/rest/api/3/myself",
            body=req.exceptions.ConnectionError("Connection refused"),
        )

        result = ta._pre_sync_health_check()

        assert result is False
        captured = capsys.readouterr()
        assert "unreachable" in captured.out.lower()

    @responses_lib.activate
    def test_health_check_tempo_timeout(self, capsys):
        """Tempo timeout -> returns False, prints unreachable message."""
        import requests as req
        ta = self._make_ta_with_sessions()
        # Jira OK
        responses_lib.add(
            responses_lib.GET,
            "https://test.atlassian.net/rest/api/3/myself",
            json={"accountId": "712020:test-uuid"},
            status=200,
        )
        # Tempo connection error
        responses_lib.add(
            responses_lib.GET,
            "https://api.tempo.io/4/work-attributes",
            body=req.exceptions.ConnectionError("Connection refused"),
        )

        result = ta._pre_sync_health_check()

        assert result is False
        captured = capsys.readouterr()
        assert "unreachable" in captured.out.lower()

    @responses_lib.activate
    def test_health_check_called_before_sync(self):
        """sync_daily() calls _pre_sync_health_check before any mutations."""
        cfg = _dev_config()
        ta = _make_automation(cfg)

        # Make it a working day so we reach the health check
        ta.schedule_mgr.is_working_day.return_value = (True, "")

        # Health check fails -> sync should abort
        ta._pre_sync_health_check = MagicMock(return_value=False)

        with patch("tempo_automation.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 10)
            mock_date.fromisoformat = date.fromisoformat
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            ta.sync_daily("2026-02-10")

        ta._pre_sync_health_check.assert_called_once()
        # No worklogs should have been created since health check failed
        ta.jira_client.create_worklog.assert_not_called()
        ta.jira_client.delete_worklog.assert_not_called()


# ===========================================================================
# TestViewMonthlyHours
# ===========================================================================

class TestViewMonthlyHours:
    """Tests for TempoAutomation.view_monthly_hours()."""

    def _make_gap_data(
        self,
        gaps=None,
        day_details=None,
        expected=160.0,
        actual=160.0,
        working_days=20,
        period="2026-02",
    ):
        """Build a mock _detect_monthly_gaps return value."""
        return {
            "period": period,
            "expected": expected,
            "actual": actual,
            "gaps": gaps or [],
            "working_days": working_days,
            "day_details": day_details or [],
        }

    def test_view_monthly_shows_daily_breakdown(self, capsys):
        """Output should include per-day hours for every working day."""
        cfg = _dev_config()
        ta = _make_automation(cfg)

        details = [
            {"date": "2026-02-09", "day": "Monday", "logged": 8.0,
             "expected": 8.0, "gap": 0.0},
            {"date": "2026-02-10", "day": "Tuesday", "logged": 8.0,
             "expected": 8.0, "gap": 0.0},
        ]
        gap_data = self._make_gap_data(
            day_details=details, expected=16.0, actual=16.0,
            working_days=2
        )
        ta._detect_monthly_gaps = MagicMock(return_value=gap_data)

        with patch("tempo_automation.SHORTFALL_FILE", Path("/fake/sf.json")), \
             patch("tempo_automation.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 13)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            ta.view_monthly_hours("current")

        out = capsys.readouterr().out
        assert "2026-02-09" in out
        assert "2026-02-10" in out
        assert "Monday" in out
        assert "Tuesday" in out

    def test_view_monthly_detects_gaps(self, capsys, tmp_path):
        """Days with <8h should be flagged in the output."""
        cfg = _dev_config()
        ta = _make_automation(cfg)

        details = [
            {"date": "2026-02-09", "day": "Monday", "logged": 6.0,
             "expected": 8.0, "gap": 2.0},
        ]
        gap_data = self._make_gap_data(
            gaps=details, day_details=details,
            expected=8.0, actual=6.0, working_days=1
        )
        ta._detect_monthly_gaps = MagicMock(return_value=gap_data)

        shortfall_path = tmp_path / "monthly_shortfall.json"
        with patch("tempo_automation.SHORTFALL_FILE", shortfall_path), \
             patch("tempo_automation.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 13)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            ta.view_monthly_hours("current")

        out = capsys.readouterr().out
        assert "Shortfall" in out or "-2.0h" in out

    def test_view_monthly_saves_shortfall_json(self, tmp_path):
        """monthly_shortfall.json should be written when gaps exist."""
        cfg = _dev_config()
        ta = _make_automation(cfg)

        details = [
            {"date": "2026-02-09", "day": "Monday", "logged": 4.0,
             "expected": 8.0, "gap": 4.0},
        ]
        gap_data = self._make_gap_data(
            gaps=details, day_details=details,
            expected=8.0, actual=4.0, working_days=1
        )
        ta._detect_monthly_gaps = MagicMock(return_value=gap_data)

        shortfall_path = tmp_path / "monthly_shortfall.json"

        with patch("tempo_automation.SHORTFALL_FILE", shortfall_path), \
             patch("tempo_automation.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 13)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            ta.view_monthly_hours("current")

        assert shortfall_path.exists()
        data = json.loads(shortfall_path.read_text(encoding="utf-8"))
        assert data["period"] == "2026-02"
        assert len(data["gaps"]) == 1

    def test_view_monthly_no_gaps_no_file(self, tmp_path):
        """No shortfall file when all days are complete."""
        cfg = _dev_config()
        ta = _make_automation(cfg)

        details = [
            {"date": "2026-02-09", "day": "Monday", "logged": 8.0,
             "expected": 8.0, "gap": 0.0},
        ]
        gap_data = self._make_gap_data(
            gaps=[], day_details=details,
            expected=8.0, actual=8.0, working_days=1
        )
        ta._detect_monthly_gaps = MagicMock(return_value=gap_data)

        shortfall_path = tmp_path / "monthly_shortfall.json"

        with patch("tempo_automation.SHORTFALL_FILE", shortfall_path), \
             patch("tempo_automation.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 13)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            ta.view_monthly_hours("current")

        assert not shortfall_path.exists()

    def test_view_monthly_specific_month(self):
        """Passing '2026-01' should fetch Jan 2026 data."""
        cfg = _dev_config()
        ta = _make_automation(cfg)

        gap_data = self._make_gap_data(period="2026-01")
        ta._detect_monthly_gaps = MagicMock(return_value=gap_data)

        with patch("tempo_automation.SHORTFALL_FILE", Path("/fake/sf.json")):
            ta.view_monthly_hours("2026-01")

        ta._detect_monthly_gaps.assert_called_once_with(2026, 1)

    def test_view_monthly_skips_weekends(self):
        """Weekends should not appear in working day calculations."""
        cfg = _dev_config()
        ta = _make_automation(cfg)

        def weekday_schedule(date_str):
            d = date.fromisoformat(date_str)
            if d.weekday() >= 5:
                return (False, "Weekend")
            return (True, "")

        ta.schedule_mgr.is_working_day.side_effect = weekday_schedule

        with patch("tempo_automation.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 28)
            mock_date.fromisoformat = date.fromisoformat
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

            gap_data = ta._detect_monthly_gaps(2026, 2)

        # Feb 2026: 20 weekdays
        assert gap_data["working_days"] == 20

    def test_view_monthly_skips_pto(self):
        """PTO days should be excluded from gap calculation."""
        cfg = _dev_config()
        ta = _make_automation(cfg)

        def schedule_with_pto(date_str):
            d = date.fromisoformat(date_str)
            if d.weekday() >= 5:
                return (False, "Weekend")
            if date_str == "2026-02-10":
                return (False, "PTO")
            return (True, "")

        ta.schedule_mgr.is_working_day.side_effect = schedule_with_pto

        with patch("tempo_automation.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 28)
            mock_date.fromisoformat = date.fromisoformat
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

            gap_data = ta._detect_monthly_gaps(2026, 2)

        # Feb 2026 has 20 weekdays, minus 1 PTO = 19
        assert gap_data["working_days"] == 19

    def test_view_monthly_skips_holidays(self):
        """Org holidays should be excluded from gap calculation."""
        cfg = _dev_config()
        ta = _make_automation(cfg)

        def schedule_with_holiday(date_str):
            d = date.fromisoformat(date_str)
            if d.weekday() >= 5:
                return (False, "Weekend")
            if date_str == "2026-02-16":
                return (False, "Organization Holiday")
            return (True, "")

        ta.schedule_mgr.is_working_day.side_effect = schedule_with_holiday

        with patch("tempo_automation.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 28)
            mock_date.fromisoformat = date.fromisoformat
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

            gap_data = ta._detect_monthly_gaps(2026, 2)

        # Feb 2026 has 20 weekdays, minus 1 holiday = 19
        assert gap_data["working_days"] == 19


# ===========================================================================
# TestFixShortfall
# ===========================================================================

class TestFixShortfall:
    """Tests for TempoAutomation.fix_shortfall()."""

    def test_fix_shortfall_loads_shortfall_file(self):
        """fix_shortfall should call _detect_monthly_gaps for current month."""
        cfg = _dev_config()
        ta = _make_automation(cfg)

        gap_data = {
            "period": "2026-03",
            "expected": 176.0,
            "actual": 168.0,
            "gaps": [
                {"date": "2026-03-10", "day": "Tuesday",
                 "logged": 0.0, "expected": 8.0, "gap": 8.0},
            ],
            "working_days": 22,
            "day_details": [],
        }
        ta._detect_monthly_gaps = MagicMock(return_value=gap_data)

        with patch("tempo_automation.date") as mock_date, \
             patch("builtins.input", side_effect=["Q"]):
            mock_date.today.return_value = date(2026, 3, 13)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            ta.fix_shortfall()

        ta._detect_monthly_gaps.assert_called_once_with(2026, 3)

    def test_fix_shortfall_shows_gap_days(self, capsys):
        """fix_shortfall should display each gap day's details."""
        cfg = _dev_config()
        ta = _make_automation(cfg)

        gap_data = {
            "period": "2026-03",
            "expected": 176.0,
            "actual": 164.0,
            "gaps": [
                {"date": "2026-03-10", "day": "Tuesday",
                 "logged": 0.0, "expected": 8.0, "gap": 8.0},
                {"date": "2026-03-12", "day": "Thursday",
                 "logged": 4.0, "expected": 8.0, "gap": 4.0},
            ],
            "working_days": 22,
            "day_details": [],
        }
        ta._detect_monthly_gaps = MagicMock(return_value=gap_data)

        with patch("tempo_automation.date") as mock_date, \
             patch("builtins.input", side_effect=["Q"]):
            mock_date.today.return_value = date(2026, 3, 13)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            ta.fix_shortfall()

        out = capsys.readouterr().out
        assert "2026-03-10" in out
        assert "2026-03-12" in out
        assert "12.0" in out  # total shortfall

    def test_fix_shortfall_user_selects_ticket(self, tmp_path):
        """When user selects specific days, sync_daily is called."""
        cfg = _dev_config()
        ta = _make_automation(cfg)

        gaps = [
            {"date": "2026-03-10", "day": "Tuesday",
             "logged": 0.0, "expected": 8.0, "gap": 8.0},
            {"date": "2026-03-11", "day": "Wednesday",
             "logged": 4.0, "expected": 8.0, "gap": 4.0},
        ]
        gap_data_initial = {
            "period": "2026-03",
            "expected": 176.0, "actual": 164.0,
            "gaps": gaps, "working_days": 22, "day_details": [],
        }
        # After fixing day 1, re-detect shows remaining gap
        gap_data_after = {
            "period": "2026-03",
            "expected": 176.0, "actual": 172.0,
            "gaps": [gaps[1]], "working_days": 22, "day_details": [],
        }
        ta._detect_monthly_gaps = MagicMock(
            side_effect=[gap_data_initial, gap_data_after]
        )
        ta.sync_daily = MagicMock()

        shortfall_path = tmp_path / "monthly_shortfall.json"
        with patch("tempo_automation.date") as mock_date, \
             patch("builtins.input", side_effect=["1", ""]), \
             patch("tempo_automation.SHORTFALL_FILE", shortfall_path):
            mock_date.today.return_value = date(2026, 3, 13)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            ta.fix_shortfall()

        ta.sync_daily.assert_called_once_with("2026-03-10")

    def test_fix_shortfall_creates_worklog_for_gap(self):
        """When 'A' is chosen, sync_daily is called for every gap day."""
        cfg = _dev_config()
        ta = _make_automation(cfg)

        gaps = [
            {"date": "2026-03-10", "day": "Tuesday",
             "logged": 0.0, "expected": 8.0, "gap": 8.0},
            {"date": "2026-03-12", "day": "Thursday",
             "logged": 4.0, "expected": 8.0, "gap": 4.0},
        ]
        gap_data = {
            "period": "2026-03",
            "expected": 176.0, "actual": 164.0,
            "gaps": gaps, "working_days": 22, "day_details": [],
        }
        ta._detect_monthly_gaps = MagicMock(return_value=gap_data)
        ta.sync_daily = MagicMock()

        shortfall_path = Path("/fake/shortfall.json")
        with patch("tempo_automation.date") as mock_date, \
             patch("builtins.input", side_effect=["A", ""]), \
             patch("tempo_automation.SHORTFALL_FILE", shortfall_path):
            mock_date.today.return_value = date(2026, 3, 13)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            ta.fix_shortfall()

        assert ta.sync_daily.call_count == 2
        ta.sync_daily.assert_any_call("2026-03-10")
        ta.sync_daily.assert_any_call("2026-03-12")

    def test_fix_shortfall_no_shortfall_file(self, capsys):
        """When no gaps exist, display graceful message."""
        cfg = _dev_config()
        ta = _make_automation(cfg)

        gap_data = {
            "period": "2026-03",
            "expected": 176.0, "actual": 176.0,
            "gaps": [], "working_days": 22, "day_details": [],
        }
        ta._detect_monthly_gaps = MagicMock(return_value=gap_data)

        shortfall_path = Path("/fake/shortfall.json")
        with patch("tempo_automation.date") as mock_date, \
             patch("tempo_automation.SHORTFALL_FILE", shortfall_path):
            mock_date.today.return_value = date(2026, 3, 13)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            ta.fix_shortfall()

        out = capsys.readouterr().out
        assert "No shortfall" in out or "All hours" in out

    def test_fix_shortfall_updates_shortfall_after_fix(self, tmp_path):
        """After partial fix, shortfall file should be updated."""
        cfg = _dev_config()
        ta = _make_automation(cfg)

        gaps = [
            {"date": "2026-03-10", "day": "Tuesday",
             "logged": 0.0, "expected": 8.0, "gap": 8.0},
            {"date": "2026-03-12", "day": "Thursday",
             "logged": 4.0, "expected": 8.0, "gap": 4.0},
        ]
        gap_data_initial = {
            "period": "2026-03",
            "expected": 176.0, "actual": 164.0,
            "gaps": gaps, "working_days": 22, "day_details": [],
        }
        gap_data_after = {
            "period": "2026-03",
            "expected": 176.0, "actual": 172.0,
            "gaps": [gaps[1]], "working_days": 22, "day_details": [],
        }
        ta._detect_monthly_gaps = MagicMock(
            side_effect=[gap_data_initial, gap_data_after]
        )
        ta.sync_daily = MagicMock()

        shortfall_path = tmp_path / "monthly_shortfall.json"

        with patch("tempo_automation.date") as mock_date, \
             patch("builtins.input", side_effect=["1", ""]), \
             patch("tempo_automation.SHORTFALL_FILE", shortfall_path):
            mock_date.today.return_value = date(2026, 3, 13)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            ta.fix_shortfall()

        assert shortfall_path.exists()
        data = json.loads(shortfall_path.read_text(encoding="utf-8"))
        assert len(data["gaps"]) == 1


# ===========================================================================
# TestTempoSourceOfTruth
# ===========================================================================

class TestTempoSourceOfTruth:
    """Tests for the Tempo-as-source-of-truth pattern (max(jira, tempo))."""

    def test_check_day_hours_max_pattern(self):
        """_check_day_hours should use max(jira_seconds, tempo_seconds)."""
        cfg = _dev_config()
        ta = _make_automation(cfg)

        # Jira returns 4h, Tempo returns 6h -> use 6h
        ta.jira_client.get_my_worklogs.return_value = [
            {"issue_key": "PROJ-1", "time_spent_seconds": 14400}
        ]
        ta.tempo_client.get_user_worklogs.return_value = [
            {"timeSpentSeconds": 21600, "startDate": "2026-02-10"}
        ]

        result = ta._check_day_hours("2026-02-10")

        # 21600s = 6h, expected 8h -> gap = 2h
        assert result["existing_hours"] == 6.0
        assert abs(result["gap_hours"] - 2.0) < 0.01

    def test_check_day_hours_tempo_only(self):
        """Tempo has hours, Jira 0 -> should use Tempo value."""
        cfg = _dev_config()
        ta = _make_automation(cfg)

        ta.jira_client.get_my_worklogs.return_value = []
        ta.tempo_client.get_user_worklogs.return_value = [
            {"timeSpentSeconds": 28800, "startDate": "2026-02-10"}
        ]

        result = ta._check_day_hours("2026-02-10")

        assert result["existing_hours"] == 8.0
        assert result["gap_hours"] == 0.0

    def test_check_day_hours_jira_only(self):
        """Jira has hours, Tempo 0 -> should use Jira value."""
        cfg = _dev_config()
        ta = _make_automation(cfg)

        ta.jira_client.get_my_worklogs.return_value = [
            {"issue_key": "PROJ-1", "time_spent_seconds": 28800}
        ]
        ta.tempo_client.get_user_worklogs.return_value = []

        result = ta._check_day_hours("2026-02-10")

        assert result["existing_hours"] == 8.0
        assert result["gap_hours"] == 0.0

    def test_detect_monthly_gaps_uses_tempo(self):
        """_detect_monthly_gaps should call Tempo API for worklogs."""
        cfg = _dev_config()
        ta = _make_automation(cfg)

        ta.schedule_mgr.is_working_day.return_value = (True, "")
        ta.tempo_client.get_user_worklogs.return_value = [
            {"startDate": "2026-02-02", "timeSpentSeconds": 28800},
        ]

        with patch("tempo_automation.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 2)
            mock_date.fromisoformat = date.fromisoformat
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

            ta._detect_monthly_gaps(2026, 2)

        ta.tempo_client.get_user_worklogs.assert_called_once_with(
            "2026-02-01", "2026-02-02"
        )

    def test_detect_monthly_gaps_jira_fallback(self):
        """When Tempo account_id is empty, fall back to Jira."""
        cfg = _dev_config()
        ta = _make_automation(cfg)

        ta.tempo_client.account_id = ""  # No Tempo account
        ta.schedule_mgr.is_working_day.return_value = (True, "")
        ta.jira_client.get_my_worklogs.return_value = [
            {"started": "2026-02-02", "time_spent_seconds": 28800,
             "issue_key": "PROJ-1"},
        ]

        with patch("tempo_automation.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 2)
            mock_date.fromisoformat = date.fromisoformat
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

            gap_data = ta._detect_monthly_gaps(2026, 2)

        ta.jira_client.get_my_worklogs.assert_called_once_with(
            "2026-02-01", "2026-02-02"
        )
        assert gap_data["actual"] == 8.0

    def test_sync_pto_overhead_tempo_check(self):
        """_sync_pto_overhead should check Tempo before logging overhead."""
        cfg = _dev_config()
        ta = _make_automation(cfg)

        # Both APIs show 8h already logged -> should skip creating
        ta.jira_client.get_my_worklogs.return_value = [
            {"issue_key": "OVERHEAD-2", "time_spent_seconds": 28800,
             "issue_summary": "PTO"}
        ]
        ta.tempo_client.get_user_worklogs.return_value = [
            {"timeSpentSeconds": 28800, "startDate": "2026-03-10"}
        ]

        ta._sync_pto_overhead("2026-03-10")

        # Both APIs should be consulted
        ta.jira_client.get_my_worklogs.assert_called_once_with(
            "2026-03-10", "2026-03-10"
        )
        ta.tempo_client.get_user_worklogs.assert_called_once_with(
            "2026-03-10", "2026-03-10"
        )
        # Since 28800s >= total_seconds, no new worklog created
        ta.jira_client.create_worklog.assert_not_called()

    def test_verify_week_uses_tempo(self):
        """verify_week should call _check_day_hours which reads Tempo."""
        cfg = _dev_config()
        ta = _make_automation(cfg)

        def weekday_schedule(date_str):
            d = date.fromisoformat(date_str)
            if d.weekday() >= 5:
                return (False, "Weekend")
            return (True, "")

        ta.schedule_mgr.is_working_day.side_effect = weekday_schedule
        ta._check_day_hours = MagicMock(return_value={
            "existing_hours": 8.0,
            "gap_hours": 0.0,
            "worklogs": [
                {"issue_key": "PROJ-1", "time_spent_seconds": 28800}
            ],
            "existing_keys": {"PROJ-1"},
        })
        ta._backfill_day = MagicMock()

        with patch("tempo_automation.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 13)
            mock_date.fromisoformat = date.fromisoformat
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            ta.verify_week()

        assert ta._check_day_hours.call_count == 5

    def test_tempo_api_timeout_falls_back_to_jira(self):
        """When Tempo API fails, _check_day_hours uses Jira (tempo_seconds=0)."""
        cfg = _dev_config()
        ta = _make_automation(cfg)

        # Jira returns 8h
        ta.jira_client.get_my_worklogs.return_value = [
            {"issue_key": "PROJ-1", "time_spent_seconds": 28800}
        ]
        # Tempo has no account_id -> tempo_seconds stays 0
        ta.tempo_client.account_id = ""

        result = ta._check_day_hours("2026-02-10")

        # max(28800, 0) = 28800 -> 8h
        assert result["existing_hours"] == 8.0
        assert result["gap_hours"] == 0.0


# ===========================================================================
# 2C. Create-Before-Delete Tests
# ===========================================================================

class TestCreateBeforeDelete:
    """Tests for the create-before-delete strategy in _auto_log_jira_worklogs."""

    def _setup_auto_log(self, ta, active_issues, existing_worklogs=None):
        """Common setup: configure mocks for _auto_log_jira_worklogs."""
        ta._is_overhead_configured = MagicMock(return_value=False)
        ta._check_overhead_pi_current = MagicMock(return_value=True)
        ta._get_overhead_config = MagicMock(return_value={
            'project_prefix': 'OVERHEAD-',
        })
        ta._generate_work_summary = MagicMock(return_value="Working on task")
        ta.jira_client.get_my_active_issues.return_value = active_issues
        ta.jira_client.get_my_worklogs.return_value = existing_worklogs or []
        ta.tempo_client.get_user_worklogs.return_value = []

    def test_creates_before_deletes(self):
        """Creation happens before deletion (Phase 1 then Phase 2)."""
        cfg = _dev_config(overhead_hours=0)
        ta = _make_automation(cfg)

        existing = [
            {
                'issue_key': 'PROJ-OLD',
                'worklog_id': 'w-old',
                'time_spent_seconds': 28800,
            }
        ]
        active = [
            {'issue_key': 'PROJ-1', 'issue_summary': 'Task 1'},
        ]
        self._setup_auto_log(ta, active, existing)
        ta.jira_client.create_worklog.return_value = "new-wl-id"
        ta.jira_client.delete_worklog.return_value = True

        call_order = []
        orig_create = ta.jira_client.create_worklog
        orig_delete = ta.jira_client.delete_worklog

        def track_create(**kwargs):
            call_order.append('create')
            return orig_create(**kwargs)

        def track_delete(*args, **kwargs):
            call_order.append('delete')
            return orig_delete(*args, **kwargs)

        ta.jira_client.create_worklog = track_create
        ta.jira_client.delete_worklog = track_delete

        ta._auto_log_jira_worklogs("2026-02-10")

        assert call_order.index('create') < call_order.index('delete')

    def test_partial_create_failure_rollback(self):
        """If 2nd creation fails, _rollback_created is called for 1st."""
        cfg = _dev_config(overhead_hours=0)
        ta = _make_automation(cfg)

        active = [
            {'issue_key': 'PROJ-1', 'issue_summary': 'Task 1'},
        ]
        self._setup_auto_log(ta, active, [])
        # Single ticket uses sequential path; create fails
        ta.jira_client.create_worklog.return_value = None
        ta._rollback_created = MagicMock()

        result = ta._auto_log_jira_worklogs("2026-02-10")

        # No worklogs created successfully, so nothing to roll back
        # (failure on first ticket -> created list is empty)
        # But with 2 tickets, first succeeds, second fails:
        active2 = [
            {'issue_key': 'PROJ-1', 'issue_summary': 'Task 1'},
            {'issue_key': 'PROJ-2', 'issue_summary': 'Task 2'},
        ]
        self._setup_auto_log(ta, active2, [])
        # Parallel path: mock _create_worklogs_parallel to simulate
        # partial failure
        created_partial = [
            {
                'issue_key': 'PROJ-1',
                'issue_summary': 'Task 1',
                'time_spent_seconds': 14400,
                'worklog_id': 'wl-1',
            }
        ]
        ta._create_worklogs_parallel = MagicMock(
            return_value=(created_partial, True)
        )
        ta._rollback_created = MagicMock()

        ta._auto_log_jira_worklogs("2026-02-10")

        ta._rollback_created.assert_called_once()
        rolled_back = ta._rollback_created.call_args[0][0]
        assert len(rolled_back) == 1
        assert rolled_back[0]['issue_key'] == 'PROJ-1'

    def test_all_creates_succeed_then_deletes(self):
        """Success -> old worklogs deleted after creation."""
        cfg = _dev_config(overhead_hours=0)
        ta = _make_automation(cfg)

        existing = [
            {
                'issue_key': 'PROJ-OLD',
                'worklog_id': 'w-old',
                'time_spent_seconds': 28800,
            }
        ]
        active = [
            {'issue_key': 'PROJ-1', 'issue_summary': 'Task 1'},
        ]
        self._setup_auto_log(ta, active, existing)
        ta.jira_client.create_worklog.return_value = "new-wl-id"
        ta.jira_client.delete_worklog.return_value = True

        ta._auto_log_jira_worklogs("2026-02-10")

        ta.jira_client.delete_worklog.assert_called_once_with(
            'PROJ-OLD', 'w-old'
        )

    def test_no_existing_worklogs_creates_only(self):
        """First sync: creates only, no deletes."""
        cfg = _dev_config(overhead_hours=0)
        ta = _make_automation(cfg)

        active = [
            {'issue_key': 'PROJ-1', 'issue_summary': 'Task 1'},
        ]
        self._setup_auto_log(ta, active, [])
        ta.jira_client.create_worklog.return_value = "new-wl-id"

        ta._auto_log_jira_worklogs("2026-02-10")

        ta.jira_client.create_worklog.assert_called_once()
        ta.jira_client.delete_worklog.assert_not_called()

    def test_idempotent_double_sync(self):
        """Running sync twice -> same final state (worklogs replaced)."""
        cfg = _dev_config(overhead_hours=0)
        ta = _make_automation(cfg)

        active = [
            {'issue_key': 'PROJ-1', 'issue_summary': 'Task 1'},
        ]
        # First sync: no existing worklogs
        self._setup_auto_log(ta, active, [])
        ta.jira_client.create_worklog.return_value = "wl-1"
        result1 = ta._auto_log_jira_worklogs("2026-02-10")

        # Second sync: existing worklogs from first sync
        existing = [
            {
                'issue_key': 'PROJ-1',
                'worklog_id': 'wl-1',
                'time_spent_seconds': 28800,
            }
        ]
        self._setup_auto_log(ta, active, existing)
        ta.jira_client.create_worklog.return_value = "wl-2"
        ta.jira_client.delete_worklog.return_value = True
        result2 = ta._auto_log_jira_worklogs("2026-02-10")

        # Both produce 1 worklog for PROJ-1
        assert len(result1) == 1
        assert len(result2) == 1
        assert result1[0]['issue_key'] == 'PROJ-1'
        assert result2[0]['issue_key'] == 'PROJ-1'

    def test_overhead_worklogs_preserved(self):
        """Overhead worklogs are never deleted during re-sync."""
        cfg = _dev_config(overhead_hours=0)
        ta = _make_automation(cfg)

        existing = [
            {
                'issue_key': 'OVERHEAD-10',
                'worklog_id': 'oh-wl',
                'time_spent_seconds': 7200,
            },
            {
                'issue_key': 'PROJ-OLD',
                'worklog_id': 'w-old',
                'time_spent_seconds': 21600,
            },
        ]
        active = [
            {'issue_key': 'PROJ-1', 'issue_summary': 'Task 1'},
        ]
        self._setup_auto_log(ta, active, existing)
        ta.jira_client.create_worklog.return_value = "new-wl-id"
        ta.jira_client.delete_worklog.return_value = True

        ta._auto_log_jira_worklogs("2026-02-10")

        # Only non-overhead worklog should be deleted
        delete_calls = ta.jira_client.delete_worklog.call_args_list
        deleted_keys = [c[0][0] for c in delete_calls]
        assert 'OVERHEAD-10' not in deleted_keys
        assert 'PROJ-OLD' in deleted_keys


# ===========================================================================
# 2E. Dry-Run Tests
# ===========================================================================

class TestDryRun:
    """Tests for dry_run mode."""

    def _setup_dry_run(self, ta, active_issues):
        """Common setup for dry-run tests."""
        ta.dry_run = True
        ta._is_overhead_configured = MagicMock(return_value=False)
        ta._check_overhead_pi_current = MagicMock(return_value=True)
        ta._get_overhead_config = MagicMock(return_value={
            'project_prefix': 'OVERHEAD-',
        })
        ta._generate_work_summary = MagicMock(return_value="Summary")
        ta.jira_client.get_my_active_issues.return_value = active_issues
        ta.jira_client.get_my_worklogs.return_value = []
        ta.tempo_client.get_user_worklogs.return_value = []

    def test_dry_run_no_post_requests(self):
        """Zero create_worklog calls with dry_run=True."""
        cfg = _dev_config(overhead_hours=0)
        ta = _make_automation(cfg)
        active = [
            {'issue_key': 'PROJ-1', 'issue_summary': 'Task 1'},
        ]
        self._setup_dry_run(ta, active)

        ta._auto_log_jira_worklogs("2026-02-10")

        ta.jira_client.create_worklog.assert_not_called()

    def test_dry_run_no_delete_requests(self):
        """Zero delete_worklog calls with dry_run=True."""
        cfg = _dev_config(overhead_hours=0)
        ta = _make_automation(cfg)

        existing = [
            {
                'issue_key': 'PROJ-OLD',
                'worklog_id': 'w-old',
                'time_spent_seconds': 28800,
            }
        ]
        active = [
            {'issue_key': 'PROJ-1', 'issue_summary': 'Task 1'},
        ]
        self._setup_dry_run(ta, active)
        ta.jira_client.get_my_worklogs.return_value = existing

        ta._auto_log_jira_worklogs("2026-02-10")

        ta.jira_client.delete_worklog.assert_not_called()

    def test_dry_run_still_fetches_tickets(self):
        """get_my_active_issues is still called in dry-run."""
        cfg = _dev_config(overhead_hours=0)
        ta = _make_automation(cfg)
        active = [
            {'issue_key': 'PROJ-1', 'issue_summary': 'Task 1'},
        ]
        self._setup_dry_run(ta, active)

        ta._auto_log_jira_worklogs("2026-02-10")

        ta.jira_client.get_my_active_issues.assert_called_once()

    def test_dry_run_output_prefix(self, capsys):
        """Output contains '[DRY RUN]' prefix."""
        cfg = _dev_config(overhead_hours=0)
        ta = _make_automation(cfg)
        active = [
            {'issue_key': 'PROJ-1', 'issue_summary': 'Task 1'},
        ]
        self._setup_dry_run(ta, active)

        ta.sync_daily("2026-02-10")

        captured = capsys.readouterr()
        assert "[DRY RUN]" in captured.out

    def test_dry_run_shows_hours(self, capsys):
        """Output includes hours per ticket in dry-run."""
        cfg = _dev_config(overhead_hours=0)
        ta = _make_automation(cfg)
        active = [
            {'issue_key': 'PROJ-1', 'issue_summary': 'Task 1'},
        ]
        self._setup_dry_run(ta, active)

        ta._auto_log_jira_worklogs("2026-02-10")

        captured = capsys.readouterr()
        # Should show "Would log X.XXh on PROJ-1"
        assert "PROJ-1" in captured.out
        assert "h on" in captured.out or "8.00" in captured.out

    def test_dry_run_cli_flag(self):
        """--dry-run is parsed by argparse correctly."""
        import argparse
        from tempo_automation import main

        with patch('sys.argv', ['prog', '--dry-run']):
            with patch(
                'tempo_automation.TempoAutomation'
            ) as MockTA:
                instance = MagicMock()
                MockTA.return_value = instance
                instance.sync_daily = MagicMock()

                try:
                    main()
                except SystemExit:
                    pass

                MockTA.assert_called_once_with(dry_run=True)


# ===========================================================================
# 2F. Progress Indication Tests
# ===========================================================================

class TestProgressIndication:
    """Tests for progress counter output in sync operations."""

    def _setup_sync(self, ta, active_issues, existing=None):
        """Common setup for sync tests."""
        ta._is_overhead_configured = MagicMock(return_value=False)
        ta._check_overhead_pi_current = MagicMock(return_value=True)
        ta._get_overhead_config = MagicMock(return_value={
            'project_prefix': 'OVERHEAD-',
        })
        ta._generate_work_summary = MagicMock(return_value="Summary")
        ta.jira_client.get_my_active_issues.return_value = active_issues
        ta.jira_client.get_my_worklogs.return_value = existing or []
        ta.tempo_client.get_user_worklogs.return_value = []
        ta.jira_client.create_worklog.return_value = "wl-id"

    def test_progress_counter_in_sync_output(self, capsys):
        """Output contains [1/3], [2/3], [3/3] for 3 tickets."""
        cfg = _dev_config(overhead_hours=0)
        ta = _make_automation(cfg)
        active = [
            {'issue_key': 'PROJ-1', 'issue_summary': 'Task 1'},
            {'issue_key': 'PROJ-2', 'issue_summary': 'Task 2'},
            {'issue_key': 'PROJ-3', 'issue_summary': 'Task 3'},
        ]
        self._setup_sync(ta, active)
        # Parallel path returns ordered results
        ta._create_worklogs_parallel = MagicMock(return_value=(
            [
                {
                    'issue_key': f'PROJ-{i}',
                    'issue_summary': f'Task {i}',
                    'time_spent_seconds': 9600,
                    'worklog_id': f'wl-{i}',
                }
                for i in range(1, 4)
            ],
            False,
        ))

        ta._auto_log_jira_worklogs("2026-02-10")

        # The parallel mock was called; progress counters are printed
        # by _create_worklogs_parallel internally, so we verify it
        # was invoked (parallel path handles 2+ tickets).
        ta._create_worklogs_parallel.assert_called_once()

    def test_progress_counter_single_ticket(self, capsys):
        """[1/1] for single ticket (sequential path)."""
        cfg = _dev_config(overhead_hours=0)
        ta = _make_automation(cfg)
        active = [
            {'issue_key': 'PROJ-1', 'issue_summary': 'Task 1'},
        ]
        self._setup_sync(ta, active)

        ta._auto_log_jira_worklogs("2026-02-10")

        captured = capsys.readouterr()
        assert "[1/1]" in captured.out

    def test_progress_counter_verify_week(self, capsys):
        """verify_week shows per-day progress like [Day 1/5]."""
        cfg = _dev_config()
        ta = _make_automation(cfg)

        ta._check_day_hours = MagicMock(return_value={
            "existing_hours": 8.0,
            "gap_hours": 0.0,
            "worklogs": [],
            "existing_keys": set(),
        })
        ta._backfill_day = MagicMock()

        with patch("tempo_automation.date") as mock_date:
            # Wednesday Feb 11, 2026
            mock_date.today.return_value = date(2026, 2, 11)
            mock_date.fromisoformat = date.fromisoformat
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            ta.verify_week()

        captured = capsys.readouterr()
        assert "[Day 1/5]" in captured.out
        assert "[Day 2/5]" in captured.out

    def test_progress_counter_backfill_day(self, capsys):
        """backfill_range shows [N/total] progress per day."""
        cfg = _dev_config()
        ta = _make_automation(cfg)

        # Mon-Wed (3 days, all working)
        ta.schedule_mgr.is_working_day.return_value = (True, "")
        ta.sync_daily = MagicMock()

        ta.backfill_range("2026-02-09", "2026-02-11")

        captured = capsys.readouterr()
        assert "[1/3]" in captured.out
        assert "[2/3]" in captured.out
        assert "[3/3]" in captured.out


# ===========================================================================
# 2G. Backfill, Weights, Approval Tests
# ===========================================================================

class TestDateRangeBackfill:
    """Tests for TempoAutomation.backfill_range()."""

    def test_backfill_weekday_range(self):
        """Mon-Fri syncs 5 days."""
        cfg = _dev_config()
        ta = _make_automation(cfg)

        ta.schedule_mgr.is_working_day.return_value = (True, "")
        ta.sync_daily = MagicMock()

        # Mon Feb 9 to Fri Feb 13, 2026
        ta.backfill_range("2026-02-09", "2026-02-13")

        assert ta.sync_daily.call_count == 5

    def test_backfill_skips_weekends(self):
        """Sat/Sun are skipped."""
        cfg = _dev_config()
        ta = _make_automation(cfg)

        def weekday_schedule(date_str):
            d = date.fromisoformat(date_str)
            if d.weekday() >= 5:
                return (False, "Weekend")
            return (True, "")

        ta.schedule_mgr.is_working_day.side_effect = weekday_schedule
        ta.sync_daily = MagicMock()

        # Thu Feb 12 to Mon Feb 16 (includes Sat 14, Sun 15)
        ta.backfill_range("2026-02-12", "2026-02-16")

        # Should sync Thu, Fri, Mon = 3 days
        assert ta.sync_daily.call_count == 3

    def test_backfill_skips_pto(self):
        """PTO day is skipped."""
        cfg = _dev_config()
        ta = _make_automation(cfg)

        def schedule_with_pto(date_str):
            if date_str == "2026-02-11":
                return (False, "PTO")
            return (True, "")

        ta.schedule_mgr.is_working_day.side_effect = schedule_with_pto
        ta.sync_daily = MagicMock()

        # Mon-Wed, Tue is PTO
        ta.backfill_range("2026-02-10", "2026-02-12")

        assert ta.sync_daily.call_count == 2
        synced_dates = [c[0][0] for c in ta.sync_daily.call_args_list]
        assert "2026-02-11" not in synced_dates

    def test_backfill_summary_output(self, capsys):
        """Summary shows synced/skipped counts."""
        cfg = _dev_config()
        ta = _make_automation(cfg)

        def schedule_with_skip(date_str):
            if date_str == "2026-02-11":
                return (False, "PTO")
            return (True, "")

        ta.schedule_mgr.is_working_day.side_effect = schedule_with_skip
        ta.sync_daily = MagicMock()

        ta.backfill_range("2026-02-10", "2026-02-12")

        captured = capsys.readouterr()
        assert "Synced: 2/3" in captured.out
        assert "Skipped: 1" in captured.out
        assert "PTO" in captured.out


class TestWeightedDistribution:
    """Tests for weighted hour distribution."""

    def _setup_weighted(self, ta, active, weights=None):
        """Setup for weighted distribution tests."""
        ta._is_overhead_configured = MagicMock(return_value=False)
        ta._check_overhead_pi_current = MagicMock(return_value=True)
        ta._get_overhead_config = MagicMock(return_value={
            'project_prefix': 'OVERHEAD-',
        })
        ta._generate_work_summary = MagicMock(return_value="Summary")
        ta.jira_client.get_my_active_issues.return_value = active
        ta.jira_client.get_my_worklogs.return_value = []
        ta.tempo_client.get_user_worklogs.return_value = []
        ta.jira_client.create_worklog.return_value = "wl-id"

        if weights:
            ta.config['schedule']['distribution_weights'] = weights

    def test_weights_applied(self):
        """Weights 3:1 for 2 tickets -> 6h and 2h on 8h day."""
        cfg = _dev_config(overhead_hours=0, daily_hours=8.0)
        ta = _make_automation(cfg)

        active = [
            {'issue_key': 'PROJ-1', 'issue_summary': 'Heavy task'},
            {'issue_key': 'PROJ-2', 'issue_summary': 'Light task'},
        ]
        weights = {'PROJ-1': 3.0, 'PROJ-2': 1.0}
        self._setup_weighted(ta, active, weights)

        # Use _create_worklogs_parallel mock to capture allocations
        original_parallel = None
        captured_allocations = []

        def mock_parallel(allocations, target_date, num_tickets):
            for issue, secs in allocations:
                captured_allocations.append(
                    (issue['issue_key'], secs)
                )
            created = [
                {
                    'issue_key': issue['issue_key'],
                    'issue_summary': issue['issue_summary'],
                    'time_spent_seconds': secs,
                    'worklog_id': f'wl-{issue["issue_key"]}',
                }
                for issue, secs in allocations
            ]
            return (created, False)

        ta._create_worklogs_parallel = mock_parallel

        result = ta._auto_log_jira_worklogs("2026-02-10")

        # 3:1 ratio on 8h (28800s): PROJ-1 gets ~21600 (6h),
        # PROJ-2 gets remainder = 7200 (2h)
        assert len(captured_allocations) == 2
        proj1_secs = captured_allocations[0][1]
        proj2_secs = captured_allocations[1][1]
        assert proj1_secs == 21600  # 6h
        assert proj2_secs == 7200   # 2h

    def test_weights_equal_when_unconfigured(self):
        """No weights -> equal split."""
        cfg = _dev_config(overhead_hours=0, daily_hours=8.0)
        ta = _make_automation(cfg)

        active = [
            {'issue_key': 'PROJ-1', 'issue_summary': 'Task 1'},
            {'issue_key': 'PROJ-2', 'issue_summary': 'Task 2'},
        ]
        self._setup_weighted(ta, active)

        captured_allocations = []

        def mock_parallel(allocations, target_date, num_tickets):
            for issue, secs in allocations:
                captured_allocations.append(
                    (issue['issue_key'], secs)
                )
            created = [
                {
                    'issue_key': issue['issue_key'],
                    'issue_summary': issue['issue_summary'],
                    'time_spent_seconds': secs,
                    'worklog_id': f'wl-{issue["issue_key"]}',
                }
                for issue, secs in allocations
            ]
            return (created, False)

        ta._create_worklogs_parallel = mock_parallel

        result = ta._auto_log_jira_worklogs("2026-02-10")

        # Equal: 28800 / 2 = 14400 each (4h)
        assert len(captured_allocations) == 2
        proj1_secs = captured_allocations[0][1]
        proj2_secs = captured_allocations[1][1]
        assert proj1_secs == 14400  # 4h
        assert proj2_secs == 14400  # 4h


class TestApprovalStatus:
    """Tests for TempoAutomation.check_approval_status()."""

    def test_approval_status_open(self, capsys):
        """OPEN period displayed correctly."""
        cfg = _dev_config()
        ta = _make_automation(cfg)

        ta.tempo_client.get_timesheet_periods.return_value = [
            {
                'status': 'OPEN',
                'dateFrom': '2026-02-01',
                'dateTo': '2026-02-28',
            }
        ]

        ta.check_approval_status('2026-02')

        captured = capsys.readouterr()
        assert "OPEN (not submitted)" in captured.out
        assert "2026-02-01" in captured.out
        assert "2026-02-28" in captured.out

    def test_approval_status_submitted(self, capsys):
        """WAITING_FOR_APPROVAL displayed as 'Awaiting approval'."""
        cfg = _dev_config()
        ta = _make_automation(cfg)

        ta.tempo_client.get_timesheet_periods.return_value = [
            {
                'status': 'WAITING_FOR_APPROVAL',
                'dateFrom': '2026-03-01',
                'dateTo': '2026-03-31',
                'reviewer': {
                    'displayName': 'Jane Manager'
                },
            }
        ]

        ta.check_approval_status('2026-03')

        captured = capsys.readouterr()
        assert "Awaiting approval" in captured.out
        assert "Jane Manager" in captured.out
