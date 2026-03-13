"""
Integration tests for the monthly submission and weekly verification flows.

Strategy
--------
These tests exercise the full orchestration paths through submit_timesheet()
and verify_week(), verifying that gap detection, shortfall blocking,
Tempo submission, and backfill logic work correctly end-to-end.

All collaborators are Mocks -- no real HTTP calls.  Date-dependent logic
is controlled via ``freezegun.freeze_time`` or ``unittest.mock.patch``
on ``tempo_automation.date``.

Coverage (~8 tests)
-------------------
- TestMonthlySubmitFlow (5 tests)
  * Successful submission with no gaps
  * Submission blocked by gaps
  * Gap detection accuracy (specific amounts)
  * Notification on shortfall
  * Already submitted returns early

- TestVerifyWeekFlow (3 tests)
  * All days complete -- no backfill
  * Gap on one day triggers backfill
  * Weekend/PTO days skipped
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
# Helper: build a TempoAutomation without triggering __init__
# ---------------------------------------------------------------------------

def _make_automation(config: dict) -> TempoAutomation:
    """
    Create a TempoAutomation instance without calling __init__.

    Manually attaches mock collaborators so each test starts clean
    and deterministic.
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


def _build_tempo_worklogs_for_month(
    year: int,
    month: int,
    hours_per_day: float,
    is_working_day_fn,
    end_day: int = None,
) -> list:
    """Build a list of Tempo worklog dicts for every working day in a month.

    Args:
        year: Calendar year.
        month: Calendar month (1-12).
        hours_per_day: Hours to log for each working day.
        is_working_day_fn: Callable(date_str) -> (bool, str).
        end_day: Last day to include (defaults to last day of month).

    Returns:
        List of dicts with startDate and timeSpentSeconds.
    """
    import calendar as cal
    last_day = end_day or cal.monthrange(year, month)[1]
    worklogs = []
    for d in range(1, last_day + 1):
        day = date(year, month, d)
        day_str = day.strftime("%Y-%m-%d")
        is_working, _ = is_working_day_fn(day_str)
        if is_working:
            worklogs.append({
                "startDate": day_str,
                "timeSpentSeconds": int(hours_per_day * 3600),
            })
    return worklogs


def _weekday_schedule(date_str: str):
    """Simple schedule: weekdays are working, weekends are not."""
    d = date.fromisoformat(date_str)
    if d.weekday() >= 5:
        return (False, "Weekend")
    return (True, "")


# ===========================================================================
# Monthly submission flow
# ===========================================================================

