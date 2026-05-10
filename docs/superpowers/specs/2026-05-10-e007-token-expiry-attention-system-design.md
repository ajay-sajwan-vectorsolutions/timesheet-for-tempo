# E007 + Attention System — Design Spec

**Date:** 2026-05-10  
**Status:** Approved for implementation planning  
**Branch:** feature/fix-token-issue  
**Scope:** Token expiry handling + persistent attention indicator system

---

## Problem Statement

### E007 (Reported)
When a Jira or Tempo API token expires (HTTP 401), the tray app shows:

> "Sync Skipped — No hours logged, today is not a working day."

This is wrong and misleading. The user has no indication their token expired and no in-tray path to fix it — they must re-run the full `--setup` wizard.

### Expanded Scope (User-requested additions)
1. A persistent visual indicator (badge dot) on the tray icon to signal unresolved issues
2. A catalog of all detectable actionable events with consistent notification + fix paths
3. An "Attention" submenu in the tray menu that lists all active issues

---

## Root Cause Analysis

### Why the wrong message appears

`tempo_automation.py` — `_pre_sync_health_check()` (line 2886) returns `False` for all failures (401, timeout, 500, etc.) without encoding *which* failure occurred.

`tempo_automation.py` — `sync_daily()` (line 3006-3008): both a schedule skip (non-working day) and a health-check abort return bare `None`.

`tray_app.py` — `_run_sync()` (line 589-596): the `result is None` branch shows a single hardcoded message "today is not a working day" regardless of whether the actual cause was a schedule skip or a 401 abort.

### Why there's no token-update path
The tray has no dialog for token update. The only fix is `python tempo_automation.py --setup`, which re-asks all 10 setup questions for a one-field change.

---

## Approach Selected

**Structured return value + tray dialog + attention system**

- `sync_daily()` returns a dict `{"skip_reason": "token_expired_jira"}` (not bare `None`) when a 401 is detected — consistent with how it already returns `{"reason": "no_overhead" / "no_tickets" / "partial"}` for partial syncs
- A new in-tray dialog handles token update (validates against live API before saving)
- A new attention state manager tracks all unresolved issues and drives the badge dot + Attention submenu
- The badge is a PIL-composited circle on the existing 64×64 icon, independent of the background color state

---

## Part 1 — E007 Core Fix

### 1.1 `_pre_sync_health_check()` return type change

**File:** `tempo_automation.py` line 2886

Change return type from `bool` to `tuple[bool, Optional[str]]`:

```python
# Returns:
#   (True, None)          — both APIs healthy
#   (False, "jira_401")   — Jira token expired/invalid
#   (False, "tempo_401")  — Tempo token expired/invalid
#   (False, "jira_error") — Jira unreachable or 5xx
#   (False, "tempo_error")— Tempo unreachable or 5xx
def _pre_sync_health_check(self) -> tuple[bool, Optional[str]]:
```

All four call sites (`sync_daily`, `submit_timesheet`, `backfill_range`, `verify_week`) must unpack:
```python
ok, fail_reason = self._pre_sync_health_check()
if not ok:
    print(_color_prefix("[FAIL] Aborting due to API health check failure."))
    ...
```

### 1.2 `sync_daily()` — differentiated return for 401

```python
ok, fail_reason = self._pre_sync_health_check()
if not ok:
    print(_color_prefix("[FAIL] Aborting daily sync due to API health check failure."))
    if fail_reason in ("jira_401", "tempo_401"):
        return {"skip_reason": fail_reason}   # actionable — tray can prompt user
    return None                                # other errors — tray shows generic skip
```

`submit_timesheet`, `backfill_range`, and `verify_week` keep returning `None` for their health-check aborts (they are not tray-initiated the same way; this can be extended later).

### 1.3 `tray_app.py` — `_run_sync()` result handler

Split the current single `result is None` branch into three cases:

