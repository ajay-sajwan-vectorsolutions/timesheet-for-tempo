"""
Unit tests for the TrayApp class in tray_app.py.

Strategy
--------
TrayApp's constructor is lightweight (no I/O, no GUI), so we create
real instances and exercise logic methods.  All GUI dependencies
(pystray, PIL, winotify) are mocked at the module level so that
tests pass even when those packages are not installed.

Mocking approach:
  - pystray, PIL, winotify -> sys.modules stubs before import
  - threading.Timer -> @patch('tray_app.threading.Timer')
  - SHORTFALL_FILE / SUBMITTED_FILE / CONFIG_FILE -> tmp_path fixtures
  - datetime.date.today / datetime.datetime.now -> @patch
  - sys.platform -> @patch where platform-specific logic is tested

Coverage targets (~32 tests)
-----------------------------
- TestTrayAppInit:           4 tests
- TestGetSyncTime:           3 tests
- TestShortfallVisible:      3 tests
- TestSubmitVisible:         5 tests
- TestOnSyncNow:             4 tests
- TestProcessPtoInput:       4 tests
- TestFindPythonw:           3 tests
- TestReloadConfig:          3 tests
- TestScheduleNextSync:      3 tests
"""

import json
import sys
import threading
import time
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure project root is importable
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Stub out optional GUI dependencies before importing tray_app.
# pystray, PIL (Pillow), and winotify may not be installed in CI or
# test environments.  We insert MagicMock modules so that tray_app's
# top-level imports succeed without real packages.
# ---------------------------------------------------------------------------
_pystray_stub = MagicMock()
_pystray_stub.Menu = MagicMock()
_pystray_stub.Menu.SEPARATOR = "---"
_pystray_stub.MenuItem = MagicMock()
_pystray_stub.Icon = MagicMock()

_pil_image_stub = MagicMock()
_pil_imagedraw_stub = MagicMock()
_pil_imagefont_stub = MagicMock()
_pil_stub = MagicMock()
_pil_stub.Image = _pil_image_stub
_pil_stub.ImageDraw = _pil_imagedraw_stub
_pil_stub.ImageFont = _pil_imagefont_stub

_winotify_stub = MagicMock()

# Only inject stubs for modules that are NOT already available
_stubs_installed = {}
for mod_name, stub in [
    ("pystray", _pystray_stub),
    ("PIL", _pil_stub),
    ("PIL.Image", _pil_image_stub),
    ("PIL.ImageDraw", _pil_imagedraw_stub),
    ("PIL.ImageFont", _pil_imagefont_stub),
    ("winotify", _winotify_stub),
]:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = stub
        _stubs_installed[mod_name] = stub

# Now we can safely import tray_app
from tray_app import (  # noqa: E402
    BG_COLORS,
    TrayApp,
    _find_pythonw,
)

# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def app():
    """Create a fresh TrayApp instance for each test."""
    return TrayApp()


@pytest.fixture
def shortfall_file(tmp_path):
    """Patch SHORTFALL_FILE to a tmp_path location and return the path."""
    sf = tmp_path / "monthly_shortfall.json"
    with patch("tray_app.SHORTFALL_FILE", sf):
        yield sf


@pytest.fixture
def submitted_file(tmp_path):
    """Patch SUBMITTED_FILE to a tmp_path location and return the path."""
    sf = tmp_path / "monthly_submitted.json"
    with patch("tray_app.SUBMITTED_FILE", sf):
        yield sf


@pytest.fixture
def config_file_path(tmp_path):
    """Patch CONFIG_FILE to a tmp_path location and return the path."""
    cf = tmp_path / "config.json"
    with patch("tray_app.CONFIG_FILE", cf):
        yield cf


# ===========================================================================
# TestTrayAppInit
# ===========================================================================


class TestTrayAppInit:
    """Verify all instance attributes are initialised correctly."""

    def test_initial_state(self, app):
        """All attributes should be set to their expected defaults."""
        assert app._pending_confirmation is False
        assert app._timer is None
        assert app._icon is None
        assert app._automation is None
        assert app._config is None
        assert app._import_error is None
        assert app._anim_timer is None
        assert app._anim_running is False

    def test_sync_running_is_threading_event(self, app):
        """_sync_running must be a threading.Event, not a bool."""
        assert isinstance(app._sync_running, threading.Event)
        assert not app._sync_running.is_set()

    def test_pending_confirmation_starts_false(self, app):
        """_pending_confirmation must start as False."""
        assert app._pending_confirmation is False

    def test_config_starts_none(self, app):
        """_config should be None before _load_automation or _reload_config."""
        assert app._config is None


