# E006: Post-Install Shortfall Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After installation, detect missing hours in the current month and offer to backfill them with a Y/N prompt.

**Architecture:** New `post_install_check()` method on `TempoAutomation` reuses existing `_detect_monthly_gaps()` and `backfill_range()`. New `--post-install-check` CLI arg. New Step 8 in `install.bat` calls it.

**Tech Stack:** Python 3.7+, batch script

**Spec:** `docs/superpowers/specs/2026-04-09-post-install-shortfall-check-design.md`

---

### Task 1: Write failing test for `post_install_check()` -- no gaps case

**Files:**
- Test: `tests/unit/test_post_install_check.py` (create)

- [ ] **Step 1: Create test file with no-gaps test**

```python
"""Tests for TempoAutomation.post_install_check()."""

import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tempo_automation import TempoAutomation


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
        automation._detect_monthly_gaps = MagicMock(return_value={
            "period": "2026-04",
            "expected": 64.0,
            "actual": 64.0,
            "gaps": [],
            "working_days": 8,
            "day_details": [],
        })

        automation.post_install_check()

        output = capsys.readouterr().out
        assert "All hours are up to date" in output

    def test_no_gaps_does_not_call_backfill(self, automation, capsys):
        """Should not call backfill_range when there are no gaps."""
        automation._detect_monthly_gaps = MagicMock(return_value={
            "period": "2026-04",
            "expected": 64.0,
            "actual": 64.0,
            "gaps": [],
            "working_days": 8,
            "day_details": [],
        })
        automation.backfill_range = MagicMock()

        automation.post_install_check()

        automation.backfill_range.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_post_install_check.py -v`
Expected: FAIL with `AttributeError: 'TempoAutomation' object has no attribute 'post_install_check'`

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_post_install_check.py
git commit -m "test: add failing tests for post_install_check no-gaps case"
```

---

### Task 2: Write failing test for `post_install_check()` -- gaps found, user accepts

**Files:**
- Modify: `tests/unit/test_post_install_check.py`

- [ ] **Step 1: Add gaps-with-accept tests**

Append to `tests/unit/test_post_install_check.py`:

```python
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
        assert "16.0h missing" in output or "16.0h" in output

    def test_user_accepts_calls_backfill(self, automation, capsys):
        """User entering 'y' should trigger backfill_range."""
        automation._detect_monthly_gaps = MagicMock(return_value=self.GAP_DATA)
        automation.backfill_range = MagicMock()

        with patch("builtins.input", return_value="y"):
            automation.post_install_check()

        automation.backfill_range.assert_called_once_with("2026-04-06", "2026-04-08")

    def test_user_declines_shows_fix_command(self, automation, capsys):
        """User entering 'n' should show the --fix-shortfall hint."""
        automation._detect_monthly_gaps = MagicMock(return_value=self.GAP_DATA)
        automation.backfill_range = MagicMock()

        with patch("builtins.input", return_value="n"):
            automation.post_install_check()

        output = capsys.readouterr().out
        assert "--fix-shortfall" in output
        automation.backfill_range.assert_not_called()

    def test_single_gap_day_backfills_same_date(self, automation, capsys):
        """When only one gap day, from_date and to_date should be the same."""
        single_gap = {
            "period": "2026-04",
            "expected": 72.0,
            "actual": 64.0,
            "gaps": [
                {"date": "2026-04-08", "day": "Wednesday", "logged": 0.0, "expected": 8.0, "gap": 8.0},
            ],
            "working_days": 9,
            "day_details": [],
        }
        automation._detect_monthly_gaps = MagicMock(return_value=single_gap)
        automation.backfill_range = MagicMock()

        with patch("builtins.input", return_value="y"):
            automation.post_install_check()

        automation.backfill_range.assert_called_once_with("2026-04-08", "2026-04-08")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_post_install_check.py -v`
Expected: FAIL -- `post_install_check` not defined

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_post_install_check.py
git commit -m "test: add failing tests for post_install_check with gaps"
```

---

### Task 3: Implement `post_install_check()` method

**Files:**
- Modify: `tempo_automation.py:4507` (insert before `backfill_range`)

- [ ] **Step 1: Add `post_install_check()` method to `TempoAutomation` class**

Insert before the `# Date-range backfill` comment (line 4504) in `tempo_automation.py`:

```python
    def post_install_check(self):
        """Detect and offer to fix monthly shortfall after installation.

        Called by install.bat as the final step.  Checks the current month
        for working days with missing hours and offers a Y/N backfill.
        """
        today = date.today()
        year, month = today.year, today.month
        month_name = calendar.month_name[month]

        print(f"\n{'=' * 60}")
        print(f"POST-INSTALL CHECK - {month_name} {year}")
        print(f"{'=' * 60}\n")

        gap_data = self._detect_monthly_gaps(year, month)

        if not gap_data["gaps"]:
            print(f"[OK] All hours are up to date for {month_name} {year}.")
            print()
            return

        shortfall = gap_data["expected"] - gap_data["actual"]
        gap_count = len(gap_data["gaps"])

        print(f"  SHORTFALL DETECTED FOR {month_name.upper()} {year}")
        print(f"  {'=' * 50}")
        print(f"  {'Date':<12} {'Day':<12} {'Logged':>7} {'Expected':>9} {'Gap':>6}")
        print(f"  {'-' * 50}")

        for g in gap_data["gaps"]:
            print(
                f"  {g['date']:<12} {g['day']:<12} "
                f"{g['logged']:>6.1f}h "
                f"{g['expected']:>8.1f}h "
                f"{g['gap']:>5.1f}h"
            )

        print(f"  {'=' * 50}")
        print(f"  Total: {shortfall:.1f}h missing across {gap_count} day(s)")
        print()

        try:
            answer = input("  Would you like to sync hours for these days now? (Y/N): ")
        except (EOFError, KeyboardInterrupt):
            answer = "n"

        if answer.strip().lower() == "y":
            first_gap = gap_data["gaps"][0]["date"]
            last_gap = gap_data["gaps"][-1]["date"]
            print()
            self.backfill_range(first_gap, last_gap)
        else:
            print()
            print("  You can fix this later with:")
            print("    python tempo_automation.py --fix-shortfall")
            print()
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `pytest tests/unit/test_post_install_check.py -v`
Expected: All 6 tests PASS

- [ ] **Step 3: Commit**

```bash
git add tempo_automation.py
git commit -m "feat: add post_install_check() for E006 shortfall detection"
```

---

### Task 4: Add `--post-install-check` CLI argument

**Files:**
- Modify: `tempo_automation.py:5247` (argparse section, near backfill args)
- Modify: `tempo_automation.py:5385` (dispatch section, near backfill dispatch)

- [ ] **Step 1: Add argparse argument**

After the `--fix-shortfall` argument (line 5244), add:

```python
    parser.add_argument(
        "--post-install-check",
        action="store_true",
        help="Check for monthly shortfall after installation and offer to backfill",
    )
```

- [ ] **Step 2: Add to quiet_console list**

In the `quiet_console` tuple (around line 5292), add `or args.post_install_check` after `args.fix_shortfall`:

```python
        or args.fix_shortfall
        or args.post_install_check
```

- [ ] **Step 3: Add dispatch logic**

In the dispatch section, after the `fix_shortfall` block (line 5383), add:

```python
    elif args.post_install_check:
        automation.post_install_check()
```

- [ ] **Step 4: Run a quick CLI smoke test**

Run: `python tempo_automation.py --post-install-check --dry-run`
Expected: Should show "POST-INSTALL CHECK" header and either gaps or "All hours are up to date"

Note: `--dry-run` won't affect the check (it only affects sync writes), but it verifies the CLI arg is wired up.

- [ ] **Step 5: Commit**

```bash
git add tempo_automation.py
git commit -m "feat: add --post-install-check CLI argument for E006"
```

---

### Task 5: Add Step 8 to install.bat

**Files:**
- Modify: `install.bat:522` (after Step 7 closing parenthesis, before "Installation complete")

- [ ] **Step 1: Add Step 8 to install.bat**

Insert after line 523 (`echo.` after Step 7) and before the "Installation complete" section (line 525):

```batch
REM ============================================================================
REM Post-install shortfall check
REM ============================================================================

echo [8/8] Checking for missing hours this month...
echo.
"%PYTHON_EXE%" tempo_automation.py --post-install-check
echo.
```

- [ ] **Step 2: Update step numbering**

Change Step 7 label from `[7/7]` to `[7/8]` on line 509:

```batch
echo [7/8] Test sync (optional)
```

- [ ] **Step 3: Verify the batch file syntax**

Run: `python -c "print('install.bat syntax check passed')"`

(No batch syntax checker available, but visually confirm the REM blocks and echo/call structure match the surrounding code.)

- [ ] **Step 4: Commit**

```bash
git add install.bat
git commit -m "feat: add Step 8 post-install shortfall check to install.bat"
```

---

### Task 6: Run full test suite and verify

**Files:** None (verification only)

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ -v --tb=short`
Expected: All tests pass (including new `test_post_install_check.py`)

- [ ] **Step 2: Run the CLI command manually to verify end-to-end**

Run: `python tempo_automation.py --post-install-check`
Expected: Shows current month shortfall table or "All hours are up to date"

- [ ] **Step 3: Final commit if any fixups needed**

```bash
git add -A
git commit -m "fix: post-install check fixups from verification"
```
