"""
Integration tests for the daily sync flow (tempo_automation.py).

Strategy
--------
These tests exercise the full orchestration path through sync_daily(),
verifying that the correct collaborator methods are called in the correct
order with the correct arguments.  No real HTTP calls are made -- all
collaborators (JiraClient, TempoClient, ScheduleManager, Notifier) are
Mocks injected via ``_make_automation()``.

The tests are "integration" in the sense that they span multiple internal
methods (sync_daily -> _auto_log_jira_worklogs -> _log_overhead_hours,
etc.) rather than testing a single method in isolation.

Coverage (~10 tests)
--------------------
- TestDeveloperDailySyncFlow (8 tests)
  * Working day full flow (overhead + 2 tickets)
  * PTO day flow (overhead to pto_story_key)
  * Holiday flow (same as PTO)
  * Weekend flow (skip entirely)
  * No active issues flow (all hours to overhead)
  * No overhead configured flow (full hours to tickets)
  * Idempotent overwrite (delete then create)
  * Notification always sent on working day

- TestProductOwnerDailySyncFlow (2 tests)
  * PO daily sync with manual activities
  * PO PTO day skips (no Jira calls)
"""

import sys
from datetime import date
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
# Helper: build a TempoAutomation without triggering __init__
# ---------------------------------------------------------------------------

def _make_automation(config: dict) -> TempoAutomation:
    """
    Create a TempoAutomation instance without calling __init__.

    Manually attaches mock collaborators so each test starts clean and
    deterministic.  The caller can configure the mocks as needed.
    """
    ta = object.__new__(TempoAutomation)

    ta.config = config
    ta.config_manager = MagicMock()
    ta.config_manager.config = config
    ta.config_manager.config_path = Path("/fake/config.json")
    ta.dry_run = False

    # ScheduleManager mock
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


# ===========================================================================
# Developer daily sync flow
# ===========================================================================

