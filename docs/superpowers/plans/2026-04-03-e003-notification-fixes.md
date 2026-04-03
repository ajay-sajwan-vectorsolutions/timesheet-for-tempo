# E003: Notification & Schedule Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three tray app bugs: (1) scheduled sync firing at wrong time due to `<=` boundary condition, (2) no toast notification shown when manual sync runs on a PTO day, (3) Task Scheduler / tray startup not honoring config sync time after fresh install or config edits.

**Architecture:** Bug 1 is a one-line operator fix in `_schedule_next_sync()`. Bug 2 requires `_sync_pto_overhead()` to return a result dict (like normal sync does) so the tray app's `_run_sync()` can show an appropriate toast, plus a toast for the `result is None` case. Bug 3 requires `install.bat` to read sync time from config instead of hardcoding 18:00, and the tray app to reconcile Task Scheduler on startup.

**Tech Stack:** Python 3.14, pystray, pytest

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `tray_app.py:295` | Modify | Fix `<=` to `<` in `_schedule_next_sync()` |
| `tray_app.py:514-518` | Modify | Add toast notification for `result is None` (PTO/non-working day skip) |
| `tray_app.py` (startup) | Modify | Reconcile Task Scheduler time with config on tray startup |
| `tempo_automation.py:2648-2716` | Modify | Make `_sync_pto_overhead()` return a result dict |
| `tempo_automation.py:2838` | Modify | Return the result from `_sync_pto_overhead()` in `sync_daily()` |
| `install.bat:389` | Modify | Read sync time from config instead of hardcoding 18:00 |
| `tests/unit/test_tray_app.py` | Modify | Add tests for schedule boundary + PTO notification + startup reconcile |
| `tests/unit/test_tempo_automation.py` | Modify | Add test for `_sync_pto_overhead()` return value |

---

## Bug 1: Schedule Boundary Fix

### Task 1: Fix `<=` operator and add boundary test

**Files:**
- Modify: `tray_app.py:295`
- Test: `tests/unit/test_tray_app.py` (class `TestScheduleNextSync`)

**Analysis:**

Line 295 currently:
```python
if target <= now:
    target += timedelta(days=1)
```

When `now` is exactly at the configured time (e.g., 11:00:00.123456) and `target` is 11:00:00.000000, the `<=` causes the timer to schedule for **tomorrow** instead of firing now. This is wrong — if we're at or past the exact second, `_maybe_sync_on_start()` handles the catch-up. The timer just needs to avoid scheduling in the past.

- [ ] **Step 1: Write failing test for exact-time boundary**

Add to `TestScheduleNextSync` in `tests/unit/test_tray_app.py`:

```python
def test_schedules_for_today_when_at_exact_time(self, app):
    """If current time equals configured sync time (same second), schedule for today, not tomorrow."""
    app._config = {"schedule": {"daily_sync_time": "11:00"}}

    # now = 11:00:00.500000, target after replace = 11:00:00.000000
    # With '<', target (11:00:00) is NOT < now (11:00:00.5), so stays today
    fake_now = datetime(2026, 3, 15, 11, 0, 0, 500000)
    with patch.object(app, "_reload_config"):
        with patch("tray_app.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            with patch("tray_app.threading.Timer") as MockTimer:
                mock_timer = MagicMock()
                MockTimer.return_value = mock_timer
                app._schedule_next_sync()

        delay_seconds = MockTimer.call_args[0][0]
        # Should be near 0 (same minute), NOT ~24h
        assert delay_seconds < 60, f"Expected <60s delay, got {delay_seconds:.0f}s (scheduled for tomorrow?)"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_tray_app.py::TestScheduleNextSync::test_schedules_for_today_when_at_exact_time -v`
Expected: FAIL — delay will be ~86400s (24h) because `<=` pushes to tomorrow.

- [ ] **Step 3: Fix the operator**

In `tray_app.py`, line 295, change:
```python
# Old
if target <= now:
# New
if target < now:
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_tray_app.py::TestScheduleNextSync -v`
Expected: All 4 tests PASS (including the new boundary test).

- [ ] **Step 5: Commit**

```bash
git add tray_app.py tests/unit/test_tray_app.py
git commit -m "fix: use < instead of <= in _schedule_next_sync to prevent next-day misfire (E003)"
```

---

## Bug 2: PTO Notification Fix

### Task 2: Make `_sync_pto_overhead()` return a result dict

**Files:**
- Modify: `tempo_automation.py:2648-2716` (`_sync_pto_overhead`)
- Modify: `tempo_automation.py:2838` (`sync_daily` — return the value)
- Test: `tests/unit/test_tempo_automation.py`

**Analysis:**

Currently `_sync_pto_overhead()` returns `None` (implicit). `sync_daily()` calls it and also returns `None` (bare `return` on line 2838-2839). The tray app's `_run_sync()` treats `result is None` as "skip silently."

Fix: make `_sync_pto_overhead()` return the same dict shape as normal sync (`hours_logged`, `target_hours`, `reason`), and propagate it from `sync_daily()`.

