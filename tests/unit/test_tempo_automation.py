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
    jc.create_worklog.return_value = True
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

    def test_idempotent_deletes_non_overhead_before_creating(self):
        """Non-overhead Jira worklogs are deleted before new ones are created."""
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
        ta.jira_client.create_worklog.side_effect = lambda **kw: call_order.append("create") or True

        ta._auto_log_jira_worklogs("2026-02-10")

        # delete must happen before create
        assert call_order.index("delete") < call_order.index("create")

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