# ===========================================================================
# TestGetSyncTime
# ===========================================================================


class TestGetSyncTime:
    """Tests for _get_sync_time()."""

    def test_returns_configured_time(self, app):
        """When config has schedule.daily_sync_time, return it."""
        app._config = {"schedule": {"daily_sync_time": "09:30"}}
        assert app._get_sync_time() == "09:30"

    def test_returns_default_when_no_config(self, app):
        """When _config is None, return the default '18:00'."""
        app._config = None
        assert app._get_sync_time() == "18:00"

    def test_returns_default_when_missing_key(self, app):
        """When config exists but lacks daily_sync_time, return '18:00'."""
        app._config = {"schedule": {}}
        assert app._get_sync_time() == "18:00"


# ===========================================================================
# TestShortfallVisible
# ===========================================================================


class TestShortfallVisible:
    """Tests for _shortfall_visible() dynamic menu visibility."""

    def test_returns_true_when_file_exists(self, app, shortfall_file):
        """Visible when the shortfall JSON file is present."""
        shortfall_file.write_text(json.dumps({"days": ["2026-02-10"]}), encoding="utf-8")
        assert app._shortfall_visible(None) is True

    def test_returns_false_when_file_missing(self, app, shortfall_file):
        """Hidden when the shortfall file does not exist."""
        assert not shortfall_file.exists()
        assert app._shortfall_visible(None) is False

    def test_accepts_item_argument(self, app, shortfall_file):
        """pystray passes a MenuItem as the first arg; method must accept it."""
        fake_item = MagicMock(name="MenuItem")
        # Should not raise regardless of file state
        result = app._shortfall_visible(fake_item)
        assert isinstance(result, bool)


# ===========================================================================
# TestSubmitVisible
# ===========================================================================


class TestSubmitVisible:
    """Tests for _submit_visible() dynamic menu visibility."""

    def test_hidden_early_in_month(self, app, shortfall_file, submitted_file):
        """Should be hidden on day 1 of a 31-day month."""
        with patch("tray_app._today", return_value=date(2026, 1, 1)):
            assert app._submit_visible(None) is False

    def test_visible_last_7_days(self, app, shortfall_file, submitted_file):
        """Should be visible on day 25 of a 28-day month (Feb)."""
        with patch("tray_app._today", return_value=date(2026, 2, 25)):
            assert app._submit_visible(None) is True

    def test_hidden_when_shortfall_exists(self, app, shortfall_file, submitted_file):
        """Even in the last 7 days, hide if shortfall file exists."""
        shortfall_file.write_text(json.dumps({"days": ["2026-02-10"]}), encoding="utf-8")
        with patch("tray_app._today", return_value=date(2026, 2, 25)):
            assert app._submit_visible(None) is False

    def test_hidden_when_already_submitted(self, app, shortfall_file, submitted_file):
        """Hide if submitted_file shows the current period was submitted."""
        submitted_file.write_text(json.dumps({"period": "2026-02"}), encoding="utf-8")
        with patch("tray_app._today", return_value=date(2026, 2, 25)):
            assert app._submit_visible(None) is False

    def test_visible_when_submitted_different_period(self, app, shortfall_file, submitted_file):
        """Visible if submitted_file is for a different month."""
        submitted_file.write_text(json.dumps({"period": "2026-01"}), encoding="utf-8")
        with patch("tray_app._today", return_value=date(2026, 2, 25)):
            assert app._submit_visible(None) is True

    def test_visible_early_when_no_working_days_remain(self, app, shortfall_file, submitted_file):
        """Visible mid-month when all remaining days are non-working."""
        mock_schedule = MagicMock()
        mock_schedule.count_working_days.return_value = 0
        mock_automation = MagicMock()
        mock_automation.schedule_mgr = mock_schedule
        app._automation = mock_automation

        with patch("tray_app._today", return_value=date(2026, 1, 15)):
            assert app._submit_visible(None) is True

    def test_hidden_early_when_working_days_remain(self, app, shortfall_file, submitted_file):
        """Hidden mid-month when working days still remain."""
        mock_schedule = MagicMock()
        mock_schedule.count_working_days.return_value = 5
        mock_automation = MagicMock()
        mock_automation.schedule_mgr = mock_schedule
        app._automation = mock_automation

        with patch("tray_app._today", return_value=date(2026, 1, 15)):
            assert app._submit_visible(None) is False

    def test_hidden_early_when_automation_none(self, app, shortfall_file, submitted_file):
        """Falls back to 7-day window when automation is None."""
        app._automation = None
        with patch("tray_app._today", return_value=date(2026, 1, 15)):
            assert app._submit_visible(None) is False