- [ ] **Step 1: Write failing test for return value**

Add to the appropriate test class in `tests/unit/test_tempo_automation.py`:

```python
def test_sync_pto_overhead_returns_result_dict(self, automation):
    """_sync_pto_overhead must return a dict with hours_logged, target_hours, reason."""
    automation.config["schedule"]["daily_hours"] = 8
    automation.config["overhead"] = {
        "current_pi": {"stories": [{"issue_key": "OVERHEAD-1", "summary": "PTO"}]},
        "pto_story_key": "OVERHEAD-1",
    }

    # Stub out API calls
    automation.jira_client.get_my_worklogs = MagicMock(return_value=[])
    automation.tempo_client.get_user_worklogs = MagicMock(return_value=[])
    automation.tempo_client.account_id = "test-id"
    automation._log_overhead_hours = MagicMock(return_value=[
        {"issue_key": "OVERHEAD-1", "issue_summary": "PTO", "time_spent_seconds": 28800}
    ])
    automation.notifier.send_daily_summary = MagicMock()

    result = automation._sync_pto_overhead("2026-04-03")

    assert result is not None
    assert result["hours_logged"] == 8.0
    assert result["target_hours"] == 8
    assert result["reason"] == "pto"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_tempo_automation.py::...<test_class>::test_sync_pto_overhead_returns_result_dict -v`
Expected: FAIL — `result is None` because method has no return statement.

- [ ] **Step 3: Add return statement to `_sync_pto_overhead()`**

In `tempo_automation.py`, at the end of `_sync_pto_overhead()` (after the logger.info line ~2716), add:

```python
        # Return result dict so tray app can show appropriate notification
        return {
            "hours_logged": total_hours,
            "target_hours": daily_hours,
            "reason": "pto",
        }
```

- [ ] **Step 4: Propagate return in `sync_daily()`**

In `tempo_automation.py`, line 2838, change:
```python
# Old (line 2838-2839)
                    self._sync_pto_overhead(target_date)
                    return
# New
                    return self._sync_pto_overhead(target_date)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/unit/test_tempo_automation.py -k "sync_pto_overhead" -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add tempo_automation.py tests/unit/test_tempo_automation.py
git commit -m "fix: _sync_pto_overhead returns result dict for tray notifications (E003)"
```

---

### Task 3: Add toast for `result is None` and PTO result in tray app

**Files:**
- Modify: `tray_app.py:514-518` (`_run_sync`)
- Test: `tests/unit/test_tray_app.py`

**Analysis:**

After Task 2, PTO days will return `{"hours_logged": 8.0, "target_hours": 8, "reason": "pto"}` — this already flows into the existing `result["hours_logged"] >= result["target_hours"]` branch (line 519), which shows "Sync Complete." That's acceptable behavior.

However, `result is None` still happens for:
- Weekends (bare `return` at line 2848)
- Non-working day with no overhead configured (bare `return` at line 2844)
- Health check failures (bare `return` at line 2856)

The user's complaint is that **manual sync should always show a notification**. Currently it's silent on skip. Fix: add a toast in the `result is None` branch.

- [ ] **Step 1: Write failing test for skip-day toast**

Add to the appropriate test class in `tests/unit/test_tray_app.py`:

```python
def test_run_sync_shows_toast_on_skip(self, app):
    """When sync_daily returns None (non-working day), a toast must still appear."""
    mock_automation = MagicMock()
    mock_automation.sync_daily.return_value = None
    app._automation = mock_automation

    with patch.object(app, "_show_toast") as mock_toast, \
         patch.object(app, "_set_icon_state"), \
         patch.object(app, "_start_sync_animation"), \
         patch("tray_app._monthly_log_file", return_value=Path("test.log")), \
         patch("builtins.open", mock_open()):
        app._run_sync()

    mock_toast.assert_called_once()
    title, body = mock_toast.call_args[0][:2]
    assert "skip" in title.lower() or "skip" in body.lower() or "no hours" in body.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_tray_app.py -k "test_run_sync_shows_toast_on_skip" -v`
Expected: FAIL — `_show_toast` is never called when `result is None`.

- [ ] **Step 3: Add toast to the `result is None` branch**

In `tray_app.py`, replace lines 514-518:

```python
# Old
            if result is None:
                # Non-working day / health check abort / early exit
                self._set_icon_state("green", "Tempo Automation")
                tray_logger.info("Sync skipped (non-working day or early exit)")
                sync_succeeded = True
# New
            if result is None:
                # Non-working day / health check abort / early exit
                self._set_icon_state("green", "Tempo Automation")
                self._show_toast(
                    "Sync Skipped",
                    "No hours logged -- today is not a working day.",
                )
                tray_logger.info("Sync skipped (non-working day or early exit)")
                sync_succeeded = True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_tray_app.py -k "test_run_sync_shows_toast_on_skip" -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -v --tb=short`
Expected: All existing + new tests pass.

- [ ] **Step 6: Commit**