@pytest.mark.integration
class TestDeveloperDailySyncFlow:
    """Full developer daily sync from start to finish."""

    @pytest.fixture
    def dev_config(self, developer_config):
        """Return the developer_config fixture from conftest."""
        return developer_config

    def test_working_day_full_flow(self, dev_config):
        """Working day with 2 active issues and overhead configured.

        Expected flow:
        1. schedule_mgr.is_working_day -> (True, "")
        2. _auto_log_jira_worklogs called
        3. Overhead (2h) logged first via jira_client.create_worklog
        4. Remaining 6h distributed: 3h to each of 2 tickets
        5. jira_client.create_worklog called 3 times total
        6. notifier.send_daily_summary called with all 3 worklogs
        """
        ta = _make_automation(dev_config)

        # No existing worklogs -> fresh day
        ta.jira_client.get_my_worklogs.return_value = []
        ta.tempo_client.get_user_worklogs.return_value = []

        # 2 active issues
        ta.jira_client.get_my_active_issues.return_value = [
            {"issue_key": "PROJ-101", "issue_summary": "Implement auth"},
            {"issue_key": "PROJ-102", "issue_summary": "Add search"},
        ]

        ta.sync_daily("2026-02-10")

        # Verify overhead was logged (2h = 7200s)
        create_calls = ta.jira_client.create_worklog.call_args_list
        assert len(create_calls) == 3, (
            f"Expected 3 create_worklog calls (1 overhead + 2 tickets), "
            f"got {len(create_calls)}"
        )

        # First call should be overhead (OVERHEAD-10, 7200s)
        oh_call = create_calls[0]
        assert oh_call.kwargs["issue_key"] == "OVERHEAD-10"
        assert oh_call.kwargs["time_spent_seconds"] == 7200

        # Remaining 6h = 21600s across 2 tickets = 10800s each
        ticket_seconds = [
            c.kwargs["time_spent_seconds"] for c in create_calls[1:]
        ]
        assert ticket_seconds == [10800, 10800]
        assert sum(ticket_seconds) + 7200 == 28800  # 8h total

        # Notification sent
        ta.notifier.send_daily_summary.assert_called_once()
        summary_worklogs = (
            ta.notifier.send_daily_summary.call_args.args[0]
        )
        assert len(summary_worklogs) == 3

    def test_pto_day_flow(self, dev_config):
        """PTO day: should log 8h to pto_story_key only.

        Expected flow:
        1. schedule_mgr.is_working_day -> (False, "PTO")
        2. _is_overhead_configured -> True
        3. _sync_pto_overhead called
        4. jira_client.create_worklog called once with OVERHEAD-2 and 8h
        5. notifier.send_daily_summary called
        """
        ta = _make_automation(dev_config)

        ta.schedule_mgr.is_working_day.return_value = (False, "PTO")

        # No existing worklogs for this PTO day
        ta.jira_client.get_my_worklogs.return_value = []
        ta.tempo_client.get_user_worklogs.return_value = []

        ta.sync_daily("2026-03-10")

        # create_worklog should be called with pto_story_key
        create_calls = ta.jira_client.create_worklog.call_args_list
        assert len(create_calls) == 1, (
            f"Expected 1 create_worklog call for PTO overhead, "
            f"got {len(create_calls)}"
        )
        assert create_calls[0].kwargs["issue_key"] == "OVERHEAD-2"
        assert create_calls[0].kwargs["time_spent_seconds"] == 28800

        # Notification sent (PTO sync also sends summary)
        ta.notifier.send_daily_summary.assert_called_once()

        # get_my_active_issues should NOT be called (PTO, not working day)
        ta.jira_client.get_my_active_issues.assert_not_called()

    def test_holiday_flow(self, dev_config):
        """Holiday (not weekend): same behavior as PTO.

        Logs full daily hours to pto_story_key overhead.
        """
        ta = _make_automation(dev_config)

        ta.schedule_mgr.is_working_day.return_value = (
            False, "Organization Holiday"
        )

        ta.jira_client.get_my_worklogs.return_value = []
        ta.tempo_client.get_user_worklogs.return_value = []

        ta.sync_daily("2026-01-20")

        # Should log overhead hours just like PTO
        create_calls = ta.jira_client.create_worklog.call_args_list
        assert len(create_calls) == 1
        assert create_calls[0].kwargs["issue_key"] == "OVERHEAD-2"
        assert create_calls[0].kwargs["time_spent_seconds"] == 28800

    def test_weekend_flow(self, dev_config):
        """Weekend: skip entirely, no API calls at all."""
        ta = _make_automation(dev_config)

        ta.schedule_mgr.is_working_day.return_value = (False, "Weekend")

        ta.sync_daily("2026-02-21")  # Saturday

        ta.jira_client.get_my_active_issues.assert_not_called()
        ta.jira_client.get_my_worklogs.assert_not_called()
        ta.jira_client.create_worklog.assert_not_called()
        ta.tempo_client.get_user_worklogs.assert_not_called()
        ta.notifier.send_daily_summary.assert_not_called()

    def test_no_active_issues_flow(self, dev_config):
        """Working day but no active issues: all hours go to overhead.

        Expected:
        1. Overhead (2h) logged first
        2. No active issues found
        3. Remaining 6h logged to overhead stories
        4. Total: 8h on overhead
        """
        ta = _make_automation(dev_config)

        ta.jira_client.get_my_worklogs.return_value = []
        ta.tempo_client.get_user_worklogs.return_value = []
        ta.jira_client.get_my_active_issues.return_value = []

        ta.sync_daily("2026-02-10")

        # All create_worklog calls should target OVERHEAD stories
        create_calls = ta.jira_client.create_worklog.call_args_list
        total_seconds = sum(
            c.kwargs["time_spent_seconds"] for c in create_calls
        )
        assert total_seconds == 28800, (
            f"Expected 28800s (8h) total overhead, got {total_seconds}"
        )

        for c in create_calls:
            key = c.kwargs["issue_key"]
            assert key.startswith("OVERHEAD") or key == "DEFAULT-1", (
                f"Non-overhead issue {key} logged when no active issues"
            )

    def test_no_overhead_configured_flow(self, dev_config):
        """Working day, 2 active issues, NO overhead configured.

        Expected: full 8h distributed across 2 tickets (4h each).
        """
        # Remove overhead configuration
        dev_config["overhead"]["current_pi"]["pi_identifier"] = ""
        dev_config["overhead"]["current_pi"]["stories"] = []
        dev_config["overhead"]["daily_overhead_hours"] = 0

        ta = _make_automation(dev_config)

        ta.jira_client.get_my_worklogs.return_value = []
        ta.tempo_client.get_user_worklogs.return_value = []
        ta.jira_client.get_my_active_issues.return_value = [
            {"issue_key": "PROJ-101", "issue_summary": "Auth feature"},
            {"issue_key": "PROJ-102", "issue_summary": "Search feature"},
        ]

        ta.sync_daily("2026-02-10")

        create_calls = ta.jira_client.create_worklog.call_args_list
        ticket_seconds = [
            c.kwargs["time_spent_seconds"] for c in create_calls
        ]
        # Full 8h = 28800s across 2 tickets: 14400s each
        assert ticket_seconds == [14400, 14400]
        assert sum(ticket_seconds) == 28800

    def test_idempotent_overwrite_creates_before_deleting(self, dev_config):
        """New worklogs are created before old non-overhead ones are deleted.

        Tests the safe create-before-delete flow:
        1. Existing PROJ-OLD worklog found
        2. New worklogs created for active tickets
        3. Only then PROJ-OLD deleted
        4. Create happens before delete (safe pattern)
        """
        ta = _make_automation(dev_config)

        # Existing non-overhead worklog from a previous sync
        existing_wl = {
            "issue_key": "PROJ-OLD",
            "worklog_id": "888",
            "time_spent_seconds": 21600,
        }
        ta.jira_client.get_my_worklogs.return_value = [existing_wl]
        ta.tempo_client.get_user_worklogs.return_value = []
        ta.jira_client.get_my_active_issues.return_value = [
            {"issue_key": "PROJ-NEW", "issue_summary": "New task"},
        ]

        # Track call order
        call_order = []
        ta.jira_client.delete_worklog.side_effect = (
            lambda *a, **kw: call_order.append("delete") or True
        )
        ta.jira_client.create_worklog.side_effect = (
            lambda **kw: call_order.append("create") or "99"
        )

        ta.sync_daily("2026-02-10")

        # Create must happen before any delete (safe pattern)
        assert "delete" in call_order, "delete_worklog was never called"
        assert "create" in call_order, "create_worklog was never called"
        first_delete = call_order.index("delete")
        first_create = call_order.index("create")
        assert first_create < first_delete, (
            f"Create at index {first_create} should precede "
            f"delete at index {first_delete}"
        )

    def test_notification_sent_on_working_day(self, dev_config):
        """Notification is always sent after a successful working day sync."""
        ta = _make_automation(dev_config)

        ta.jira_client.get_my_worklogs.return_value = []
        ta.tempo_client.get_user_worklogs.return_value = []
        ta.jira_client.get_my_active_issues.return_value = [
            {"issue_key": "PROJ-1", "issue_summary": "Task A"},
        ]

        ta.sync_daily("2026-02-10")

        ta.notifier.send_daily_summary.assert_called_once()
        args = ta.notifier.send_daily_summary.call_args
        worklogs_arg = args.args[0]
        hours_arg = args.args[1]

        # At least one worklog should be present
        assert len(worklogs_arg) >= 1
        # Total hours should be daily_hours (8.0)
        assert hours_arg == 8.0