# ===========================================================================
# TestOnSyncNow
# ===========================================================================


class TestOnSyncNow:
    """Tests for _on_sync_now()."""

    def test_starts_background_thread(self, app):
        """Should spawn a daemon thread targeting _run_sync."""
        app._automation = MagicMock()
        mock_thread = MagicMock()
        with patch("tray_app.threading.Thread", return_value=mock_thread) as cls:
            app._on_sync_now()

        cls.assert_called_once_with(target=app._run_sync, daemon=True)
        mock_thread.start.assert_called_once()

    def test_blocks_when_sync_already_running(self, app):
        """When a sync is in progress, show toast and return."""
        app._sync_running.set()
        app._automation = MagicMock()
        with patch.object(app, "_show_toast") as mock_toast:
            app._on_sync_now()

        mock_toast.assert_called_once()
        assert "already" in mock_toast.call_args[0][1].lower()

    def test_shows_error_when_automation_none(self, app):
        """When automation failed to load, show error toast."""
        app._automation = None
        app._import_error = "config.json not found"
        with patch.object(app, "_show_toast") as mock_toast:
            app._on_sync_now()

        mock_toast.assert_called_once()
        assert "config.json not found" in mock_toast.call_args[0][1]

    def test_clears_pending_confirmation(self, app):
        """_on_sync_now should reset _pending_confirmation to False."""
        app._pending_confirmation = True
        app._automation = MagicMock()
        with patch("tray_app.threading.Thread", return_value=MagicMock()):
            app._on_sync_now()
        assert app._pending_confirmation is False


# ===========================================================================
# TestProcessPtoInput
# ===========================================================================


class TestProcessPtoInput:
    """Tests for _process_pto_input()."""

    def test_sanitizes_input(self, app):
        """Regex should strip non-date characters."""
        mock_schedule = MagicMock()
        mock_schedule.add_pto.return_value = (["2026-03-10"], [])
        app._automation = MagicMock()
        app._automation.schedule_mgr = mock_schedule

        with patch.object(app, "_show_toast"):
            app._process_pto_input("  2026-03-10 (Monday)  ")

        # add_pto should receive the cleaned date string
        args = mock_schedule.add_pto.call_args[0][0]
        assert "2026-03-10" in args
        # Parenthetical text should have been stripped
        for d in args:
            assert "(" not in d
            assert ")" not in d

    def test_splits_comma_separated_dates(self, app):
        """Multiple comma-separated dates should be split and passed."""
        mock_schedule = MagicMock()
        mock_schedule.add_pto.return_value = (["2026-03-10", "2026-03-11"], [])
        app._automation = MagicMock()
        app._automation.schedule_mgr = mock_schedule

        with patch.object(app, "_show_toast"):
            app._process_pto_input("2026-03-10, 2026-03-11")

        dates_arg = mock_schedule.add_pto.call_args[0][0]
        assert len(dates_arg) == 2
        assert "2026-03-10" in dates_arg
        assert "2026-03-11" in dates_arg

    def test_shows_toast_when_no_valid_dates(self, app):
        """Input with only special chars (no digits/dashes) should toast."""
        # The regex strips everything except digits, dashes, commas, and
        # whitespace.  Pure letters + symbols produce only whitespace,
        # which is truthy but yields an empty dates list.  add_pto([])
        # returns ([], ["reason"]) in real code -- mock that.
        mock_schedule = MagicMock()
        mock_schedule.add_pto.return_value = ([], ["No valid dates"])
        app._automation = MagicMock()
        app._automation.schedule_mgr = mock_schedule

        with patch.object(app, "_show_toast") as mock_toast:
            app._process_pto_input("nothing valid here")

        mock_toast.assert_called_once()
        assert "No PTO Added" in mock_toast.call_args[0][0]

    def test_calls_schedule_mgr_add_pto(self, app):
        """Should call schedule_mgr.add_pto with cleaned date list."""
        mock_schedule = MagicMock()
        mock_schedule.add_pto.return_value = (["2026-04-01"], [])
        app._automation = MagicMock()
        app._automation.schedule_mgr = mock_schedule

        with patch.object(app, "_show_toast"):
            app._process_pto_input("2026-04-01")

        mock_schedule.add_pto.assert_called_once()
        assert "2026-04-01" in mock_schedule.add_pto.call_args[0][0]


