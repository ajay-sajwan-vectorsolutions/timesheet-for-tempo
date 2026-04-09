"""Tests for TempoAutomation.post_install_check()."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tempo_automation import TempoAutomation  # noqa: E402


@pytest.fixture
def automation():
    """Create a TempoAutomation with mocked internals."""
    with patch.object(TempoAutomation, "__init__", lambda self, *a, **kw: None):
        auto = TempoAutomation.__new__(TempoAutomation)
        auto.schedule_mgr = MagicMock()
        auto.schedule_mgr.daily_hours = 8.0
        auto.tempo_client = MagicMock()
        auto.jira_client = MagicMock()
        auto.config = {}
        return auto


class TestPostInstallCheckNoGaps:
    """When no shortfall exists, print success and return."""

    def test_no_gaps_prints_up_to_date(self, automation, capsys):
        """Should print 'All hours are up to date' when no gaps detected."""
        automation._detect_monthly_gaps = MagicMock(
            return_value={
                "period": "2026-04",
                "expected": 64.0,
                "actual": 64.0,
                "gaps": [],
                "working_days": 8,
                "day_details": [],
            }
        )

        automation.post_install_check()

        output = capsys.readouterr().out
        assert "All hours are up to date" in output

    def test_no_gaps_does_not_call_backfill(self, automation, capsys):
        """Should not call backfill_range when there are no gaps."""
        automation._detect_monthly_gaps = MagicMock(
            return_value={
                "period": "2026-04",
                "expected": 64.0,
                "actual": 64.0,
                "gaps": [],
                "working_days": 8,
                "day_details": [],
            }
        )
        automation.backfill_range = MagicMock()

        automation.post_install_check()

        automation.backfill_range.assert_not_called()


class TestPostInstallCheckWithGaps:
    """When shortfall exists, display table and prompt user."""

    GAP_DATA = {
        "period": "2026-04",
        "expected": 72.0,
        "actual": 56.0,
        "gaps": [
            {"date": "2026-04-06", "day": "Monday", "logged": 0.0, "expected": 8.0, "gap": 8.0},
            {"date": "2026-04-08", "day": "Wednesday", "logged": 0.0, "expected": 8.0, "gap": 8.0},
        ],
        "working_days": 9,
        "day_details": [],
    }

    def test_displays_gap_table(self, automation, capsys):
        """Should print the shortfall header and gap days."""
        automation._detect_monthly_gaps = MagicMock(return_value=self.GAP_DATA)
        automation.backfill_range = MagicMock()

        with patch("builtins.input", return_value="n"):
            automation.post_install_check()

        output = capsys.readouterr().out
        assert "SHORTFALL DETECTED" in output
        assert "2026-04-06" in output
        assert "2026-04-08" in output
        assert "16.0h" in output

    def test_user_accepts_calls_backfill(self, automation, capsys):
        """User entering 'y' should trigger backfill_range."""
        automation._detect_monthly_gaps = MagicMock(return_value=self.GAP_DATA)
        automation.backfill_range = MagicMock()

        with (
            patch("builtins.input", return_value="y"),
            patch("tempo_automation.SHORTFALL_FILE") as mock_sf,
        ):
            mock_sf.exists.return_value = False
            automation.post_install_check()

        automation.backfill_range.assert_called_once_with("2026-04-06", "2026-04-08")

    def test_user_accepts_cleans_shortfall_file(self, automation, capsys):
        """After successful backfill, shortfall file should be removed immediately."""
        automation._detect_monthly_gaps = MagicMock(return_value=self.GAP_DATA)
        automation.backfill_range = MagicMock()

        with (
            patch("builtins.input", return_value="y"),
            patch("tempo_automation.SHORTFALL_FILE") as mock_sf,
        ):
            mock_sf.exists.return_value = True
            automation.post_install_check()

        mock_sf.unlink.assert_called_once_with(missing_ok=True)
        output = capsys.readouterr().out
        assert "All gaps fixed" in output

    def test_user_declines_shows_fix_command(self, automation, capsys):
        """User entering 'n' should show the --fix-shortfall hint."""
        automation._detect_monthly_gaps = MagicMock(return_value=self.GAP_DATA)
        automation._save_shortfall_data = MagicMock()
        automation.backfill_range = MagicMock()

        with patch("builtins.input", return_value="n"):
            automation.post_install_check()

        output = capsys.readouterr().out
        assert "Fix Monthly Shortfall" in output
        automation.backfill_range.assert_not_called()
        automation._save_shortfall_data.assert_called_once()

    def test_single_gap_day_backfills_same_date(self, automation, capsys):
        """When only one gap day, from_date and to_date should be the same."""
        single_gap = {
            "period": "2026-04",
            "expected": 72.0,
            "actual": 64.0,
            "gaps": [
                {
                    "date": "2026-04-08",
                    "day": "Wednesday",
                    "logged": 0.0,
                    "expected": 8.0,
                    "gap": 8.0,
                },
            ],
            "working_days": 9,
            "day_details": [],
        }
        automation._detect_monthly_gaps = MagicMock(return_value=single_gap)
        automation.backfill_range = MagicMock()

        with (
            patch("builtins.input", return_value="y"),
            patch("tempo_automation.SHORTFALL_FILE") as mock_sf,
        ):
            mock_sf.exists.return_value = False
            automation.post_install_check()

        automation.backfill_range.assert_called_once_with("2026-04-08", "2026-04-08")