# ===========================================================================
# Product Owner daily sync flow
# ===========================================================================

@pytest.mark.integration
class TestProductOwnerDailySyncFlow:
    """PO role: Tempo-only, manual activities."""

    @pytest.fixture
    def po_cfg(self, po_config):
        """Return the po_config fixture from conftest."""
        return po_config

    def test_po_daily_sync_manual_activities(self, po_cfg):
        """PO working day: creates Tempo worklogs for manual activities.

        Expected:
        1. role == product_owner -> _sync_manual_activities called
        2. tempo_client.create_worklog called for each manual activity
        3. No jira_client.create_worklog calls
        """
        ta = _make_automation(po_cfg)

        # No existing Tempo entries for today
        ta.tempo_client.get_user_worklogs.return_value = []
        ta.tempo_client.create_worklog.return_value = True

        ta.sync_daily("2026-02-10")

        # Tempo create_worklog should be called for each manual activity
        # po_config has 3 manual activities: 3h + 2h + 3h = 8h
        tempo_calls = ta.tempo_client.create_worklog.call_args_list
        assert len(tempo_calls) == 3, (
            f"Expected 3 tempo_client.create_worklog calls for 3 "
            f"manual activities, got {len(tempo_calls)}"
        )

        # Verify activities and hours
        activities_logged = []
        for c in tempo_calls:
            activities_logged.append({
                "description": c.kwargs.get(
                    "description", c.args[3] if len(c.args) > 3 else ""
                ),
                "time_seconds": c.kwargs.get(
                    "time_seconds", c.args[1] if len(c.args) > 1 else 0
                ),
            })

        total_seconds = sum(a["time_seconds"] for a in activities_logged)
        assert total_seconds == 28800, (
            f"Expected 28800s (8h) total, got {total_seconds}"
        )

        # Jira client should NOT be used for worklog creation
        ta.jira_client.create_worklog.assert_not_called()

    def test_po_pto_day_skips(self, po_cfg):
        """PO on PTO day: skip entirely (no overhead for non-developers)."""
        ta = _make_automation(po_cfg)

        ta.schedule_mgr.is_working_day.return_value = (False, "PTO")

        ta.sync_daily("2026-03-10")

        ta.jira_client.create_worklog.assert_not_called()
        ta.tempo_client.create_worklog.assert_not_called()
        ta.notifier.send_daily_summary.assert_not_called()