```python
if result is None:
    # Schedule skip: non-working day, holiday, PTO, etc.
    self._set_icon_state("green", "Tempo Automation")
    self._show_toast("Sync Skipped", "No hours logged -- today is not a working day.")
    tray_logger.info("Sync skipped (non-working day or override)")
    sync_succeeded = True

elif isinstance(result, dict) and result.get("skip_reason") in ("jira_401", "tempo_401"):
    which = "Jira" if result["skip_reason"] == "jira_401" else "Tempo"
    self._attention.add("token_expired_" + ("jira" if which == "Jira" else "tempo"))
    self._set_icon_state("red", f"Tempo - {which} token expired")
    self._show_toast(
        f"{which} Token Expired",
        f"Your {which} API token is invalid or expired.\n"
        f"Right-click tray icon > Configure > Update {which} Token."
    )
    tray_logger.warning(f"{which} token expired (401) -- sync aborted")
    sync_succeeded = False

elif isinstance(result, dict) and result.get("hours_logged", 0) >= result.get("target_hours", 0):
    ...  # existing success case
```

### 1.4 Token update dialogs

**Add to Configure submenu** (always visible — supports proactive rotation too):

```python
pystray.MenuItem("Update Jira Token", self._on_update_jira_token),
pystray.MenuItem("Update Tempo Token", self._on_update_tempo_token),
```

**Flow for `_on_update_jira_token()`** (same pattern as `_on_change_sync_time`):

```
1. _show_input_dialog(
       "Enter your new Jira API token\n"
       "(generate at id.atlassian.com/manage-profile/security/api-tokens):",
       "Tempo - Update Jira Token"
   )
2. Validate: GET /rest/api/3/myself with new token in throwaway requests.Session (timeout=10)
   - 200 → proceed
   - 401 → toast "Token is still invalid. Check it and try again." Return.
   - Exception → toast "Could not reach Jira: <reason>". Return.
3. Encrypt: CredentialManager.encrypt(new_token, key="jira_token")
4. Read config.json → config["jira"]["api_token"] = encrypted → write back
5. self._reload_config()
6. self._attention.remove("token_expired_jira")
7. toast: "Jira Token Updated — token saved and encrypted."
8. _show_yesno_dialog("Run a sync now with the new token?") → if Yes: _on_sync_now()
```

Same pattern for `_on_update_tempo_token()` hitting `GET /work-attributes`.

### 1.5 Token expiry ordering behaviour (by design, not a bug)

Health check short-circuits on Jira first. If both tokens are expired, the user sees Jira 401 → fixes → re-syncs → sees Tempo 401. This is one-prompt-at-a-time and acceptable.

### 1.6 Test changes required

**Must update:**
- `tests/unit/test_tempo_automation.py:1288-1351` — all `_pre_sync_health_check` unit tests assert `result is False`; must unpack tuple: `ok, reason = ta._pre_sync_health_check(); assert ok is False; assert reason == "jira_401"`
- Add tests: `test_health_check_returns_jira_401_reason`, `test_health_check_returns_tempo_401_reason`
- Add tray tests: `test_run_sync_shows_token_expired_toast`, `test_update_jira_token_validates_before_save`, `test_update_jira_token_rejects_invalid_token`

**Safe (no change needed):**
- Integration test mocks at lines 130, 214, 245, 278, 318 use `MagicMock(return_value=True)` — truthy still truthy; these keep working unchanged

---

## Part 2 — Attention System (Badge Dot + Attention Submenu)

### 2.1 Actionable Events Catalog

All events the system can detect, with severity and resolution action:

| ID | Event | Detection Method | Severity | Fix Action |
|----|-------|-----------------|----------|------------|
| `token_expired_jira` | Jira token expired (401) | Health check during sync | Critical | Update Jira Token (new dialog) |
| `token_expired_tempo` | Tempo token expired (401) | Health check during sync | Critical | Update Tempo Token (new dialog) |
| `sync_not_done_today` | Working day with 0h logged | Startup + post-sync Tempo query | High | Sync Now |
| `monthly_shortfall` | Month has hours gap | `monthly_shortfall.json` exists | High | Fix Shortfall (existing) |
| `timesheet_not_submitted` | End-of-month, not submitted | Last 7 days + no `monthly_submitted.json` | Medium | Submit Timesheet (existing) |
| `no_overhead_stories` | No overhead configured | Post-sync `reason="no_overhead"` | Medium | Select Overhead (existing) |
| `no_active_tickets` | No tickets in dev/review | Post-sync `reason="no_tickets"` | Medium | Inform user (Jira team action) |
| `config_invalid` | Config missing or schema error | Startup validation | Critical | Re-run Setup |