```bash
git add tray_app.py tests/unit/test_tray_app.py
git commit -m "fix: show toast notification on sync skip so manual sync always gives feedback (E003)"
```

---

## Bug 3: Task Scheduler / Startup Not Honoring Config Time

### Task 4: Install.bat reads config time + tray reconciles on startup

**Files:**
- Modify: `install.bat:387-389`
- Modify: `tray_app.py` (add `_reconcile_task_scheduler()` call on startup)
- Test: `tests/unit/test_tray_app.py`

**Analysis:**

Three sub-issues discovered:

1. **`install.bat` hardcodes `18:00`** (line 389): `schtasks /Create ... /ST 18:00`. If the user already has a config with a different `daily_sync_time`, the Task Scheduler ignores it. The install script should read from config if it exists, else use the default `18:00`.

2. **Tray startup doesn't reconcile**: When the tray app starts (login, restart, or `--sync-on-start`), it reads config and sets its internal timer via `_schedule_next_sync()`. But it never checks if the Windows Task Scheduler task matches. If someone edited config.json directly (or ran `--setup`), the Task Scheduler stays stale until the user explicitly uses "Change Sync Time" from the tray menu.

3. **`_on_change_sync_time` already works correctly** (line 844-845): It calls both `_schedule_next_sync()` and `_update_task_scheduler_time()`. The fix is to reuse this pattern on startup.

- [ ] **Step 1: Fix `install.bat` to read config time**

In `install.bat`, replace lines 387-389:

```batch
REM Old:
REM Daily sync task (weekdays only at 6:00 PM, uses OK/Cancel dialog wrapper)
echo Creating daily sync task ^(Mon-Fri at 6:00 PM^)...
schtasks /Create /TN "TempoAutomation-DailySync" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 18:00 /TR "\"%SCRIPT_DIR%run_daily.bat\"" /F >nul 2>&1

REM New:
REM Read sync time from config if it exists, otherwise default to 18:00
set SYNC_TIME=18:00
if exist "%SCRIPT_DIR%config.json" (
    for /f "tokens=2 delims=:, " %%A in ('findstr /C:"daily_sync_time" "%SCRIPT_DIR%config.json"') do (
        set "RAW=%%~A"
    )
    if defined RAW set SYNC_TIME=!RAW!
)
echo Creating daily sync task ^(Mon-Fri at %SYNC_TIME%^)...
schtasks /Create /TN "TempoAutomation-DailySync" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST %SYNC_TIME% /TR "\"%SCRIPT_DIR%run_daily.bat\"" /F >nul 2>&1
```

- [ ] **Step 2: Add `_reconcile_task_scheduler()` to tray app**

Add a new method to `TempoTrayApp` (after `_update_task_scheduler_time`):

```python
def _reconcile_task_scheduler(self):
    """Ensure Windows Task Scheduler time matches config on startup.

    Covers the case where config was edited directly (e.g. --setup)
    but the Task Scheduler task still has the old/default time.
    """
    if sys.platform != "win32":
        return
    sync_time = self._get_sync_time()
    self._update_task_scheduler_time(sync_time)
    tray_logger.info(f"Task Scheduler reconciled to config sync time: {sync_time}")
```

- [ ] **Step 3: Call `_reconcile_task_scheduler()` on tray startup**

In the tray app's `run()` method (or the startup sequence where `_schedule_next_sync()` is first called), add a call to `_reconcile_task_scheduler()` right after `_schedule_next_sync()`:

```python
self._schedule_next_sync()
self._reconcile_task_scheduler()
```

- [ ] **Step 4: Write test for startup reconciliation**

Add to `tests/unit/test_tray_app.py`:

```python
def test_reconcile_task_scheduler_on_startup(self, app):
    """Tray startup should update Task Scheduler to match config sync time."""
    app._config = {"schedule": {"daily_sync_time": "11:00"}}

    with patch.object(app, "_update_task_scheduler_time") as mock_update:
        app._reconcile_task_scheduler()

    mock_update.assert_called_once_with("11:00")
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/unit/test_tray_app.py -k "reconcile" -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add install.bat tray_app.py tests/unit/test_tray_app.py
git commit -m "fix: install.bat reads config sync time + tray reconciles Task Scheduler on startup (E003)"
```

---

## Summary of Changes

| Bug | Root Cause | Fix | Lines Changed |
|-----|-----------|-----|---------------|
| Wrong sync time (boundary) | `<=` includes exact-second boundary, pushes to tomorrow | Change to `<` | `tray_app.py:295` (1 char) |
| No PTO notification | `_sync_pto_overhead` returns `None`, tray skips silently | Return result dict + add skip toast | `tempo_automation.py:2716,2838` + `tray_app.py:514-518` |
| Task Scheduler ignores config | `install.bat` hardcodes 18:00, tray doesn't reconcile on startup | Read config in install + reconcile on startup | `install.bat:389` + `tray_app.py` (new method + startup call) |

**Total: 4 tasks, ~20 steps, 4 commits**