# ===========================================================================
# Overhead Cases (integration)
# ===========================================================================

@pytest.mark.integration
class TestOverheadCases:
    """Integration tests for the 5 overhead story cases."""

    @pytest.fixture
    def dev_config(self, developer_config):
        """Return the developer_config fixture from conftest."""
        return developer_config

    def test_case0_default_daily_overhead(self, dev_config):
        """Case 0: 2h overhead + remaining 6h to 2 active tickets.

        Expected: 3 create_worklog calls (1 overhead + 2 tickets),
        total = 8h.
        """
        ta = _make_automation(dev_config)

        ta.jira_client.get_my_worklogs.return_value = []
        ta.tempo_client.get_user_worklogs.return_value = []
        ta.jira_client.get_my_active_issues.return_value = [
            {"issue_key": "PROJ-101", "issue_summary": "Auth"},
            {"issue_key": "PROJ-102", "issue_summary": "Search"},
        ]

        ta.sync_daily("2026-02-10")

        create_calls = ta.jira_client.create_worklog.call_args_list
        assert len(create_calls) == 3

        # First call: overhead (2h = 7200s)
        assert create_calls[0].kwargs["issue_key"] == "OVERHEAD-10"
        assert create_calls[0].kwargs["time_spent_seconds"] == 7200

        # Remaining: 6h / 2 = 3h each
        ticket_seconds = [
            c.kwargs["time_spent_seconds"] for c in create_calls[1:]
        ]
        assert ticket_seconds == [10800, 10800]
        total = sum(c.kwargs["time_spent_seconds"] for c in create_calls)
        assert total == 28800  # 8h

    def test_case1_no_active_tickets_all_overhead(self, dev_config):
        """Case 1: No active tickets -> all 8h to overhead stories."""
        ta = _make_automation(dev_config)

        ta.jira_client.get_my_worklogs.return_value = []
        ta.tempo_client.get_user_worklogs.return_value = []
        ta.jira_client.get_my_active_issues.return_value = []

        ta.sync_daily("2026-02-10")

        create_calls = ta.jira_client.create_worklog.call_args_list
        total = sum(c.kwargs["time_spent_seconds"] for c in create_calls)
        assert total == 28800

        for c in create_calls:
            key = c.kwargs["issue_key"]
            assert key.startswith("OVERHEAD") or key == "DEFAULT-1"

    def test_case2_manual_overhead_preserved(self, dev_config):
        """Case 2: Existing overhead worklogs are not deleted.

        When a day already has overhead logged, the overwrite logic
        should preserve those entries.
        """
        ta = _make_automation(dev_config)

        # Existing overhead worklog (should NOT be deleted)
        existing_overhead = {
            "issue_key": "OVERHEAD-10",
            "worklog_id": "999",
            "time_spent_seconds": 7200,
        }
        ta.jira_client.get_my_worklogs.return_value = [existing_overhead]
        ta.tempo_client.get_user_worklogs.return_value = []
        ta.jira_client.get_my_active_issues.return_value = [
            {"issue_key": "PROJ-101", "issue_summary": "Auth"},
        ]

        # Track which worklogs are deleted
        deleted_keys = []
        ta.jira_client.delete_worklog.side_effect = (
            lambda key, wid: deleted_keys.append(key) or True
        )

        ta.sync_daily("2026-02-10")

        # OVERHEAD-10 should NOT be in the deleted list
        assert "OVERHEAD-10" not in deleted_keys

    def test_case3_pto_holiday_overhead(self, dev_config):
        """Case 3: PTO day -> 8h logged to pto_story_key."""
        ta = _make_automation(dev_config)

        ta.schedule_mgr.is_working_day.return_value = (False, "PTO")
        ta.jira_client.get_my_worklogs.return_value = []
        ta.tempo_client.get_user_worklogs.return_value = []

        ta.sync_daily("2026-03-10")

        create_calls = ta.jira_client.create_worklog.call_args_list
        assert len(create_calls) == 1
        assert create_calls[0].kwargs["issue_key"] == "OVERHEAD-2"
        assert create_calls[0].kwargs["time_spent_seconds"] == 28800

        # Active issues should not be fetched for PTO
        ta.jira_client.get_my_active_issues.assert_not_called()

    def test_case4_planning_week_full_flow(self, dev_config):
        """Case 4: Planning week -> overhead stories from planning_pi.

        When the current date falls in the planning week (5 working
        days after PI end), all hours go to overhead (no active tickets).
        """
        # Configure planning PI with its own stories
        dev_config["overhead"]["planning_pi"] = {
            "pi_identifier": "PI.26.2.APR.17",
            "pi_end_date": "2026-04-17",
            "stories": [
                {"issue_key": "OVERHEAD-20", "summary": "PI Planning",
                 "hours": 8},
            ],
            "distribution": "single",
        }

        ta = _make_automation(dev_config)

        ta.jira_client.get_my_worklogs.return_value = []
        ta.tempo_client.get_user_worklogs.return_value = []
        ta.jira_client.get_my_active_issues.return_value = []

        # Mock _is_planning_week to return True
        ta._is_planning_week = MagicMock(return_value=True)

        ta.sync_daily("2026-04-20")  # Monday after PI end

        create_calls = ta.jira_client.create_worklog.call_args_list
        total = sum(c.kwargs["time_spent_seconds"] for c in create_calls)
        assert total == 28800  # Full 8h to overhead