Events are ordered: Critical → High → Medium in the Attention submenu.

### 2.2 Notification Channels

| Channel | Trigger | What it shows |
|---------|---------|---------------|
| Badge dot (persistent) | Any unresolved Critical/High event | Red dot on icon corner |
| Toast | First time event is detected; re-fires after token update attempt | Event-specific message + fix hint |
| Tray tooltip (hover) | Always | "2 items need attention" or normal label |
| Attention submenu | Right-click → "Attention (2)" | List of all active events + one-click fix |

### 2.3 Badge Dot Implementation

**How:** Modify `_make_icon(color)` to accept `badge: bool = False`. When `badge=True`, composite a 14×14 red circle (with 1px white border for contrast) in the top-right corner of the 64×64 PIL image.

```python
def _make_icon(color: str = "green", badge: bool = False) -> "Image":
    ...
    if badge:
        dot_size = 14
        dot_x = size - dot_size - 1   # top-right corner
        dot_y = 1
        # White border for visibility on any background
        draw.ellipse([dot_x - 1, dot_y - 1, dot_x + dot_size, dot_y + dot_size],
                     fill=(255, 255, 255))
        draw.ellipse([dot_x, dot_y, dot_x + dot_size - 1, dot_y + dot_size - 1],
                     fill=(220, 38, 38))   # red
    return img
```

`_set_icon_state()` passes `badge=self._attention.has_active_events()` on every call.

### 2.4 AttentionManager class (new, in `tray_app.py`)

```python
class AttentionManager:
    """Tracks unresolved actionable events. Thread-safe."""
    
    def add(self, event_id: str): ...       # add an active issue
    def remove(self, event_id: str): ...    # clear resolved issue
    def clear(self): ...                    # clear all
    def has_active_events(self) -> bool: ...
    def count(self) -> int: ...
    def list_events(self) -> list[dict]: ... # returns [{id, label, severity, action_label}, ...]
```

Events persist for the lifetime of the tray process. On restart, re-detected via startup checks (shortfall file, submitted file, config validation).

### 2.5 Attention Submenu

In `_build_menu()`, add a dynamic menu item at the top (visible only when `AttentionManager.count() > 0`):

```python
pystray.MenuItem(
    lambda item: f"Attention ({self._attention.count()})" if self._attention.has_active_events() else "All Clear",
    pystray.Menu(*self._build_attention_items()),
    visible=lambda item: self._attention.has_active_events(),
)
```

Each attention item in `_build_attention_items()` shows `"[Critical] Jira token expired"` with a callback to the relevant fix action.

### 2.6 Startup checks (on tray launch)

On `TrayApp.__init__`, after config loads, run non-API checks synchronously:
- `monthly_shortfall.json` exists → add `monthly_shortfall`
- End-of-month + no `monthly_submitted.json` → add `timesheet_not_submitted`
- Config validation fails → add `config_invalid`

API-dependent checks (token validity, sync status) run in a background daemon thread 10s after startup to avoid blocking the tray icon from appearing.

---

## Implementation Order

1. E007 core (health check return type + sync_daily return + tray result handler) — standalone, ship first
2. Token update dialogs (Update Jira Token / Update Tempo Token) — pairs with E007
3. AttentionManager + badge dot — foundation for the full attention system
4. Attention submenu + startup checks — final layer

Steps 1+2 are self-contained and testable independently of 3+4.

---

## Out of Scope for This Spec

- CLI `--update-token` flag (E007 is tray-only; CLI users can re-run `--setup`)
- `winotify` action buttons on toasts (toast buttons that directly trigger "Update Token" — v2 enhancement)
- PTO absence detection (requires calendar integration, separate spec)
- Email/Teams notification channel for attention events (can be added later without design changes)
