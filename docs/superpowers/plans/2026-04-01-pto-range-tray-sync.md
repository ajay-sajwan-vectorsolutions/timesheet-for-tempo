# PTO Range & Tray Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add PTO date-range support and a post-add Tempo sync offer to the tray app.

**Architecture:** Add `expand_date_range()` to `ScheduleManager` (pure logic, no I/O), then wire the new tray flow in `TrayApp`: a Yes/No range dialog → one or two date input dialogs → `add_pto()` → a Yes/No sync offer → background `sync_daily()` per future date. Remove the now-unused `_process_pto_input`.

**Tech Stack:** Python 3.7+, ctypes (Windows dialogs), subprocess/osascript (Mac dialogs), threading (background sync), pytest + unittest.mock (tests).

---

## File Map

| File | Change |
|------|--------|
| `tempo_automation.py` | Add `ScheduleManager.expand_date_range()` |
| `tray_app.py` | Add `_show_yesno_dialog()`, `_sync_pto_dates_background()`, replace `_on_add_pto()`, remove `_process_pto_input()` |
| `tests/unit/test_schedule_manager.py` | Add `TestExpandDateRange` class |
| `tests/unit/test_tray_app.py` | Remove `TestProcessPtoInput`, add `TestShowYesnoDialog`, `TestSyncPtoDatesBackground`, `TestOnAddPto` |

---

## Task 1: `ScheduleManager.expand_date_range()`

**Files:**
- Modify: `tempo_automation.py` (inside `ScheduleManager`, after `add_pto()` at ~line 1407)
- Test: `tests/unit/test_schedule_manager.py` (new class after `TestPto`)

### Step 1.1 — Write failing tests

Add this class at the end of `tests/unit/test_schedule_manager.py`:

```python
# ===========================================================================
# TestExpandDateRange
# ===========================================================================

class TestExpandDateRange:
    """Tests for ScheduleManager.expand_date_range()."""

    def test_single_working_day(self, sm, tmp_path):
        """A range of one weekday returns that day."""
        result = sm.expand_date_range("2026-03-02", "2026-03-02")  # Monday
        assert result == ["2026-03-02"]

    def test_range_skips_weekend(self, sm, tmp_path):
        """Mon-Sun range returns only the 5 weekdays."""
        result = sm.expand_date_range("2026-03-02", "2026-03-08")
        assert "2026-03-07" not in result  # Saturday
        assert "2026-03-08" not in result  # Sunday
        assert len(result) == 5

    def test_range_skips_org_holiday(self, tmp_path):
        """A day marked as org holiday is excluded."""
        config = {
            "schedule": {
                "daily_hours": 8.0,
                "country_code": "US",
                "state": "",
                "pto_days": [],
                "extra_holidays": [],
                "working_days": [],
            },
            "organization": {"holidays_url": ""},
        }
        # Inject a known org holiday into the range
        org_data = {"holidays": {"US": {"national": [{"date": "2026-03-04", "name": "Test Holiday"}], "MH": [], "TG": [], "GJ": []}, "IN": {"national": [], "MH": [], "TG": [], "GJ": []}}}
        sm2 = _make_schedule_manager(config, org_data, tmp_path=tmp_path)
        result = sm2.expand_date_range("2026-03-02", "2026-03-06")
        assert "2026-03-04" not in result  # org holiday
        assert len(result) == 4

    def test_start_after_end_raises(self, sm):
        """start > end raises ValueError."""
        with pytest.raises(ValueError, match="start_date"):
            sm.expand_date_range("2026-03-10", "2026-03-05")

    def test_invalid_start_date_raises(self, sm):
        """Bad format raises ValueError."""
        with pytest.raises(ValueError, match="Invalid"):
            sm.expand_date_range("not-a-date", "2026-03-10")

    def test_invalid_end_date_raises(self, sm):
        """Bad format raises ValueError."""
        with pytest.raises(ValueError, match="Invalid"):
            sm.expand_date_range("2026-03-02", "not-a-date")

    def test_all_weekend_range_returns_empty(self, sm):
        """A Sat-Sun range returns an empty list."""
        result = sm.expand_date_range("2026-03-07", "2026-03-08")
        assert result == []

    def test_multi_week_range(self, sm):
        """Two full working weeks return 10 dates."""
        result = sm.expand_date_range("2026-03-02", "2026-03-13")
        assert len(result) == 10
```

