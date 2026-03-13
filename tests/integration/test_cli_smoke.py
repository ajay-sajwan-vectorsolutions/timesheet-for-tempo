"""
CLI smoke tests -- verify every command executes without error.

All tests mock TempoAutomation, ConfigManager, and ScheduleManager to
avoid real API calls.  The focus is on verifying that each CLI flag
dispatches to the correct method.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tempo_automation import main  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_automation_instance():
    """Return a MagicMock that acts as a TempoAutomation instance."""
    instance = MagicMock()
    instance.sync_daily = MagicMock()
    instance.verify_week = MagicMock()
    instance.submit_timesheet = MagicMock()
    instance.view_monthly_hours = MagicMock()
    instance.fix_shortfall = MagicMock()
    instance.select_overhead_stories = MagicMock()
    instance.show_overhead_config = MagicMock()
    return instance


def _mock_schedule_instance():
    """Return a MagicMock that acts as a ScheduleManager instance."""
    instance = MagicMock()
    instance.print_month_calendar = MagicMock()
    instance.interactive_menu = MagicMock()
    instance.add_pto = MagicMock()
    instance.remove_pto = MagicMock()
    instance.add_extra_holidays = MagicMock()
    instance.remove_extra_holidays = MagicMock()
    instance.add_working_days = MagicMock()
    instance.remove_working_days = MagicMock()
    return instance


# ===========================================================================
# TestCLISmoke
# ===========================================================================

@pytest.mark.integration
class TestCLISmoke:
    """Smoke tests for CLI command dispatch."""

    @patch('sys.argv', ['prog'])
    def test_cli_no_args_defaults_to_sync(self):
        """Default (no args) runs sync_daily."""
        mock_ta = _mock_automation_instance()
        mock_cls = MagicMock(return_value=mock_ta)

        with patch('tempo_automation.TempoAutomation', mock_cls):
            main()

        mock_ta.sync_daily.assert_called_once_with(None)

    @patch('sys.argv', ['prog', '--date', '2026-03-10'])
    def test_cli_date_flag(self):
        """--date 2026-03-10 passes correct date to sync_daily."""
        mock_ta = _mock_automation_instance()
        mock_cls = MagicMock(return_value=mock_ta)

        with patch('tempo_automation.TempoAutomation', mock_cls):
            main()

        mock_ta.sync_daily.assert_called_once_with('2026-03-10')

    @patch('sys.argv', ['prog', '--verify-week'])
    def test_cli_verify_week(self):
        """--verify-week calls verify_week()."""
        mock_ta = _mock_automation_instance()
        mock_cls = MagicMock(return_value=mock_ta)

        with patch('tempo_automation.TempoAutomation', mock_cls):
            main()

        mock_ta.verify_week.assert_called_once()

    @patch('sys.argv', ['prog', '--submit'])
    def test_cli_submit(self):
        """--submit calls submit_timesheet()."""
        mock_ta = _mock_automation_instance()
        mock_cls = MagicMock(return_value=mock_ta)

        with patch('tempo_automation.TempoAutomation', mock_cls):
            main()

        mock_ta.submit_timesheet.assert_called_once()

    @patch('sys.argv', ['prog', '--view-monthly'])
    def test_cli_view_monthly(self):
        """--view-monthly calls view_monthly_hours with 'current'."""
        mock_ta = _mock_automation_instance()
        mock_cls = MagicMock(return_value=mock_ta)

        with patch('tempo_automation.TempoAutomation', mock_cls):
            main()

        mock_ta.view_monthly_hours.assert_called_once_with('current')

    @patch('sys.argv', ['prog', '--view-monthly', '2026-01'])
    def test_cli_view_monthly_with_month(self):
        """--view-monthly 2026-01 passes '2026-01' to view_monthly_hours."""
        mock_ta = _mock_automation_instance()
        mock_cls = MagicMock(return_value=mock_ta)

        with patch('tempo_automation.TempoAutomation', mock_cls):
            main()

        mock_ta.view_monthly_hours.assert_called_once_with('2026-01')

    @patch('sys.argv', ['prog', '--fix-shortfall'])
    def test_cli_fix_shortfall(self):
        """--fix-shortfall calls fix_shortfall()."""
        mock_ta = _mock_automation_instance()
        mock_cls = MagicMock(return_value=mock_ta)

        with patch('tempo_automation.TempoAutomation', mock_cls):
            main()

        mock_ta.fix_shortfall.assert_called_once()

    @patch('sys.argv', ['prog', '--show-schedule'])
    def test_cli_show_schedule(self):
        """--show-schedule calls print_month_calendar."""
        mock_sm = _mock_schedule_instance()
        mock_cm = MagicMock()
        mock_cm.config = {
            "schedule": {"daily_hours": 8.0, "pto_days": [],
                         "extra_holidays": [], "working_days": [],
                         "country_code": "US", "state": ""},
        }

        with patch('tempo_automation.ConfigManager', return_value=mock_cm), \
             patch('tempo_automation.ScheduleManager',
                   return_value=mock_sm):
            main()

        mock_sm.print_month_calendar.assert_called_once_with('current')

    @patch('sys.argv', ['prog', '--add-pto', '2026-03-10'])
    def test_cli_add_pto(self):
        """--add-pto 2026-03-10 calls add_pto with the date."""
        mock_sm = _mock_schedule_instance()
        mock_cm = MagicMock()
        mock_cm.config = {
            "schedule": {"daily_hours": 8.0, "pto_days": [],
                         "extra_holidays": [], "working_days": [],
                         "country_code": "US", "state": ""},
        }

        with patch('tempo_automation.ConfigManager', return_value=mock_cm), \
             patch('tempo_automation.ScheduleManager',
                   return_value=mock_sm):
            main()

        mock_sm.add_pto.assert_called_once_with(['2026-03-10'])

    @patch('sys.argv', ['prog', '--show-overhead'])
    def test_cli_show_overhead(self):
        """--show-overhead calls show_overhead_config()."""
        mock_ta = _mock_automation_instance()
        mock_cls = MagicMock(return_value=mock_ta)

        with patch('tempo_automation.TempoAutomation', mock_cls):
            main()

        mock_ta.show_overhead_config.assert_called_once()
