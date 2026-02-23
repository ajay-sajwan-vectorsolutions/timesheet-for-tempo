"""
Unit tests for the CLI main() function in tempo_automation.py.

Strategy
--------
Mock all constructors and collaborator methods.  The key thing to test is
that the correct class is instantiated and the correct method is called
for each CLI argument combination.

Mocking approach:
  - sys.argv  -> @patch('sys.argv', ['prog', '--flag'])
  - ConfigManager  -> @patch('tempo_automation.ConfigManager')
  - ScheduleManager -> @patch('tempo_automation.ScheduleManager')
  - TempoAutomation -> @patch('tempo_automation.TempoAutomation')
  - DualWriter -> @patch('tempo_automation.DualWriter')

Coverage targets (~24 tests)
-----------------------------
- TestCLISetup:               3 tests
- TestCLIScheduleCommands:    8 tests
- TestCLIAutomationCommands:  8 tests
- TestCLIErrorHandling:       3 tests
- TestCLIDualWriter:          2 tests
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Ensure project root is importable
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tempo_automation import main  # noqa: E402


# ===========================================================================
# TestCLISetup
# ===========================================================================

class TestCLISetup:
    """Tests for the --setup flag.

    The main() code for --setup is::

        config_manager = ConfigManager.__new__(ConfigManager)
        config_manager.config_path = CONFIG_FILE
        config_manager.config = config_manager.setup_wizard()

    We cannot mock ``__new__`` on a MagicMock (Python 3.14 blocks it),
    so we create a lightweight stand-in class whose ``__new__`` we can
    observe and whose ``setup_wizard`` we control.
    """

    @staticmethod
    def _make_stub_cm_class():
        """Return a stand-in ConfigManager class and its created instance."""
        instance = MagicMock()
        instance.setup_wizard.return_value = {"user": {}}

        class StubConfigManager:
            """Minimal stand-in for ConfigManager."""
            def __new__(cls):
                return instance

        return StubConfigManager, instance

    @patch('sys.argv', ['prog', '--setup'])
    def test_setup_flag_calls_setup_wizard(self):
        """--setup creates ConfigManager via __new__ and calls setup_wizard."""
        stub_cls, instance = self._make_stub_cm_class()

        with patch('tempo_automation.ConfigManager', stub_cls):
            main()

        instance.setup_wizard.assert_called_once()

    @patch('sys.argv', ['prog', '--setup'])
    @patch('tempo_automation.TempoAutomation')
    def test_setup_does_not_create_full_automation(self, mock_ta_cls):
        """--setup should NOT instantiate TempoAutomation."""
        stub_cls, _ = self._make_stub_cm_class()

        with patch('tempo_automation.ConfigManager', stub_cls):
            main()

        mock_ta_cls.assert_not_called()

    @patch('sys.argv', ['prog', '--setup'])
    @patch('tempo_automation.CONFIG_FILE', Path('/tmp/test_config.json'))
    def test_setup_uses_config_file_path(self):
        """--setup assigns CONFIG_FILE to config_manager.config_path."""
        stub_cls, instance = self._make_stub_cm_class()

        with patch('tempo_automation.ConfigManager', stub_cls):
            main()

        assert instance.config_path == Path('/tmp/test_config.json')


# ===========================================================================
# TestCLIScheduleCommands
# ===========================================================================

class TestCLIScheduleCommands:
    """Tests for schedule management CLI flags."""

    @patch('sys.argv', ['prog', '--show-schedule'])
    @patch('tempo_automation.ScheduleManager')
    @patch('tempo_automation.ConfigManager')
    def test_show_schedule_calls_print_month_calendar(
        self, mock_cm_cls, mock_sm_cls
    ):
        """--show-schedule (no arg) calls print_month_calendar('current')."""
        mock_cfg = MagicMock()
        mock_cfg.config = {"schedule": {}}
        mock_cm_cls.return_value = mock_cfg

        mock_sm = MagicMock()
        mock_sm_cls.return_value = mock_sm

        main()

        mock_sm.print_month_calendar.assert_called_once_with('current')

    @patch('sys.argv', ['prog', '--show-schedule', '2026-03'])
    @patch('tempo_automation.ScheduleManager')
    @patch('tempo_automation.ConfigManager')
    def test_show_schedule_with_month_arg(self, mock_cm_cls, mock_sm_cls):
        """--show-schedule 2026-03 passes the month string through."""
        mock_cfg = MagicMock()
        mock_cfg.config = {"schedule": {}}
        mock_cm_cls.return_value = mock_cfg

        mock_sm = MagicMock()
        mock_sm_cls.return_value = mock_sm

        main()

        mock_sm.print_month_calendar.assert_called_once_with('2026-03')

    @patch('sys.argv', ['prog', '--manage'])
    @patch('tempo_automation.ScheduleManager')
    @patch('tempo_automation.ConfigManager')
    def test_manage_calls_interactive_menu(self, mock_cm_cls, mock_sm_cls):
        """--manage calls schedule_mgr.interactive_menu()."""
        mock_cfg = MagicMock()
        mock_cfg.config = {"schedule": {}}
        mock_cm_cls.return_value = mock_cfg

        mock_sm = MagicMock()
        mock_sm_cls.return_value = mock_sm

        main()

        mock_sm.interactive_menu.assert_called_once()

    @patch('sys.argv', ['prog', '--add-pto', '2026-03-10,2026-03-11'])
    @patch('tempo_automation.ScheduleManager')
    @patch('tempo_automation.ConfigManager')
    def test_add_pto_splits_and_calls_add_pto(
        self, mock_cm_cls, mock_sm_cls
    ):
        """--add-pto splits comma-separated dates and calls add_pto."""
        mock_cfg = MagicMock()
        mock_cfg.config = {"schedule": {}}
        mock_cm_cls.return_value = mock_cfg

        mock_sm = MagicMock()
        mock_sm_cls.return_value = mock_sm

        main()

        mock_sm.add_pto.assert_called_once_with(
            ['2026-03-10', '2026-03-11']
        )

    @patch('sys.argv', ['prog', '--remove-pto', '2026-03-10'])
    @patch('tempo_automation.ScheduleManager')
    @patch('tempo_automation.ConfigManager')
    def test_remove_pto_calls_remove_pto(self, mock_cm_cls, mock_sm_cls):
        """--remove-pto calls schedule_mgr.remove_pto with parsed dates."""
        mock_cfg = MagicMock()
        mock_cfg.config = {"schedule": {}}
        mock_cm_cls.return_value = mock_cfg

        mock_sm = MagicMock()
        mock_sm_cls.return_value = mock_sm

        main()

        mock_sm.remove_pto.assert_called_once_with(['2026-03-10'])

    @patch('sys.argv', ['prog', '--add-holiday', '2026-04-01,2026-04-02'])
    @patch('tempo_automation.ScheduleManager')
    @patch('tempo_automation.ConfigManager')
    def test_add_holiday_calls_add_extra_holidays(
        self, mock_cm_cls, mock_sm_cls
    ):
        """--add-holiday calls schedule_mgr.add_extra_holidays."""
        mock_cfg = MagicMock()
        mock_cfg.config = {"schedule": {}}
        mock_cm_cls.return_value = mock_cfg

        mock_sm = MagicMock()
        mock_sm_cls.return_value = mock_sm

        main()

        mock_sm.add_extra_holidays.assert_called_once_with(
            ['2026-04-01', '2026-04-02']
        )

    @patch('sys.argv', ['prog', '--remove-holiday', '2026-04-01'])
    @patch('tempo_automation.ScheduleManager')
    @patch('tempo_automation.ConfigManager')
    def test_remove_holiday_calls_remove_extra_holidays(
        self, mock_cm_cls, mock_sm_cls
    ):
        """--remove-holiday calls schedule_mgr.remove_extra_holidays."""
        mock_cfg = MagicMock()
        mock_cfg.config = {"schedule": {}}
        mock_cm_cls.return_value = mock_cfg

        mock_sm = MagicMock()
        mock_sm_cls.return_value = mock_sm

        main()

        mock_sm.remove_extra_holidays.assert_called_once_with(['2026-04-01'])

    @patch('sys.argv', ['prog', '--add-workday', '2026-03-15'])
    @patch('tempo_automation.ScheduleManager')
    @patch('tempo_automation.ConfigManager')
    def test_add_workday_calls_add_working_days(
        self, mock_cm_cls, mock_sm_cls
    ):
        """--add-workday calls schedule_mgr.add_working_days."""
        mock_cfg = MagicMock()
        mock_cfg.config = {"schedule": {}}
        mock_cm_cls.return_value = mock_cfg

        mock_sm = MagicMock()
        mock_sm_cls.return_value = mock_sm

        main()

        mock_sm.add_working_days.assert_called_once_with(['2026-03-15'])

    @patch('sys.argv', ['prog', '--remove-workday', '2026-03-15'])
    @patch('tempo_automation.ScheduleManager')
    @patch('tempo_automation.ConfigManager')
    def test_remove_workday_calls_remove_working_days(
        self, mock_cm_cls, mock_sm_cls
    ):
        """--remove-workday calls schedule_mgr.remove_working_days."""
        mock_cfg = MagicMock()
        mock_cfg.config = {"schedule": {}}
        mock_cm_cls.return_value = mock_cfg

        mock_sm = MagicMock()
        mock_sm_cls.return_value = mock_sm

        main()

        mock_sm.remove_working_days.assert_called_once_with(['2026-03-15'])

    @patch('sys.argv', ['prog', '--add-pto', '2026-03-10'])
    @patch('tempo_automation.TempoAutomation')
    @patch('tempo_automation.ScheduleManager')
    @patch('tempo_automation.ConfigManager')
    def test_schedule_cmds_do_not_create_automation(
        self, mock_cm_cls, mock_sm_cls, mock_ta_cls
    ):
        """Schedule commands should NOT instantiate TempoAutomation."""
        mock_cfg = MagicMock()
        mock_cfg.config = {"schedule": {}}
        mock_cm_cls.return_value = mock_cfg

        mock_sm = MagicMock()
        mock_sm_cls.return_value = mock_sm

        main()

        mock_ta_cls.assert_not_called()


# ===========================================================================
# TestCLIAutomationCommands
# ===========================================================================

class TestCLIAutomationCommands:
    """Tests for commands that require full TempoAutomation init."""

    @patch('sys.argv', ['prog'])
    @patch('tempo_automation.TempoAutomation')
    def test_default_calls_sync_daily_with_none(self, mock_ta_cls):
        """No arguments -> sync_daily(None)."""
        mock_auto = MagicMock()
        mock_ta_cls.return_value = mock_auto

        main()

        mock_auto.sync_daily.assert_called_once_with(None)

    @patch('sys.argv', ['prog', '--date', '2026-02-15'])
    @patch('tempo_automation.TempoAutomation')
    def test_date_flag_calls_sync_daily_with_date(self, mock_ta_cls):
        """--date 2026-02-15 -> sync_daily('2026-02-15')."""
        mock_auto = MagicMock()
        mock_ta_cls.return_value = mock_auto

        main()

        mock_auto.sync_daily.assert_called_once_with('2026-02-15')

    @patch('sys.argv', ['prog', '--submit'])
    @patch('tempo_automation.TempoAutomation')
    def test_submit_calls_submit_timesheet(self, mock_ta_cls):
        """--submit -> automation.submit_timesheet()."""
        mock_auto = MagicMock()
        mock_ta_cls.return_value = mock_auto

        main()

        mock_auto.submit_timesheet.assert_called_once()

    @patch('sys.argv', ['prog', '--verify-week'])
    @patch('tempo_automation.TempoAutomation')
    def test_verify_week_calls_verify_week(self, mock_ta_cls):
        """--verify-week -> automation.verify_week()."""
        mock_auto = MagicMock()
        mock_ta_cls.return_value = mock_auto

        main()

        mock_auto.verify_week.assert_called_once()

    @patch('sys.argv', ['prog', '--select-overhead'])
    @patch('tempo_automation.TempoAutomation')
    def test_select_overhead_calls_select_overhead_stories(self, mock_ta_cls):
        """--select-overhead -> automation.select_overhead_stories()."""
        mock_auto = MagicMock()
        mock_ta_cls.return_value = mock_auto

        main()

        mock_auto.select_overhead_stories.assert_called_once()

    @patch('sys.argv', ['prog', '--show-overhead'])
    @patch('tempo_automation.TempoAutomation')
    def test_show_overhead_calls_show_overhead_config(self, mock_ta_cls):
        """--show-overhead -> automation.show_overhead_config()."""
        mock_auto = MagicMock()
        mock_ta_cls.return_value = mock_auto

        main()

        mock_auto.show_overhead_config.assert_called_once()

    @patch('sys.argv', ['prog', '--view-monthly'])
    @patch('tempo_automation.TempoAutomation')
    def test_view_monthly_calls_view_monthly_hours(self, mock_ta_cls):
        """--view-monthly (no arg) -> view_monthly_hours('current')."""
        mock_auto = MagicMock()
        mock_ta_cls.return_value = mock_auto

        main()

        mock_auto.view_monthly_hours.assert_called_once_with('current')

    @patch('sys.argv', ['prog', '--view-monthly', '2026-01'])
    @patch('tempo_automation.TempoAutomation')
    def test_view_monthly_with_month_arg(self, mock_ta_cls):
        """--view-monthly 2026-01 -> view_monthly_hours('2026-01')."""
        mock_auto = MagicMock()
        mock_ta_cls.return_value = mock_auto

        main()

        mock_auto.view_monthly_hours.assert_called_once_with('2026-01')

    @patch('sys.argv', ['prog', '--fix-shortfall'])
    @patch('tempo_automation.TempoAutomation')
    def test_fix_shortfall_calls_fix_shortfall(self, mock_ta_cls):
        """--fix-shortfall -> automation.fix_shortfall()."""
        mock_auto = MagicMock()
        mock_ta_cls.return_value = mock_auto

        main()

        mock_auto.fix_shortfall.assert_called_once()


# ===========================================================================
# TestCLIErrorHandling
# ===========================================================================

class TestCLIErrorHandling:
    """Tests for error handling in main()."""

    @patch('sys.argv', ['prog'])
    @patch('tempo_automation.TempoAutomation')
    def test_keyboard_interrupt_exits_with_code_1(self, mock_ta_cls):
        """KeyboardInterrupt during execution -> sys.exit(1)."""
        mock_auto = MagicMock()
        mock_auto.sync_daily.side_effect = KeyboardInterrupt()
        mock_ta_cls.return_value = mock_auto

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

    @patch('sys.argv', ['prog'])
    @patch('tempo_automation.TempoAutomation')
    def test_exception_exits_with_code_1(self, mock_ta_cls):
        """Unhandled exception during execution -> sys.exit(1)."""
        mock_auto = MagicMock()
        mock_auto.sync_daily.side_effect = RuntimeError("API failure")
        mock_ta_cls.return_value = mock_auto

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

    @patch('sys.argv', ['prog', '--setup'])
    def test_setup_exception_exits_with_code_1(self):
        """Exception during setup_wizard -> sys.exit(1)."""
        instance = MagicMock()
        instance.setup_wizard.side_effect = RuntimeError("Config error")

        class StubCM:
            def __new__(cls):
                return instance

        with patch('tempo_automation.ConfigManager', StubCM):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 1


# ===========================================================================
# TestCLIDualWriter
# ===========================================================================

class TestCLIDualWriter:
    """Tests for the --logfile / DualWriter integration."""

    @patch('sys.argv', ['prog', '--logfile', '/tmp/test.log'])
    @patch('tempo_automation.TempoAutomation')
    @patch('tempo_automation.DualWriter')
    def test_logfile_creates_dual_writer(self, mock_dw_cls, mock_ta_cls):
        """--logfile wraps sys.stdout in a DualWriter."""
        mock_dw_instance = MagicMock()
        mock_dw_cls.return_value = mock_dw_instance

        mock_auto = MagicMock()
        mock_ta_cls.return_value = mock_auto

        original_stdout = sys.stdout
        try:
            main()
            # DualWriter was constructed with original stdout and the path
            mock_dw_cls.assert_called_once()
            call_args = mock_dw_cls.call_args
            # First positional arg is original stdout, second is logfile path
            assert call_args[0][1] == '/tmp/test.log'
        finally:
            # Restore stdout to avoid polluting other tests
            sys.stdout = original_stdout

    @patch('sys.argv', ['prog'])
    @patch('tempo_automation.TempoAutomation')
    @patch('tempo_automation.DualWriter')
    def test_no_logfile_keeps_original_stdout(self, mock_dw_cls, mock_ta_cls):
        """Without --logfile, DualWriter is never created."""
        mock_auto = MagicMock()
        mock_ta_cls.return_value = mock_auto

        main()

        mock_dw_cls.assert_not_called()