- [ ] Paste the `TestExpandDateRange` class into `tests/unit/test_schedule_manager.py` (after the closing of `TestPto`).

### Step 1.2 — Run tests to confirm they fail

```bash
pytest tests/unit/test_schedule_manager.py::TestExpandDateRange -v
```

Expected: All 8 tests fail with `AttributeError: 'ScheduleManager' object has no attribute 'expand_date_range'`.

- [ ] Run and confirm failures.

### Step 1.3 — Implement `expand_date_range()`

In `tempo_automation.py`, add this method to `ScheduleManager` directly after `add_pto()` (after line ~1407):

```python
def expand_date_range(self, start_date: str, end_date: str) -> list[str]:
    """
    Return all working days between start_date and end_date inclusive.

    Skips weekends, org holidays, country holidays, and extra holidays.
    Raises ValueError on bad date format or start > end.
    """
    if not self._validate_date(start_date):
        raise ValueError(f"Invalid start_date: {start_date!r}")
    if not self._validate_date(end_date):
        raise ValueError(f"Invalid end_date: {end_date!r}")
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    if start > end:
        raise ValueError(f"start_date {start_date!r} is after end_date {end_date!r}")
    working = []
    current = start
    while current <= end:
        d_str = current.strftime("%Y-%m-%d")
        is_working, _ = self.is_working_day(d_str)
        if is_working:
            working.append(d_str)
        current += timedelta(days=1)
    return working
```

- [ ] Add the method.

### Step 1.4 — Run tests to confirm they pass

```bash
pytest tests/unit/test_schedule_manager.py::TestExpandDateRange -v
```

Expected: All 8 tests pass.

- [ ] Run and confirm.

### Step 1.5 — Run full suite to check for regressions

```bash
pytest tests/ -q
```

Expected: All existing tests still pass (528 + 8 = 536 total).

- [ ] Run and confirm.

### Step 1.6 — Commit

```bash
git add tempo_automation.py tests/unit/test_schedule_manager.py
git commit -m "feat: add ScheduleManager.expand_date_range() with working-day filtering"
```

- [ ] Commit.

---

## Task 2: `TrayApp._show_yesno_dialog()`

**Files:**
- Modify: `tray_app.py` (add method after `_show_input_dialog_mac()` at ~line 715)
- Test: `tests/unit/test_tray_app.py` (new class `TestShowYesnoDialog`)

### Step 2.1 — Write failing tests

Add this class to `tests/unit/test_tray_app.py` (after `TestProcessPtoInput`, before `TestFindPythonw`):

```python
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
```

- [ ] Paste the `TestShowYesnoDialog` class into `tests/unit/test_tray_app.py`.

### Step 2.2 — Run tests to confirm they fail

```bash
pytest tests/unit/test_tray_app.py::TestShowYesnoDialog -v
```

Expected: All 5 tests fail with `AttributeError: 'TrayApp' object has no attribute '_show_yesno_dialog'`.

- [ ] Run and confirm failures.

### Step 2.3 — Implement `_show_yesno_dialog()`

In `tray_app.py`, add this method after `_show_input_dialog_mac()` (after ~line 715):