# ===========================================================================
# TestShowYesnoDialog
# ===========================================================================


class TestShowYesnoDialog:
    """Tests for TrayApp._show_yesno_dialog()."""

    def test_windows_yes_returns_true(self, app):
        """MessageBoxW returning 6 (IDYES) -> True."""
        with patch("sys.platform", "win32"):
            with patch("tray_app.ctypes") as mock_ctypes:
                mock_ctypes.windll.user32.MessageBoxW.return_value = 6
                result = app._show_yesno_dialog("Sync?", "Title")
        assert result is True

    def test_windows_no_returns_false(self, app):
        """MessageBoxW returning 7 (IDNO) -> False."""
        with patch("sys.platform", "win32"):
            with patch("tray_app.ctypes") as mock_ctypes:
                mock_ctypes.windll.user32.MessageBoxW.return_value = 7
                result = app._show_yesno_dialog("Sync?", "Title")
        assert result is False

    def test_mac_yes_returns_true(self, app):
        """osascript stdout containing 'Yes' -> True."""
        with patch("sys.platform", "darwin"):
            with patch("tray_app.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="button returned:Yes\n")
                result = app._show_yesno_dialog("Sync?", "Title")
        assert result is True

    def test_mac_no_returns_false(self, app):
        """osascript stdout containing 'No' -> False."""
        with patch("sys.platform", "darwin"):
            with patch("tray_app.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="button returned:No\n")
                result = app._show_yesno_dialog("Sync?", "Title")
        assert result is False

    def test_unknown_platform_returns_false(self, app):
        """Unknown platform -> False (safe default)."""
        with patch("sys.platform", "linux"):
            result = app._show_yesno_dialog("Sync?", "Title")
        assert result is False


# ===========================================================================
# TestSyncPtoDatesBackground
# ===========================================================================


class TestSyncPtoDatesBackground:
    """Tests for TrayApp._sync_pto_dates_background()."""

    def test_calls_sync_daily_for_each_date(self, app):
        """sync_daily() is called once per date."""
        app._automation = MagicMock()
        app._automation.sync_daily = MagicMock()

        with patch.object(app, "_show_toast"):
            app._sync_pto_dates_background(["2026-04-07", "2026-04-08"])
            # Allow daemon thread to complete
            time.sleep(0.2)

        assert app._automation.sync_daily.call_count == 2
        app._automation.sync_daily.assert_any_call("2026-04-07")
        app._automation.sync_daily.assert_any_call("2026-04-08")

    def test_shows_success_toast_after_sync(self, app):
        """A success toast is shown when all syncs complete."""
        app._automation = MagicMock()
        app._automation.sync_daily = MagicMock()

        with patch.object(app, "_show_toast") as mock_toast:
            app._sync_pto_dates_background(["2026-04-07"])
            time.sleep(0.2)

        mock_toast.assert_called_once()
        title, _ = mock_toast.call_args[0]
        assert "Synced" in title or "PTO" in title

    def test_shows_error_toast_on_failure(self, app):
        """If sync_daily raises, an error toast is shown."""
        app._automation = MagicMock()
        app._automation.sync_daily = MagicMock(side_effect=RuntimeError("API down"))

        with patch.object(app, "_show_toast") as mock_toast:
            app._sync_pto_dates_background(["2026-04-07"])
            time.sleep(0.2)

        mock_toast.assert_called_once()
        title, _ = mock_toast.call_args[0]
        assert "Error" in title or "error" in title.lower()


# ===========================================================================
# TestFindPythonw
# ===========================================================================


class TestFindPythonw:
    """Tests for the module-level _find_pythonw() function."""

    def test_returns_pythonw_on_windows(self, tmp_path):
        """On Windows, if pythonw.exe exists next to python.exe, return it."""
        fake_python = tmp_path / "python.exe"
        fake_pythonw = tmp_path / "pythonw.exe"
        fake_python.write_text("fake", encoding="utf-8")
        fake_pythonw.write_text("fake", encoding="utf-8")

        with patch("tray_app.sys") as mock_sys:
            mock_sys.platform = "win32"
            mock_sys.executable = str(fake_python)
            result = _find_pythonw()

        assert result == str(fake_pythonw)

    def test_returns_sys_executable_fallback(self, tmp_path):
        """On Windows, if pythonw.exe is missing, fall back to sys.executable."""
        fake_python = tmp_path / "python.exe"
        fake_python.write_text("fake", encoding="utf-8")
        # Do NOT create pythonw.exe

        with patch("tray_app.sys") as mock_sys:
            mock_sys.platform = "win32"
            mock_sys.executable = str(fake_python)
            result = _find_pythonw()

        assert result == str(fake_python)

    def test_returns_sys_executable_on_mac(self):
        """On macOS, always return sys.executable."""
        with patch("tray_app.sys") as mock_sys:
            mock_sys.platform = "darwin"
            mock_sys.executable = "/usr/local/bin/python3"
            result = _find_pythonw()

        assert result == "/usr/local/bin/python3"


# ===========================================================================
# TestReloadConfig
# ===========================================================================


class TestReloadConfig:
    """Tests for _reload_config()."""

    def test_reloads_from_file(self, app, config_file_path):
        """Should read and parse config.json into _config."""
        config_data = {
            "schedule": {"daily_sync_time": "17:00"},
            "user": {"name": "Test"},
        }
        config_file_path.write_text(json.dumps(config_data), encoding="utf-8")
        app._reload_config()
        assert app._config is not None
        assert app._config["schedule"]["daily_sync_time"] == "17:00"

    def test_silently_handles_missing_file(self, app, config_file_path):
        """Should not raise when config.json does not exist."""
        assert not config_file_path.exists()
        app._config = None
        app._reload_config()  # should not raise
        assert app._config is None

    def test_silently_handles_json_error(self, app, config_file_path):
        """Should not raise when config.json contains invalid JSON."""
        config_file_path.write_text("{ this is not valid json !!!", encoding="utf-8")
        app._config = {"old": "value"}
        app._reload_config()  # should not raise
        # _config should retain its old value (exception was swallowed)
        assert app._config == {"old": "value"}


# ===========================================================================
# TestScheduleNextSync
# ===========================================================================


class TestScheduleNextSync:
    """Tests for _schedule_next_sync()."""

    def test_cancels_existing_timer(self, app):
        """If a timer already exists, it should be cancelled."""
        old_timer = MagicMock()
        app._timer = old_timer
        app._config = {"schedule": {"daily_sync_time": "18:00"}}

        with patch("tray_app.threading.Timer") as MockTimer:
            mock_new_timer = MagicMock()
            MockTimer.return_value = mock_new_timer
            app._schedule_next_sync()

        old_timer.cancel.assert_called_once()

    def test_creates_daemon_timer(self, app):
        """New timer should be created as a daemon thread and started."""
        app._config = {"schedule": {"daily_sync_time": "18:00"}}

        with patch("tray_app.threading.Timer") as MockTimer:
            mock_timer = MagicMock()
            MockTimer.return_value = mock_timer
            app._schedule_next_sync()

        MockTimer.assert_called_once()
        # The second arg to Timer() should be the callback
        assert MockTimer.call_args[0][1] == app._on_timer_fired
        assert mock_timer.daemon is True
        mock_timer.start.assert_called_once()

    def test_schedules_for_tomorrow_if_time_passed(self, app):
        """If today's sync time already passed, schedule for tomorrow."""
        app._config = {"schedule": {"daily_sync_time": "06:00"}}

        # Mock datetime.now to return 10:00 (past the 06:00 sync time)
        fake_now = datetime(2026, 2, 22, 10, 0, 0)
        with patch.object(app, "_reload_config"):  # prevent overwriting injected config
            with patch("tray_app.datetime") as mock_dt:
                mock_dt.now.return_value = fake_now
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

                with patch("tray_app.threading.Timer") as MockTimer:
                    mock_timer = MagicMock()
                    MockTimer.return_value = mock_timer
                    app._schedule_next_sync()

            # Delay should be roughly 20 hours (tomorrow 06:00 - today 10:00)
            delay_seconds = MockTimer.call_args[0][0]
            assert delay_seconds > 18 * 3600  # more than 18 hours
            assert delay_seconds < 24 * 3600  # less than 24 hours


# ===========================================================================
# TestBGColors
# ===========================================================================


class TestBGColors:
    """Verify the BG_COLORS constant."""

    def test_has_expected_keys(self):
        """BG_COLORS must have green, orange, and red entries."""
        assert "green" in BG_COLORS
        assert "orange" in BG_COLORS
        assert "red" in BG_COLORS

    def test_values_are_rgb_tuples(self):
        """Each value should be a 3-tuple of ints in 0..255."""
        for color, rgb in BG_COLORS.items():
            assert isinstance(rgb, tuple), f"{color} is not a tuple"
            assert len(rgb) == 3, f"{color} tuple length != 3"
            for channel in rgb:
                assert 0 <= channel <= 255, f"{color} channel {channel} out of range"
