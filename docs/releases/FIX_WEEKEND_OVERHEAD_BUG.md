# Fix: Weekend Overhead Logging Bug

**Date:** April 29, 2026
**Branch:** `fix/weekend-overhead-logging-bug`
**Status:** Fixed and tested (567 tests passing)
**Severity:** High -- caused incorrect Tempo entries on weekends

---

## Problem

Starting April 18-19, 2026, the system logged overhead hours on weekends
(Saturdays and Sundays) when it should have skipped them entirely.
Affected dates: April 18, 19, 25, 26.

## Root Cause

Two independent bugs combined:

### Bug 1: String mismatch in schedule guard (dormant since Feb 20)

`is_working_day()` returns `"Weekend (Saturday)"` or `"Weekend (Sunday)"`,
but the schedule guard in `sync_daily()` and `verify_week()` compared against
the bare string `"Weekend"`:

```python
# BUG: "Weekend (Saturday)" != "Weekend" evaluates to True
is_off_day = reason != "Weekend"
```

This made weekends pass the `is_off_day` check, entering the PTO/holiday
overhead logging branch instead of the weekend skip branch.

### Bug 2: Tray catchup backfill removed weekend protection (Apr 15)

Commit `2e2f1fe` changed `_catchup_backfill()` from:
```python
if stale_date >= today:   # accidentally protected weekends
    return
```
to:
```python
if stale_date == today:
    self._on_sync_now()   # now fires sync_daily() on weekends
    return
```

This started calling `sync_daily()` on weekends when the laptop woke from
sleep, triggering the dormant Bug 1.

### Bug 3: QA role excluded from overhead logging

The schedule guard checked `role == "developer"` while other overhead paths
used `role in ("developer", "qa")`, excluding QA from PTO/holiday overhead.

## Fixes Applied

### Fix 1: `tempo_automation.py` line 2988 -- sync_daily() schedule guard

```python
# BEFORE:
is_off_day = reason != "Weekend"
# AFTER:
is_off_day = not reason.startswith("Weekend")
```

### Fix 2: `tempo_automation.py` line 2989 -- role check

```python
# BEFORE:
if is_off_day and self.config.get("user", {}).get("role") == "developer":
# AFTER:
if is_off_day and self.config.get("user", {}).get("role") in ("developer", "qa"):
```

### Fix 3: `tempo_automation.py` line 5169 -- verify_week() schedule guard

```python
# BEFORE:
is_off_day = reason != "Weekend"
# AFTER:
is_off_day = not reason.startswith("Weekend")
```

### Fix 4: `tray_app.py` line 668 -- catchup backfill defense-in-depth

```python
# BEFORE:
if stale_date == today:
    tray_logger.info("Missed target was today -- running sync now")
    self._on_sync_now()
    return

# AFTER:
if stale_date == today:
    is_working, _ = self._automation.schedule_mgr.is_working_day(
        today.strftime("%Y-%m-%d")
    )
    if not is_working:
        tray_logger.info(
            "Missed target was today but today is non-working -- skipping"
        )
        return
    tray_logger.info("Missed target was today -- running sync now")
    self._on_sync_now()
    return
```

### Test fixes: 17 mock values corrected across 4 test files

All test mocks that returned `(False, "Weekend")` now return the realistic
`(False, "Weekend (Saturday)")` or `(False, f"Weekend (...)")` with
day-specific names matching the real `is_working_day()` output.

### New regression tests added (3)

1. `test_weekend_with_overhead_configured_does_not_log_overhead` --
   Saturday + developer + overhead configured -> skip, NOT overhead log
2. `test_weekend_sunday_with_overhead_configured_does_not_log_overhead` --
   Sunday variant
3. `test_qa_role_gets_overhead_on_pto` --
   QA role + PTO + overhead -> _sync_pto_overhead called

## Files Modified

| File | Changes |
|------|---------|
| `tempo_automation.py` | Fixed `startswith("Weekend")` check (2 locations), added QA to role check |
| `tray_app.py` | Added `is_working_day` guard in `_catchup_backfill` |
| `tests/unit/test_tempo_automation.py` | Fixed 12 mock values, added 3 regression tests |
| `tests/unit/test_error_scenarios.py` | Fixed 1 mock value |
| `tests/integration/test_daily_sync_flow.py` | Fixed 2 mock values |
| `tests/integration/test_monthly_submit_flow.py` | Fixed 2 mock values |

## Verification

- All 567 tests passing
- Covers: Windows + Mac, developer/QA/PO/sales roles, all country locales,
  tray and terminal invocations, sleep/wake drift scenarios
- The `startswith("Weekend")` pattern correctly handles any future
  parenthetical variant (Saturday, Sunday, or localized day names)