```python
def _show_yesno_dialog(self, msg: str, title: str) -> bool:
    """
    Show a Yes/No dialog. Returns True if user clicked Yes, False otherwise.
    Windows: MessageBoxW with MB_YESNO.
    Mac: osascript with Yes/No buttons.
    """
    if sys.platform == 'win32':
        # MB_YESNO=0x04 | MB_ICONQUESTION=0x20 | MB_TOPMOST=0x40000
        flags = 0x04 | 0x20 | 0x40000
        result = ctypes.windll.user32.MessageBoxW(0, msg, title, flags)
        return result == 6  # 6 = IDYES
    elif sys.platform == 'darwin':
        safe_msg = msg.replace('"', '\\"').replace('\n', '\\n')
        safe_title = title.replace('"', '\\"')
        script = (
            f'display dialog "{safe_msg}" '
            f'buttons {{"No", "Yes"}} '
            f'default button "Yes" '
            f'with title "{safe_title}"'
        )
        try:
            proc = subprocess.run(
                ['osascript', '-e', script],
                capture_output=True, text=True, timeout=120
            )
            return 'Yes' in proc.stdout
        except subprocess.TimeoutExpired:
            pass
    return False
```

- [ ] Add the method.

### Step 2.4 — Run tests to confirm they pass

```bash
pytest tests/unit/test_tray_app.py::TestShowYesnoDialog -v
```

Expected: All 5 tests pass.

- [ ] Run and confirm.

### Step 2.5 — Commit

```bash
git add tray_app.py tests/unit/test_tray_app.py
git commit -m "feat: add TrayApp._show_yesno_dialog() for Yes/No prompts"
```

- [ ] Commit.

---

## Task 3: `TrayApp._sync_pto_dates_background()`

**Files:**
- Modify: `tray_app.py` (add method after `_show_yesno_dialog()`)
- Test: `tests/unit/test_tray_app.py` (new class `TestSyncPtoDatesBackground`)

### Step 3.1 — Write failing tests

Add after `TestShowYesnoDialog`:

```python
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
            import time; time.sleep(0.2)

        assert app._automation.sync_daily.call_count == 2
        app._automation.sync_daily.assert_any_call("2026-04-07")
        app._automation.sync_daily.assert_any_call("2026-04-08")

    def test_shows_success_toast_after_sync(self, app):
        """A success toast is shown when all syncs complete."""
        app._automation = MagicMock()
        app._automation.sync_daily = MagicMock()

        with patch.object(app, "_show_toast") as mock_toast:
            app._sync_pto_dates_background(["2026-04-07"])
            import time; time.sleep(0.2)

        mock_toast.assert_called_once()
        title, _ = mock_toast.call_args[0]
        assert "Synced" in title or "PTO" in title

    def test_shows_error_toast_on_failure(self, app):
        """If sync_daily raises, an error toast is shown and sync continues."""
        app._automation = MagicMock()
        app._automation.sync_daily = MagicMock(side_effect=RuntimeError("API down"))

        with patch.object(app, "_show_toast") as mock_toast:
            app._sync_pto_dates_background(["2026-04-07"])
            import time; time.sleep(0.2)

        mock_toast.assert_called_once()
        title, _ = mock_toast.call_args[0]
        assert "Error" in title or "error" in title.lower()
```

- [ ] Paste the `TestSyncPtoDatesBackground` class into `tests/unit/test_tray_app.py`.

### Step 3.2 — Run tests to confirm they fail

```bash
pytest tests/unit/test_tray_app.py::TestSyncPtoDatesBackground -v
```

Expected: All 3 tests fail with `AttributeError: 'TrayApp' object has no attribute '_sync_pto_dates_background'`.

- [ ] Run and confirm failures.

### Step 3.3 — Implement `_sync_pto_dates_background()`

In `tray_app.py`, add after `_show_yesno_dialog()`:

```python
def _sync_pto_dates_background(self, dates: list):
    """Sync PTO overhead hours to Tempo for each date in a daemon thread."""
    def _run():
        synced = 0
        for d in dates:
            try:
                self._automation.sync_daily(d)
                synced += 1
            except Exception as e:
                tray_logger.error(f"PTO sync failed for {d}: {e}", exc_info=True)
                self._show_toast('Sync Error', f'Failed to sync {d}: {e}')
                return
        self._show_toast('PTO Synced', f'Synced {synced} day(s) to Tempo.')

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
```

