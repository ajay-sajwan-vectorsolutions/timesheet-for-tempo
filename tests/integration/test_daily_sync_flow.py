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

    def test_idempotent_overwrite_deletes_before_creating(self, dev_config):
        """Existing non-overhead worklogs are deleted before new ones.

        Tests the full idempotent overwrite flow:
        1. Existing PROJ-OLD worklog found
        2. PROJ-OLD deleted
        3. New worklogs created for active tickets
        4. Delete happens before create
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
            lambda **kw: call_order.append("create") or True
        )

        ta.sync_daily("2026-02-10")

        # Delete must happen before any create
        assert "delete" in call_order, "delete_worklog was never called"
        assert "create" in call_order, "create_worklog was never called"
        first_delete = call_order.index("delete")
        first_create = call_order.index("create")
        assert first_delete < first_create, (
            f"Delete at index {first_delete} should precede "
            f"create at index {first_create}"
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