# ===========================================================================
# PO/Sales Role Tests (integration)
# ===========================================================================

@pytest.mark.integration
class TestPOSalesRoles:
    """Integration tests for Product Owner and Sales roles."""

    @pytest.fixture
    def po_cfg(self, po_config):
        """Return the po_config fixture from conftest."""
        return po_config

    @pytest.fixture
    def sales_config(self, po_config):
        """Sales role config (same as PO but with 'sales' role)."""
        config = dict(po_config)
        config["user"] = {
            "email": "sales@example.com",
            "name": "Test Sales",
            "role": "sales",
        }
        config["manual_activities"] = [
            {"activity": "Client Calls", "hours": 4},
            {"activity": "Demos", "hours": 4},
        ]
        return config

    def test_po_manual_activities_sync(self, po_cfg):
        """PO working day: creates Tempo worklogs for manual activities.

        3 manual activities (3h + 2h + 3h = 8h) -> 3 tempo_client calls.
        """
        ta = _make_automation(po_cfg)

        ta.tempo_client.get_user_worklogs.return_value = []
        ta.tempo_client.create_worklog.return_value = True

        ta.sync_daily("2026-02-10")

        tempo_calls = ta.tempo_client.create_worklog.call_args_list
        assert len(tempo_calls) == 3

        total_seconds = sum(
            c.kwargs.get("time_seconds", 0) for c in tempo_calls
        )
        assert total_seconds == 28800  # 8h

        # Jira should not be used
        ta.jira_client.create_worklog.assert_not_called()

    def test_po_pto_day_skips(self, po_cfg):
        """PO on PTO: skip entirely (no overhead for non-developers)."""
        ta = _make_automation(po_cfg)

        ta.schedule_mgr.is_working_day.return_value = (False, "PTO")

        ta.sync_daily("2026-03-10")

        ta.jira_client.create_worklog.assert_not_called()
        ta.tempo_client.create_worklog.assert_not_called()

    def test_po_monthly_submit(self, po_cfg, tmp_path):
        """PO can submit timesheet when no gaps exist."""
        ta = _make_automation(po_cfg)

        def weekday_schedule(date_str):
            d = date.fromisoformat(date_str)
            if d.weekday() >= 5:
                return (False, "Weekend")
            return (True, "")

        ta.schedule_mgr.is_working_day.side_effect = weekday_schedule
        ta.schedule_mgr.count_working_days.return_value = 0

        # Build worklogs for full month (8h every weekday)
        import calendar as cal
        worklogs = []
        last_day = cal.monthrange(2026, 2)[1]
        for d in range(1, last_day + 1):
            day = date(2026, 2, d)
            if day.weekday() < 5:
                worklogs.append({
                    "startDate": day.strftime("%Y-%m-%d"),
                    "timeSpentSeconds": 28800,
                })
        ta.tempo_client.get_user_worklogs.return_value = worklogs

        shortfall_path = tmp_path / "monthly_shortfall.json"
        submitted_path = tmp_path / "monthly_submitted.json"

        with patch("tempo_automation.SHORTFALL_FILE", shortfall_path), \
             patch("tempo_automation.SUBMITTED_FILE", submitted_path), \
             patch("tempo_automation.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 28)
            mock_date.fromisoformat = date.fromisoformat
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

            ta.submit_timesheet()

        ta.tempo_client.submit_timesheet.assert_called_once_with("2026-02")

    def test_sales_role_manual_activities(self, sales_config):
        """Sales role uses manual activities same as PO.

        2 manual activities (4h + 4h = 8h) -> 2 tempo_client calls.
        """
        ta = _make_automation(sales_config)

        ta.tempo_client.get_user_worklogs.return_value = []
        ta.tempo_client.create_worklog.return_value = True

        ta.sync_daily("2026-02-10")

        tempo_calls = ta.tempo_client.create_worklog.call_args_list
        assert len(tempo_calls) == 2

        total_seconds = sum(
            c.kwargs.get("time_seconds", 0) for c in tempo_calls
        )
        assert total_seconds == 28800  # 8h

        ta.jira_client.create_worklog.assert_not_called()