@pytest.mark.integration
class TestMonthlySubmitFlow:
    """End-to-end monthly submission tests."""

    @pytest.fixture
    def dev_config(self, developer_config):
        """Return the developer_config fixture from conftest."""
        return developer_config

    def test_successful_submission_no_gaps(self, dev_config, tmp_path):
        """All working days have full hours -> submits successfully.

        Expected flow:
        1. _is_already_submitted -> False
        2. Today is last day of month (in submission window)
        3. _detect_monthly_gaps -> no gaps
        4. tempo_client.submit_timesheet called with period
        5. _save_submitted_marker called
        6. notifier.send_submission_confirmation called
        """
        ta = _make_automation(dev_config)

        ta.schedule_mgr.is_working_day.side_effect = _weekday_schedule

        # Build 8h worklogs for every weekday in Feb 2026 (up to 28th)
        feb_worklogs = _build_tempo_worklogs_for_month(
            2026, 2, 8.0, _weekday_schedule, end_day=28
        )
        ta.tempo_client.get_user_worklogs.return_value = feb_worklogs

        shortfall_path = tmp_path / "monthly_shortfall.json"
        submitted_path = tmp_path / "monthly_submitted.json"

        with patch("tempo_automation.SHORTFALL_FILE", shortfall_path), \
             patch("tempo_automation.SUBMITTED_FILE", submitted_path), \
             patch("tempo_automation.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 28)
            mock_date.fromisoformat = date.fromisoformat
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

            ta.submit_timesheet()

        # Tempo submit_timesheet should be called
        ta.tempo_client.submit_timesheet.assert_called_once_with("2026-02")

        # Submitted marker should be saved
        assert submitted_path.exists()
        marker = json.loads(submitted_path.read_text(encoding="utf-8"))
        assert marker["period"] == "2026-02"

        # Notification sent
        ta.notifier.send_submission_confirmation.assert_called_once_with(
            "2026-02"
        )

    def test_submission_blocked_by_gaps(self, dev_config, tmp_path):
        """Some days have shortfall -> submission blocked.

        Expected flow:
        1. _detect_monthly_gaps finds days with < 8h
        2. submit_timesheet does NOT call tempo_client.submit_timesheet
        3. Shortfall data saved to monthly_shortfall.json
        4. Shortfall notification sent
        """
        ta = _make_automation(dev_config)

        ta.schedule_mgr.is_working_day.side_effect = _weekday_schedule

        # Build worklogs: all days at 8h except Feb 10 (6h) and Feb 12 (4h)
        feb_worklogs = _build_tempo_worklogs_for_month(
            2026, 2, 8.0, _weekday_schedule, end_day=28
        )
        # Modify specific days to create shortfalls
        for wl in feb_worklogs:
            if wl["startDate"] == "2026-02-10":
                wl["timeSpentSeconds"] = int(6 * 3600)  # 6h
            elif wl["startDate"] == "2026-02-12":
                wl["timeSpentSeconds"] = int(4 * 3600)  # 4h
        ta.tempo_client.get_user_worklogs.return_value = feb_worklogs

        shortfall_path = tmp_path / "monthly_shortfall.json"
        submitted_path = tmp_path / "monthly_submitted.json"

        with patch("tempo_automation.SHORTFALL_FILE", shortfall_path), \
             patch("tempo_automation.SUBMITTED_FILE", submitted_path), \
             patch("tempo_automation.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 28)
            mock_date.fromisoformat = date.fromisoformat
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

            ta.submit_timesheet()

        # submit_timesheet on Tempo should NOT be called
        ta.tempo_client.submit_timesheet.assert_not_called()

        # Shortfall file should exist with gap data
        assert shortfall_path.exists()
        shortfall = json.loads(
            shortfall_path.read_text(encoding="utf-8")
        )
        assert shortfall["period"] == "2026-02"
        assert len(shortfall["gaps"]) == 2, (
            f"Expected 2 gap days, got {len(shortfall['gaps'])}"
        )

        # Submitted marker should NOT exist
        assert not submitted_path.exists()

    def test_gap_detection_accuracy(self, dev_config, tmp_path):
        """Verify specific gap amounts match expectations.

        Feb 10 logged 6h (gap 2h), Feb 12 logged 4h (gap 4h).
        Total shortfall = 6h.
        """
        ta = _make_automation(dev_config)

        ta.schedule_mgr.is_working_day.side_effect = _weekday_schedule

        feb_worklogs = _build_tempo_worklogs_for_month(
            2026, 2, 8.0, _weekday_schedule, end_day=28
        )
        for wl in feb_worklogs:
            if wl["startDate"] == "2026-02-10":
                wl["timeSpentSeconds"] = int(6 * 3600)
            elif wl["startDate"] == "2026-02-12":
                wl["timeSpentSeconds"] = int(4 * 3600)
        ta.tempo_client.get_user_worklogs.return_value = feb_worklogs

        with patch("tempo_automation.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 28)
            mock_date.fromisoformat = date.fromisoformat
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

            gap_data = ta._detect_monthly_gaps(2026, 2)

        # Verify exact gaps
        gaps_by_date = {g["date"]: g for g in gap_data["gaps"]}

        assert "2026-02-10" in gaps_by_date
        assert abs(gaps_by_date["2026-02-10"]["gap"] - 2.0) < 0.01

        assert "2026-02-12" in gaps_by_date
        assert abs(gaps_by_date["2026-02-12"]["gap"] - 4.0) < 0.01

        # Total shortfall
        total_gap = sum(g["gap"] for g in gap_data["gaps"])
        assert abs(total_gap - 6.0) < 0.01

        # Expected hours = working_days * 8.0
        assert gap_data["expected"] == gap_data["working_days"] * 8.0

        # Actual = expected - 6h shortfall
        assert abs(
            gap_data["actual"] - (gap_data["expected"] - 6.0)
        ) < 0.1

    def test_notification_on_shortfall(self, dev_config, tmp_path):
        """Shortfall notification is sent when gaps are found."""
        ta = _make_automation(dev_config)

        ta.schedule_mgr.is_working_day.side_effect = _weekday_schedule

        # Only one day with a gap (Feb 10 at 4h)
        feb_worklogs = _build_tempo_worklogs_for_month(
            2026, 2, 8.0, _weekday_schedule, end_day=28
        )
        for wl in feb_worklogs:
            if wl["startDate"] == "2026-02-10":
                wl["timeSpentSeconds"] = int(4 * 3600)
        ta.tempo_client.get_user_worklogs.return_value = feb_worklogs

        shortfall_path = tmp_path / "monthly_shortfall.json"
        submitted_path = tmp_path / "monthly_submitted.json"

        with patch("tempo_automation.SHORTFALL_FILE", shortfall_path), \
             patch("tempo_automation.SUBMITTED_FILE", submitted_path), \
             patch("tempo_automation.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 28)
            mock_date.fromisoformat = date.fromisoformat
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

            ta.submit_timesheet()

        # Desktop notification should be sent for shortfall
        ta.notifier.send_windows_notification.assert_called_once()
        notif_args = ta.notifier.send_windows_notification.call_args
        assert "Shortfall" in notif_args.args[0]

    def test_already_submitted_returns_early(self, dev_config, tmp_path):
        """When submitted marker exists for current period, skip entirely.

        No gap detection, no Tempo submission, no notifications.
        """
        ta = _make_automation(dev_config)

        # Create submitted marker for Feb 2026
        submitted_path = tmp_path / "monthly_submitted.json"
        submitted_path.write_text(
            json.dumps({
                "period": "2026-02",
                "submitted_at": "2026-02-28T18:00:00"
            }),
            encoding="utf-8",
        )

        shortfall_path = tmp_path / "monthly_shortfall.json"

        with patch("tempo_automation.SHORTFALL_FILE", shortfall_path), \
             patch("tempo_automation.SUBMITTED_FILE", submitted_path), \
             patch("tempo_automation.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 28)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

            ta.submit_timesheet()

        # No gap detection, no submission, no notification
        ta.tempo_client.get_user_worklogs.assert_not_called()
        ta.tempo_client.submit_timesheet.assert_not_called()
        ta.notifier.send_submission_confirmation.assert_not_called()


# ===========================================================================
# Weekly verification flow
# ===========================================================================

@pytest.mark.integration
class TestVerifyWeekFlow:
    """Weekly verification and backfill flow."""

    @pytest.fixture
    def dev_config(self, developer_config):
        """Return the developer_config fixture from conftest."""
        return developer_config

    def test_verify_week_no_gaps(self, dev_config):
        """All weekdays have full hours -> no backfill needed.

        Expected:
        1. verify_week iterates Mon-Fri of current week
        2. _check_day_hours returns 8h for each day
        3. _backfill_day is never called
        """
        ta = _make_automation(dev_config)

        ta.schedule_mgr.is_working_day.side_effect = _weekday_schedule

        # All days have 8h
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
            # Friday Feb 13, 2026 -> week of Feb 9-13
            mock_date.today.return_value = date(2026, 2, 13)
            mock_date.fromisoformat = date.fromisoformat
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

            ta.verify_week()

        ta._backfill_day.assert_not_called()

        # _check_day_hours should be called for each weekday Mon-Fri
        assert ta._check_day_hours.call_count == 5

    def test_verify_week_with_gaps(self, dev_config):
        """Working day missing hours -> backfill attempted.

        Expected:
        1. Monday has 4h gap (4h logged, 8h expected)
        2. _backfill_day called for Monday
        3. Other days are complete (no backfill)
        """
        ta = _make_automation(dev_config)

        ta.schedule_mgr.is_working_day.side_effect = _weekday_schedule

        def check_day_hours(date_str):
            d = date.fromisoformat(date_str)
            if d.weekday() == 0:  # Monday
                return {
                    "existing_hours": 4.0,
                    "gap_hours": 4.0,
                    "worklogs": [
                        {
                            "issue_key": "PROJ-1",
                            "worklog_id": "100",
                            "time_spent_seconds": 14400,
                        }
                    ],
                    "existing_keys": {"PROJ-1"},
                }
            return {
                "existing_hours": 8.0,
                "gap_hours": 0.0,
                "worklogs": [
                    {
                        "issue_key": "PROJ-1",
                        "worklog_id": "101",
                        "time_spent_seconds": 28800,
                    }
                ],
                "existing_keys": {"PROJ-1"},
            }

        ta._check_day_hours = MagicMock(side_effect=check_day_hours)
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

        # Backfill called exactly once (for Monday)
        assert ta._backfill_day.call_count == 1
        backfill_date = ta._backfill_day.call_args.args[0]
        assert date.fromisoformat(backfill_date).weekday() == 0

        # Backfill called with correct gap (4h = 14400s)
        backfill_gap_secs = ta._backfill_day.call_args.args[1]
        assert backfill_gap_secs == 14400

    def test_verify_week_skips_weekends_and_pto(self, dev_config):
        """Weekend and PTO days are not checked for gaps.

        Schedule: Mon-Thu working, Fri PTO, Sat-Sun weekend.
        Expected: _check_day_hours called 4 times (Mon-Thu only).
        """
        ta = _make_automation(dev_config)

        def schedule_with_friday_pto(date_str):
            d = date.fromisoformat(date_str)
            if d.weekday() >= 5:
                return (False, "Weekend")
            if d.weekday() == 4:  # Friday
                return (False, "PTO")
            return (True, "")

        ta.schedule_mgr.is_working_day.side_effect = (
            schedule_with_friday_pto
        )

        # _is_overhead_configured returns False so PTO branch
        # does not attempt overhead check via _check_day_hours
        ta._is_overhead_configured = MagicMock(return_value=False)

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
            mock_date.today.return_value = date(2026, 2, 13)  # Friday
            mock_date.fromisoformat = date.fromisoformat
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

            ta.verify_week()

        # Only Mon-Thu checked (4 working days)
        assert ta._check_day_hours.call_count == 4
        ta._backfill_day.assert_not_called()

        # Verify the dates that were checked are Mon-Thu
        checked_dates = [
            c.args[0] for c in ta._check_day_hours.call_args_list
        ]
        for ds in checked_dates:
            d = date.fromisoformat(ds)
            assert d.weekday() < 4, (
                f"Day {ds} (weekday={d.weekday()}) should not be checked"
            )