- [ ] Add the method.

### Step 3.4 — Run tests to confirm they pass

```bash
pytest tests/unit/test_tray_app.py::TestSyncPtoDatesBackground -v
```

Expected: All 3 tests pass.

- [ ] Run and confirm.

### Step 3.5 — Commit

```bash
git add tray_app.py tests/unit/test_tray_app.py
git commit -m "feat: add TrayApp._sync_pto_dates_background() for async PTO tempo sync"
```

- [ ] Commit.

---

## Task 4: Replace `_on_add_pto`, remove `_process_pto_input`

**Files:**
- Modify: `tray_app.py` — replace `_on_add_pto()`, delete `_process_pto_input()`
- Test: `tests/unit/test_tray_app.py` — delete `TestProcessPtoInput`, add `TestOnAddPto`

### Step 4.1 — Delete `TestProcessPtoInput` and write new failing tests

In `tests/unit/test_tray_app.py`:

1. Delete the entire `TestProcessPtoInput` class (lines ~344–411).
2. Update the docstring coverage targets at the top of the file: change `- TestProcessPtoInput: 4 tests` to `- TestOnAddPto: 6 tests`.
3. Add this class after `TestSyncPtoDatesBackground`:

```python
# ===========================================================================
# TestOnAddPto
# ===========================================================================

class TestOnAddPto:
    """Tests for the revised TrayApp._on_add_pto() flow."""

    def _make_app_with_schedule(self, add_pto_return=None, overhead=True):
        """Helper: TrayApp with mocked automation and schedule manager."""
        app = TrayApp()
        mock_schedule = MagicMock()
        mock_schedule.add_pto.return_value = add_pto_return or ([], [])
        mock_schedule.expand_date_range.return_value = []
        mock_automation = MagicMock()
        mock_automation.schedule_mgr = mock_schedule
        mock_automation._is_overhead_configured.return_value = overhead
        app._automation = mock_automation
        return app, mock_schedule, mock_automation

    def test_no_automation_shows_error_toast(self):
        """If _automation is None, show an error toast and return."""
        app = TrayApp()
        app._automation = None
        with patch.object(app, "_show_toast") as mock_toast:
            app._on_add_pto()
        mock_toast.assert_called_once()
        assert "Error" in mock_toast.call_args[0][0]

    def test_range_flow_calls_expand_date_range(self):
        """When user picks Yes (range), expand_date_range is called."""
        app, mock_schedule, _ = self._make_app_with_schedule(
            add_pto_return=(["2026-04-07", "2026-04-08"], [])
        )
        mock_schedule.expand_date_range.return_value = ["2026-04-07", "2026-04-08"]

        with patch.object(app, "_show_yesno_dialog", side_effect=[True, False]), \
             patch.object(app, "_show_input_dialog", side_effect=["2026-04-07", "2026-04-08"]), \
             patch.object(app, "_show_toast"), \
             patch("tray_app.date") as mock_date:
            mock_date.today.return_value = date(2026, 4, 6)
            app._on_add_pto()

        mock_schedule.expand_date_range.assert_called_once_with("2026-04-07", "2026-04-08")

    def test_single_day_flow_skips_expand(self):
        """When user picks No (single day), expand_date_range is NOT called."""
        app, mock_schedule, _ = self._make_app_with_schedule(
            add_pto_return=(["2026-04-07"], [])
        )

        with patch.object(app, "_show_yesno_dialog", side_effect=[False, False]), \
             patch.object(app, "_show_input_dialog", return_value="2026-04-07"), \
             patch.object(app, "_show_toast"), \
             patch("tray_app.date") as mock_date:
            mock_date.today.return_value = date(2026, 4, 6)
            app._on_add_pto()

        mock_schedule.expand_date_range.assert_not_called()
        mock_schedule.add_pto.assert_called_once_with(["2026-04-07"])

    def test_cancelled_input_returns_early(self):
        """If user cancels the start date dialog, add_pto is never called."""
        app, mock_schedule, _ = self._make_app_with_schedule()

        with patch.object(app, "_show_yesno_dialog", return_value=True), \
             patch.object(app, "_show_input_dialog", return_value=""), \
             patch.object(app, "_show_toast"):
            app._on_add_pto()

        mock_schedule.add_pto.assert_not_called()

    def test_future_dates_trigger_sync_offer(self):
        """Future dates cause the Tempo sync Yes/No dialog to appear."""
        app, mock_schedule, mock_auto = self._make_app_with_schedule(
            add_pto_return=(["2026-04-10"], [])
        )

        with patch.object(app, "_show_yesno_dialog", side_effect=[False, False]) as mock_yn, \
             patch.object(app, "_show_input_dialog", return_value="2026-04-10"), \
             patch.object(app, "_show_toast"), \
             patch("tray_app.date") as mock_date:
            mock_date.today.return_value = date(2026, 4, 1)
            app._on_add_pto()

        # Second _show_yesno_dialog call = the sync offer
        assert mock_yn.call_count == 2

    def test_sync_yes_calls_sync_background(self):
        """If user says Yes to sync, _sync_pto_dates_background is called."""
        app, mock_schedule, mock_auto = self._make_app_with_schedule(
            add_pto_return=(["2026-04-10"], [])
        )

        with patch.object(app, "_show_yesno_dialog", side_effect=[False, True]), \
             patch.object(app, "_show_input_dialog", return_value="2026-04-10"), \
             patch.object(app, "_show_toast"), \
             patch.object(app, "_sync_pto_dates_background") as mock_bg, \
             patch("tray_app.date") as mock_date:
            mock_date.today.return_value = date(2026, 4, 1)
            app._on_add_pto()

        mock_bg.assert_called_once_with(["2026-04-10"])
```

