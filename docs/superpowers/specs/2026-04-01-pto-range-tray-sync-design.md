# PTO Range & Tray Sync — Design Spec

**Date:** 2026-04-01
**Branch:** feature/pto-range-and-tray-sync
**Status:** Approved

---

## Problem

1. PTO can only be added one day at a time (or comma-separated individual dates) — no range support.
2. After adding PTO from the tray, there is no way to immediately sync those days to Tempo. Sync only happens when the scheduled daily run fires on that date.

---

## Goals

1. Let users add PTO for a date range (start → end) from the tray app.
2. After adding PTO, offer to sync the new PTO days to Tempo immediately.
3. Only sync today and future dates (past dates are assumed already handled).

---

## Dialog Flow

```
"Add PTO" menu item clicked
    │
    ▼
MsgBox: "Add PTO for a date range?" [Yes] [No]
    │                    │
   Yes                   No
    │                    │
start date dialog    single date dialog
    │                    │
end date dialog          │
    │                    │
    └──────┬─────────────┘
           │
    expand_date_range() — skips weekends + org/country holidays
           │
    schedule_mgr.add_pto(dates)
           │
    filter: today + future dates only
           │
    (any to sync?)——No——► toast "PTO added. No future dates to sync."
           │
          Yes
           │
    MsgBox: "Sync N day(s) to Tempo now?" [Yes] [No]
           │              │
          Yes             No
           │              │
    background thread     toast "PTO added. Not synced to Tempo."
    sync_daily(date)
    for each future date
           │
    toast "Synced N day(s) to Tempo."
```

---

## Code Changes

### `tempo_automation.py` — `ScheduleManager`

**New method: `expand_date_range(start_date, end_date) -> list[str]`**

- Accepts two `YYYY-MM-DD` strings.
- Validates both dates (raises `ValueError` on bad format or start > end).
- Iterates every calendar day from start to end inclusive.
- Calls `is_working_day(d)` on each; includes only working days (skips weekends, org holidays, country holidays, extra holidays).
- Returns a list of `YYYY-MM-DD` strings.
- Used by tray flow and optionally by CLI in the future.

No changes to `add_pto()` itself — it already accepts a list of dates.

---

### `tray_app.py`

#### Replace `_on_add_pto`

New flow:

1. Call `_show_yesno_dialog("Add PTO for a date range?", "Tempo - Add PTO")`.
2. **If Yes (range):**
   - Call `_show_input_dialog` for start date.
   - If cancelled, return.
   - Call `_show_input_dialog` for end date.
   - If cancelled, return.
   - Call `schedule_mgr.expand_date_range(start, end)` to get working days.
3. **If No (single day):**
   - Call `_show_input_dialog` for a single date.
   - If cancelled, return.
   - `dates = [single_date]`
4. Call `schedule_mgr.add_pto(dates)`.
5. Show toast summarising added/skipped.
6. Filter `added` to today + future dates.
7. If none to sync → toast and return.
8. Call `_show_yesno_dialog(f"Sync {n} PTO day(s) to Tempo now?\n{date_list}", "Tempo - Sync PTO")`.
9. If Yes → `_sync_pto_dates_background(future_dates)`.
10. If No → toast "PTO added. Not synced to Tempo."

#### New method: `_show_yesno_dialog(msg, title) -> bool`

- Windows: `ctypes.windll.user32.MessageBoxW` with `MB_YESNO | MB_ICONQUESTION | MB_TOPMOST`. Returns `True` if user clicked Yes (IDYES = 6).
- Mac: `osascript` with `buttons {"No", "Yes"} default button "Yes"`. Returns `True` if "Yes" in output.
- Returns `False` on any error or timeout.

Note: `_show_confirm_dialog` already exists but is semantically tied to the exit flow ("Stay Running"/"Exit") and inverts the return value. A clean boolean yes/no helper avoids confusion.

#### New method: `_sync_pto_dates_background(dates: list[str])`

- Runs in a daemon thread (same pattern as `_on_sync_now`).
- For each date, calls `self._automation.sync_daily(date)`.
- On completion: `_show_toast("PTO Synced", f"Synced {n} day(s) to Tempo.")`.
- On error: `_show_toast("Sync Error", str(e))` and logs via `tray_logger`.
- Does not set `_sync_running` event — PTO sync is independent of the daily sync lock.

#### Remove `_process_pto_input`

- Only called from `_on_add_pto`, which is being replaced. Delete the method.
- Remove its 4 unit tests in `tests/unit/test_tray_app.py` (lines ~345-407) and replace with tests for the new flow.

---

## Sync Behaviour

- "Sync to Tempo" calls `sync_daily(date)` per day, which routes to `_sync_pto_overhead()`.
- Requires overhead story to be configured (`_is_overhead_configured()`). If not configured, `sync_daily` exits silently for that date. The tray should warn the user if overhead is not configured before offering to sync.
- Only today and future PTO dates are offered for sync. Past dates are out of scope.

---

## Error Handling

| Scenario | Behaviour |
|----------|-----------|
| Invalid date format entered | `add_pto()` skips with message; toast shows skipped reason |
| Start date after end date | `expand_date_range()` raises `ValueError`; toast shows error |
| Range expands to zero working days (all weekends/holidays) | Toast: "No working days in that range." |
| Overhead not configured | Before sync dialog, check `_is_overhead_configured()`; if not, show toast "Overhead story not configured. PTO added but cannot sync to Tempo." and skip sync offer |
| `sync_daily()` throws during background sync | Log error, show error toast per failed date, continue remaining dates |

---

## Out of Scope

- CLI range support (`--add-pto` keeps existing comma-sep behaviour).
- Removing existing PTO from tray (separate feature).
- Syncing past PTO dates.
- Mac-specific checkbox dialog (VBScript/AppleScript Yes/No is used on both platforms).