- [ ] Delete `TestProcessPtoInput` from the test file.
- [ ] Update coverage targets comment at the top.
- [ ] Add `TestOnAddPto` class.

### Step 4.2 — Run tests to confirm they fail

```bash
pytest tests/unit/test_tray_app.py::TestOnAddPto -v
```

Expected: Tests fail because `_on_add_pto` still has the old implementation.

- [ ] Run and confirm failures.

### Step 4.3 — Replace `_on_add_pto()` in `tray_app.py`

Replace the existing `_on_add_pto()` method (lines ~624–642) with:

```python
def _on_add_pto(self, icon=None, item=None):
    """Add PTO via two-step dialog: range or single day, then optional Tempo sync."""
    if self._automation is None:
        msg = self._import_error or 'Automation not loaded'
        self._show_toast('Error', msg)
        return

    try:
        use_range = self._show_yesno_dialog(
            'Add PTO for a date range?\n\n'
            'Yes = enter start and end date\n'
            'No  = enter a single date',
            'Tempo - Add PTO'
        )

        if use_range:
            start = self._show_input_dialog(
                'Enter the START date (YYYY-MM-DD):', 'Tempo - Add PTO Range'
            )
            if not start:
                return
            end = self._show_input_dialog(
                'Enter the END date (YYYY-MM-DD):', 'Tempo - Add PTO Range'
            )
            if not end:
                return
            try:
                dates = self._automation.schedule_mgr.expand_date_range(
                    start.strip(), end.strip()
                )
            except ValueError as e:
                self._show_toast('Invalid Range', str(e))
                return
            if not dates:
                self._show_toast('No Working Days', 'No working days found in that range.')
                return
        else:
            single = self._show_input_dialog(
                'Enter the PTO date (YYYY-MM-DD):', 'Tempo - Add PTO'
            )
            if not single:
                return
            dates = [single.strip()]

        added, skipped = self._automation.schedule_mgr.add_pto(dates)

        if added and skipped:
            self._show_toast(
                'PTO Added (with warnings)',
                f'Added: {", ".join(added)}\nSkipped: {"; ".join(skipped)}'
            )
        elif added:
            self._show_toast('PTO Added', f'Added {len(added)} day(s): {", ".join(added)}')
        else:
            self._show_toast(
                'No PTO Added',
                '\n'.join(skipped) if skipped else 'No valid dates entered.'
            )

        if not added:
            return

        today = date.today()
        future_dates = [d for d in added if d >= today.strftime('%Y-%m-%d')]
        if not future_dates:
            return

        if not self._automation._is_overhead_configured():
            self._show_toast(
                'PTO Added',
                'Overhead story not configured. PTO saved but cannot sync to Tempo.'
            )
            return

        n = len(future_dates)
        date_list = ', '.join(future_dates)
        want_sync = self._show_yesno_dialog(
            f'Sync {n} PTO day(s) to Tempo now?\n\n{date_list}',
            'Tempo - Sync PTO'
        )
        if want_sync:
            self._sync_pto_dates_background(future_dates)
        else:
            self._show_toast('PTO Added', 'PTO saved. Not synced to Tempo.')

    except Exception as e:
        self._show_toast('Error', f'Could not add PTO: {e}')
        tray_logger.error(f"Add PTO failed: {e}", exc_info=True)
```

- [ ] Replace `_on_add_pto()`.

### Step 4.4 — Delete `_process_pto_input()` from `tray_app.py`

Remove the entire `_process_pto_input()` method (lines ~717–744).

- [ ] Delete `_process_pto_input()`.

### Step 4.5 — Run new tests to confirm they pass

```bash
pytest tests/unit/test_tray_app.py::TestOnAddPto -v
```

Expected: All 6 tests pass.

- [ ] Run and confirm.

### Step 4.6 — Run full suite

```bash
pytest tests/ -q
```

Expected: 546 tests pass (528 baseline + 8 Task1 + 5 Task2 + 3 Task3 + 6 new − 4 removed).

- [ ] Run and confirm no regressions.

### Step 4.7 — Commit

```bash
git add tray_app.py tests/unit/test_tray_app.py
git commit -m "feat: replace _on_add_pto with range+sync flow, remove _process_pto_input"
```

- [ ] Commit.

---

## Task 5: Push and open PR

### Step 5.1 — Push branch

```bash
git push origin feature/pto-range-and-tray-sync
```

- [ ] Push.

### Step 5.2 — Open PR

```bash
gh pr create \
  --title "feat: PTO range selection and Tempo sync from tray (E004)" \
  --base main \
  --body "## Summary
- Add \`ScheduleManager.expand_date_range()\` — expands a start/end range to working days only (skips weekends + holidays)
- Add \`TrayApp._show_yesno_dialog()\` — reusable Yes/No dialog for Windows + Mac
- Add \`TrayApp._sync_pto_dates_background()\` — syncs PTO days to Tempo in a daemon thread
- Replace \`TrayApp._on_add_pto()\` — new flow: range-or-single dialog → add PTO → offer Tempo sync for today/future dates
- Remove \`TrayApp._process_pto_input()\` (dead code)

## Test Plan
- [ ] 538 tests passing (\`pytest tests/ -q\`)
- [ ] \`expand_date_range\` tested: single day, skip weekends, skip org holiday, invalid input, empty range
- [ ] \`_show_yesno_dialog\` tested: Windows yes/no, Mac yes/no, unknown platform
- [ ] \`_sync_pto_dates_background\` tested: calls sync_daily, success toast, error toast
- [ ] \`_on_add_pto\` tested: no automation, range flow, single flow, cancel, sync offer, sync accepted"
```

- [ ] Create PR.
