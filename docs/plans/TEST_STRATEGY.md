# Automated Test Strategy — Tempo Timesheet Automation

**Version:** 1.0 | **Date:** February 22, 2026 | **Status:** COMPLETED -- 385 tests, all 4 phases done
**Scope:** `tempo_automation.py` (4,224 lines, 8 classes), `tray_app.py` (~1,306 lines), `confirm_and_run.py` (47 lines)

---

## 1. Executive Summary

The Tempo Timesheet Automation system has **zero test coverage** today. It is a production tool serving a 200-person engineering team, with 3 external API integrations (Jira, Tempo, Holidays), cross-platform OS operations (Windows + Mac), threading, encrypted config, and complex scheduling logic.

This strategy covers:
- **~160 unit tests** across 8 classes + tray app
- **~25 integration tests** for end-to-end flows
- **~15 edge case / regression tests**
- Shared fixtures and mock infrastructure
- CI-ready test runner configuration

**Estimated total: ~200 tests** organized into 11 test files.

---

## 2. Technology Stack

| Tool | Purpose |
|------|---------|
| **pytest** | Test runner, fixtures, parametrize, tmp_path |
| **pytest-mock** (mocker fixture) | Clean patching without nested `with` blocks |
| **pytest-cov** | Coverage reporting |
| **responses** or **requests-mock** | HTTP request mocking (Jira, Tempo, Holidays URLs) |
| **freezegun** | Freeze `datetime.now()` / `date.today()` for deterministic tests |
| **unittest.mock** | Patch OS-specific APIs (ctypes, fcntl, subprocess) |

### requirements-test.txt
```
pytest>=7.4.0
pytest-mock>=3.12.0
pytest-cov>=4.1.0
responses>=0.24.0
freezegun>=1.3.0
```

---

## 3. Test Directory Structure

```
tests/
├── conftest.py                      # Shared fixtures, mock factories, config builders
├── fixtures/                        # Static test data (JSON responses, configs)
│   ├── sample_config.json           # Valid developer config
│   ├── sample_config_po.json        # PO role config
│   ├── sample_config_sales.json     # Sales role config
│   ├── jira_myself.json             # GET /myself response
│   ├── jira_active_issues.json      # GET /search/jql (active issues)
│   ├── jira_worklogs.json           # GET /issue/{key}/worklog response
│   ├── jira_issue_details.json      # GET /issue/{key} (ADF format)
│   ├── jira_overhead_stories.json   # GET /search/jql (OVERHEAD project)
│   ├── tempo_worklogs.json          # GET /worklogs/user/{id} response
│   ├── tempo_periods.json           # GET /timesheet-approvals/periods
│   └── org_holidays.json            # Sample org holidays
│
├── unit/
│   ├── test_dual_writer.py          # DualWriter tests (5 tests)
│   ├── test_credential_manager.py   # CredentialManager tests (8 tests)
│   ├── test_config_manager.py       # ConfigManager tests (12 tests)
│   ├── test_schedule_manager.py     # ScheduleManager tests (35 tests)
│   ├── test_jira_client.py          # JiraClient tests (25 tests)
│   ├── test_tempo_client.py         # TempoClient tests (15 tests)
│   ├── test_notification_manager.py # NotificationManager tests (10 tests)
│   ├── test_tempo_automation.py     # TempoAutomation tests (40 tests)
│   └── test_tray_app.py            # TrayApp tests (20 tests)
│
├── integration/
│   ├── test_daily_sync_flow.py      # End-to-end daily sync (all roles)
│   ├── test_monthly_submit_flow.py  # Monthly gap detection + submission
│   ├── test_weekly_verify_flow.py   # Weekly verify + backfill
│   ├── test_overhead_flow.py        # Overhead selection + 5 cases
│   └── test_cli.py                  # CLI argument dispatch tests
│
└── edge/
    ├── test_cross_platform.py       # Win32 vs Darwin behavior
    ├── test_encoding.py             # ASCII enforcement, UTF-8 file I/O
    └── test_concurrency.py          # Threading, mutex, file locks
```

---

## 4. Shared Fixtures (conftest.py)

### 4.1 Config Builders

```python
import pytest
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

@pytest.fixture
def developer_config():
    """Valid developer role configuration."""
    return {
        "user": {"email": "dev@example.com", "name": "Test Developer", "role": "developer"},
        "jira": {
            "url": "test.atlassian.net",
            "email": "dev@example.com",
            "api_token": "jira-token-123"
        },
        "tempo": {"api_token": "tempo-token-456"},
        "schedule": {
            "daily_hours": 8.0,
            "daily_sync_time": "18:00",
            "monthly_submit_day": "last",
            "country_code": "US",
            "state": "",
            "pto_days": [],
            "extra_holidays": [],
            "working_days": []
        },
        "organization": {
            "default_issue_key": "DEFAULT-1",
            "holidays_url": "https://example.com/holidays.json"
        },
        "notifications": {
            "email_enabled": False,
            "smtp_server": "", "smtp_port": 587,
            "smtp_user": "", "smtp_password": "",
            "notification_email": "",
            "teams_webhook_url": "",
            "notify_on_shortfall": True
        },
        "overhead": {
            "current_pi": {
                "pi_identifier": "PI.26.1.JAN.30",
                "pi_end_date": "2026-01-30",
                "stories": [
                    {"issue_key": "OVERHEAD-10", "summary": "Scrum Ceremonies", "hours": 2}
                ],
                "distribution": "single"
            },
            "pto_story_key": "OVERHEAD-2",
            "planning_pi": {},
            "daily_overhead_hours": 2,
            "fallback_issue_key": "DEFAULT-1",
            "project_prefix": "OVERHEAD-"
        },
        "manual_activities": [],
        "options": {
            "auto_submit": True,
            "require_confirmation": False,
            "sync_on_startup": False
        }
    }

@pytest.fixture
def po_config(developer_config):
    """Product Owner role config (Tempo only, manual activities)."""
    config = developer_config.copy()
    config["user"]["role"] = "po"
    config["manual_activities"] = [
        {"activity": "Stakeholder Meetings", "hours": 3},
        {"activity": "Backlog Refinement", "hours": 2},
        {"activity": "Sprint Planning", "hours": 3}
    ]
    del config["jira"]  # PO has no Jira token
    return config

@pytest.fixture
def config_file(tmp_path, developer_config):
    """Write developer config to temp file, return Path."""
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(developer_config, indent=2), encoding="utf-8")
    return config_path
```

### 4.2 API Response Factories

```python
@pytest.fixture
def jira_active_issues():
    """Sample Jira active issues response."""
    return {
        "issues": [
            {
                "key": "PROJ-101",
                "fields": {
                    "summary": "Implement user authentication",
                    "status": {"name": "IN DEVELOPMENT"},
                    "assignee": {"accountId": "712020:test-uuid", "emailAddress": "dev@example.com"}
                }
            },
            {
                "key": "PROJ-102",
                "fields": {
                    "summary": "Add search functionality",
                    "status": {"name": "CODE REVIEW"},
                    "assignee": {"accountId": "712020:test-uuid", "emailAddress": "dev@example.com"}
                }
            }
        ]
    }

@pytest.fixture
def jira_worklogs_response():
    """Sample Jira worklogs for a single issue."""
    return {
        "worklogs": [
            {
                "id": "10001",
                "author": {"accountId": "712020:test-uuid", "emailAddress": "dev@example.com"},
                "timeSpentSeconds": 10800,
                "started": "2026-02-22T09:00:00.000+0000",
                "comment": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Worked on auth"}]}]}
            }
        ]
    }

@pytest.fixture
def tempo_worklogs_response():
    """Sample Tempo worklogs response."""
    return {
        "results": [
            {
                "tempoWorklogId": 5001,
                "issue": {"id": 10101},
                "timeSpentSeconds": 10800,
                "startDate": "2026-02-22",
                "description": "Worked on auth",
                "author": {"accountId": "712020:test-uuid"}
            }
        ]
    }
```

### 4.3 Mock Helpers

```python
@pytest.fixture
def mock_jira_api(responses):
    """Register standard Jira API responses."""
    base = "https://test.atlassian.net/rest/api/3"

    def register(method, path, json_body, status=200):
        getattr(responses, method.lower())(
            url=f"{base}{path}",
            json=json_body,
            status=status
        )

    return register

@pytest.fixture
def mock_tempo_api(responses):
    """Register standard Tempo API responses."""
    base = "https://api.tempo.io/4"

    def register(method, path, json_body, status=200):
        getattr(responses, method.lower())(
            url=f"{base}{path}",
            json=json_body,
            status=status
        )

    return register

@pytest.fixture
def no_file_io(monkeypatch, tmp_path):
    """Redirect all file writes to tmp_path."""
    original_open = open
    def patched_open(path, *args, **kwargs):
        p = Path(path)
        if p.suffix == '.json' and p.name != 'org_holidays.json':
            return original_open(tmp_path / p.name, *args, **kwargs)
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr("builtins.open", patched_open)
    return tmp_path
```

---

## 5. Unit Test Specifications

### 5.1 DualWriter (5 tests)

| # | Test | Input | Expected |
|---|------|-------|----------|
| 1 | Write to both streams | `writer.write("hello")` | Both console and file receive "hello" |
| 2 | Flush both streams | `writer.flush()` | Both `.flush()` called |
| 3 | Close logfile | `writer.close()` | File handle closed, console untouched |
| 4 | Handle None logfile | `DualWriter(console, None)` | Writes to console only, no crash |
| 5 | Encoding safety | `writer.write("ASCII only")` | No UnicodeEncodeError |

---

### 5.2 CredentialManager (8 tests)

| # | Test | Condition | Expected |
|---|------|-----------|----------|
| 1 | Encrypt on Windows | `sys.platform == 'win32'` | Returns `ENC:<base64>` string |
| 2 | Encrypt on non-Windows | `sys.platform == 'darwin'` | Returns plain text unchanged |
| 3 | Decrypt ENC: value (Windows) | Valid `ENC:...` input | Returns original plain text |
| 4 | Decrypt plain text | No `ENC:` prefix | Returns input unchanged (no-op) |
| 5 | Round-trip (Win) | encrypt then decrypt | Output == original input |
| 6 | Empty string | `encrypt("")` | Handles gracefully (no crash) |
| 7 | Special characters | `encrypt("p@$$w0rd!#%")` | Round-trip preserves specials |
| 8 | Invalid ENC: data | `decrypt("ENC:garbage")` | Raises or returns fallback gracefully |

**Mock:** `ctypes.windll.crypt32.CryptProtectData`, `CryptUnprotectData`

---

### 5.3 ConfigManager (12 tests)

| # | Test | Condition | Expected |
|---|------|-----------|----------|
| 1 | Load valid config | Well-formed config.json | Returns dict with all sections |
| 2 | Load with encrypted tokens | `ENC:...` values in jira/tempo | Tokens decrypted transparently |
| 3 | Load missing file | config.json doesn't exist | Raises FileNotFoundError |
| 4 | Load malformed JSON | Invalid JSON syntax | Raises json.JSONDecodeError |
| 5 | Save config | Valid dict | Writes JSON to file, readable back |
| 6 | Save encrypts tokens | `api_token` values | Written as `ENC:...` on Windows |
| 7 | get_account_id success | Jira /myself returns 200 | Returns accountId string |
| 8 | get_account_id 401 | Jira returns 401 | Raises or returns None with error log |
| 9 | get_account_id timeout | Network timeout | Handles gracefully |
| 10 | Config .get() safety | Missing nested keys | Returns defaults, never KeyError |
| 11 | Setup wizard (developer) | Mock input: email, tokens, role=developer | Config has jira + tempo sections |
| 12 | Setup wizard (PO) | Mock input: role=po | Config has manual_activities, no jira |

**Mock:** `builtins.input`, `requests.Session.get`, file I/O

---

### 5.4 ScheduleManager (35 tests) -- Highest Priority

This is the most logic-dense class. Tests organized by method.

#### is_working_day() -- 12 tests

| # | Test | Date | Config | Expected |
|---|------|------|--------|----------|
| 1 | Regular weekday | Mon 2026-02-23 | Empty PTO/holidays | `(True, "working")` |
| 2 | Saturday | Sat 2026-02-21 | — | `(False, "weekend")` |
| 3 | Sunday | Sun 2026-02-22 | — | `(False, "weekend")` |
| 4 | PTO day | Mon 2026-03-10 | pto_days: ["2026-03-10"] | `(False, "pto")` |
| 5 | Org holiday | Dec 25 | org_holidays: {"2026-12-25": "Christmas"} | `(False, "org_holiday")` |
| 6 | Country holiday | Jul 4 | country_code: "US" | `(False, "country_holiday")` |
| 7 | Extra holiday | Custom date | extra_holidays: [...] | `(False, "extra_holiday")` |
| 8 | Working day override (Sat) | Sat in working_days | working_days: ["2026-02-21"] | `(True, "working_day_override")` |
| 9 | **Priority: working_days > PTO** | Date in both | working_days + pto_days | `(True, ...)` (working wins) |
| 10 | **Priority: PTO > org_holiday** | Date in both | pto + org_holidays | `(False, "pto")` |
| 11 | **Priority: org > country** | Date in both | org + country | `(False, "org_holiday")` |
| 12 | Future date | 2027-01-01 | — | Works without error |

#### PTO Management -- 8 tests

| # | Test | Input | Expected |
|---|------|-------|----------|
| 13 | Add single PTO | "2026-03-10" | Added to pto_days, config saved |
| 14 | Add multiple PTO | "2026-03-10,2026-03-11" | Both added |
| 15 | Add duplicate PTO | Already exists | Skipped, reported as duplicate |
| 16 | Remove PTO | Existing date | Removed from pto_days |
| 17 | Remove non-existent PTO | Not in list | No error, reported as not found |
| 18 | Invalid date format | "03-10-2026" | Rejected with validation error |
| 19 | Past date PTO | Yesterday | Accepted (valid use case for backfill) |
| 20 | Weekend PTO | Saturday date | Accepted but effectively redundant |

#### Calendar and Counting -- 8 tests

| # | Test | Input | Expected |
|---|------|-------|----------|
| 21 | count_working_days (full week) | Mon-Fri | 5 |
| 22 | count_working_days (with holiday) | Week with 1 holiday | 4 |
| 23 | count_working_days (with PTO) | Week with 2 PTO | 3 |
| 24 | get_expected_hours | 5 working days, 8h/day | 40.0 |
| 25 | get_expected_hours (partial) | 3 working days | 24.0 |
| 26 | get_month_calendar (Feb 2026) | year=2026, month=2 | 28 entries with markers |
| 27 | get_holiday_name (known) | Christmas 2026 | "Christmas Day" or org name |
| 28 | get_holiday_name (not holiday) | Regular weekday | None |

#### Validation and Edge Cases -- 7 tests

| # | Test | Input | Expected |
|---|------|-------|----------|
| 29 | _validate_date valid | "2026-02-22" | True |
| 30 | _validate_date invalid | "2026-13-01" | False |
| 31 | _validate_date wrong format | "Feb 22, 2026" | False |
| 32 | Extra holidays CRUD | add + remove | List updated correctly |
| 33 | Working days CRUD | add + remove | List updated correctly |
| 34 | Year-end warning | < 100 days left | Warning string returned |
| 35 | Year-end no warning | > 100 days left | None |

---

### 5.5 JiraClient (25 tests)

#### Authentication -- 4 tests

| # | Test | Condition | Expected |
|---|------|-----------|----------|
| 1 | Auth header set | After __init__ | Basic auth header present |
| 2 | get_myself success | 200 response | Returns accountId |
| 3 | get_myself 401 | Invalid token | Raises/logs error |
| 4 | get_myself timeout | 30s timeout | Handles gracefully |

#### Worklogs CRUD -- 8 tests

| # | Test | Condition | Expected |
|---|------|-----------|----------|
| 5 | get_my_worklogs (has data) | Worklogs exist for date | Returns filtered list |
| 6 | get_my_worklogs (empty) | No worklogs | Returns [] |
| 7 | get_my_worklogs (author filter email) | Match by emailAddress | Only matching worklogs |
| 8 | get_my_worklogs (author filter accountId) | Match by accountId | Only matching worklogs |
| 9 | delete_worklog success | 204 response | Returns True |
| 10 | delete_worklog 404 | Worklog not found | Returns False |
| 11 | create_worklog success | 201 response | Returns True, ADF format sent |
| 12 | create_worklog multiline | 3-line comment | 3 ADF paragraphs in request body |

#### Issue Queries -- 6 tests

| # | Test | Condition | Expected |
|---|------|-----------|----------|
| 13 | get_my_active_issues (2 tickets) | IN DEVELOPMENT + CODE REVIEW | Returns both |
| 14 | get_my_active_issues (none) | No matching status | Returns [] |
| 15 | get_issues_in_status_on_date | Historical JQL | Correct WAS...ON query |
| 16 | get_issue_details (full) | Issue with description + comments | Extracted text from ADF |
| 17 | get_issue_details (minimal) | No description/comments | Graceful fallback |
| 18 | get_overhead_stories | OVERHEAD project, In Progress | Returns stories with PI ids |

#### ADF Parsing -- 7 tests

| # | Test | Input | Expected |
|---|------|-------|----------|
| 19 | Simple paragraph | `{type: "doc", content: [{type: "paragraph", content: [{type: "text", text: "Hello"}]}]}` | "Hello" |
| 20 | Nested content | Multiple nested levels | All text concatenated |
| 21 | Multiple paragraphs | 3 paragraphs | Text joined with newlines |
| 22 | Empty ADF | `{type: "doc", content: []}` | "" |
| 23 | None input | None | "" (no crash) |
| 24 | Mixed content types | text + code + mention | Text extracted, others skipped |
| 25 | Deeply nested | 5+ levels deep | All text found |

---

### 5.6 TempoClient (15 tests)

| # | Test | Condition | Expected |
|---|------|-----------|----------|
| 1 | Auth header | After __init__ | Bearer token in Authorization |
| 2 | get_user_worklogs success | 200 with results | Returns results array |
| 3 | get_user_worklogs empty | No worklogs | Returns [] |
| 4 | get_user_worklogs date filter | from/to params | Correct query params sent |
| 5 | create_worklog success | 200 response | Returns True |
| 6 | create_worklog fields | Valid input | Correct JSON body (issueKey, timeSpentSeconds, startDate, startTime, authorAccountId) |
| 7 | submit_timesheet success | 200 response | Returns True |
| 8 | submit_timesheet body | Period key | Correct JSON (worker.accountId, period.key) |
| 9 | _get_current_period found | Periods response with match | Returns correct period key |
| 10 | _get_current_period no match | No period for today | Returns formatted fallback |
| 11 | get_user_worklogs 401 | Invalid token | Error handled |
| 12 | submit_timesheet 403 | No permission | Error handled |
| 13 | create_worklog timeout | Network timeout | Error handled |
| 14 | get_user_worklogs pagination | Multiple pages | All results aggregated |
| 15 | account_id attribute | After __init__ | Correct accountId stored |

---

### 5.7 NotificationManager (10 tests)

| # | Test | Condition | Expected |
|---|------|-----------|----------|
| 1 | Email disabled | email_enabled: false | send_daily_summary is no-op |
| 2 | Email enabled | email_enabled: true | SMTP connection made |
| 3 | Email content | Valid worklogs | Subject + HTML body correct |
| 4 | Email SMTP error | Connection refused | Logs error, doesn't crash |
| 5 | Windows toast (Win) | sys.platform == 'win32' | winotify.Notification called |
| 6 | Windows toast (Mac) | sys.platform == 'darwin' | osascript called |
| 7 | Teams notification | Valid webhook URL | POST to webhook |
| 8 | Teams no URL | Empty webhook_url | No-op |
| 9 | Submission confirmation | Period string | Email with correct subject |
| 10 | Shortfall notification | Gap data | Notification sent with details |

**Mock:** `smtplib.SMTP`, `winotify.Notification`, `subprocess.run`

---

### 5.8 TempoAutomation (40 tests) -- Core Logic

#### Daily Sync -- 15 tests

| # | Test | Scenario | Expected |
|---|------|----------|----------|
| 1 | Sync on weekday | Regular Monday | Full sync flow executed |
| 2 | Sync on weekend | Saturday | Skipped (schedule guard) |
| 3 | Sync on PTO | PTO day + developer + overhead | _sync_pto_overhead() called |
| 4 | Sync on PTO (no overhead) | PTO day + no overhead config | Skipped |
| 5 | Sync on holiday | Org holiday + developer | _sync_pto_overhead() called |
| 6 | Developer 2 tickets | 2 active issues, 8h daily, 2h overhead | 3h each to tickets |
| 7 | Developer 0 tickets | No active issues | Fallback to overhead/default |
| 8 | Developer 1 ticket | 1 active issue | All remaining hours to it |
| 9 | PO role | role=po, 3 manual activities | Manual activities logged |
| 10 | Idempotent overwrite | Run sync twice same date | Delete + recreate, same result |
| 11 | Specific date | --date 2026-02-15 | Uses provided date, not today |
| 12 | Overhead Case 0 | daily_overhead_hours=2 | 2h overhead logged first |
| 13 | Overhead Case 2 | Existing manual overhead | Preserved, only remainder distributed |
| 14 | Planning week (Case 4) | Date in planning week | Uses upcoming PI stories |
| 15 | Existing overhead detection | Hybrid Jira+Tempo check | Both sources checked |

#### Hour Distribution -- 5 tests (parametrized)

| # | daily_hours | overhead | tickets | Expected distribution |
|---|-------------|----------|---------|----------------------|
| 16 | 8 | 2 | 2 | 3h, 3h (6h / 2 = 3 each) |
| 17 | 8 | 2 | 3 | 2h, 2h, 2h (6h / 3 = 2 each) |
| 18 | 8 | 2 | 4 | 1h, 1h, 1h, 3h (1 + remainder) |
| 19 | 8 | 0 | 2 | 4h, 4h |
| 20 | 8 | 2 | 1 | 6h (all remaining) |

#### Work Summary Generation -- 5 tests

| # | Test | Input | Expected |
|---|------|-------|----------|
| 21 | Full details | Description + 3 comments | 1-3 line summary |
| 22 | No description | Only summary | Uses summary as description |
| 23 | No comments | Description only | Description-based summary |
| 24 | Long description | 500+ chars | Truncated to reasonable length |
| 25 | ADF content | ADF JSON in description | Plain text extracted |

#### Monthly Submission -- 10 tests

| # | Test | Scenario | Expected |
|---|------|----------|----------|
| 26 | No gaps | All days at 8h | Submission proceeds |
| 27 | Gap detected | Day with 6h (2h gap) | Blocked, shortfall saved |
| 28 | Multiple gaps | 3 days short | All gaps in shortfall.json |
| 29 | Weekend skipped | Saturday in month | Not in gap analysis |
| 30 | Holiday skipped | Christmas in month | Not in gap analysis |
| 31 | Already submitted | monthly_submitted.json exists | Skipped with message |
| 32 | Fix shortfall | Interactive selection | Worklogs created for gap |
| 33 | View monthly | Current month | Per-day table printed |
| 34 | View monthly (specific) | "2026-01" | January data shown |
| 35 | Shortfall file format | Gap detected | Valid JSON with period, gaps dict |

#### Weekly Verify -- 5 tests

| # | Test | Scenario | Expected |
|---|------|----------|----------|
| 36 | Full week logged | All 5 days at 8h | No backfill needed |
| 37 | One day short | Wednesday at 6h | 2h backfill for Wednesday |
| 38 | Missing day | Thursday 0h | 8h backfill for Thursday |
| 39 | PTO day in week | Tuesday is PTO | Tuesday skipped |
| 40 | Partial week (holiday) | Monday is holiday | Monday skipped |

---

### 5.9 TrayApp (20 tests)

| # | Test | Method | Expected |
|---|------|--------|----------|
| 1 | Single instance (Win) | _check_single_instance | CreateMutexW called |
| 2 | Single instance (Mac) | _check_single_instance | fcntl.flock called |
| 3 | Already running | Mutex/lock held | Returns False |
| 4 | Deferred import | _load_automation | TempoAutomation imported |
| 5 | Import error | Missing config.json | _import_error set |
| 6 | Schedule timer | _schedule_next_sync | Timer set with correct delay |
| 7 | Timer fired | _on_timer_fired | Icon turns orange, toast shown |
| 8 | Sync now (spawn) | _on_sync_now | Background thread created |
| 9 | Sync success | _run_sync | Icon turns green |
| 10 | Sync failure | _run_sync raises | Icon turns red |
| 11 | Add PTO (valid) | _process_pto_input("2026-03-10") | schedule_mgr.add_pto called |
| 12 | Add PTO (invalid) | _process_pto_input("bad") | Toast with error |
| 13 | Input dialog (Win) | _show_input_dialog_win | VBScript created + executed |
| 14 | Input dialog (Mac) | _show_input_dialog_mac | osascript called |
| 15 | Open terminal (Win) | _open_in_terminal | cmd /k with CREATE_NEW_CONSOLE |
| 16 | Open terminal (Mac) | _open_in_terminal | osascript Terminal.app |
| 17 | Shortfall visible | _shortfall_visible | True when file exists |
| 18 | Submit visible | _submit_visible | True in last 7 days, no shortfall |
| 19 | Menu structure | _build_menu | Submenus: Configure, Log and Reports |
| 20 | Exit flow | _exit_flow | Timer stopped, mutex released |

**Mock:** `pystray.Icon`, `threading.Timer`, `subprocess.Popen`, `ctypes`, `fcntl`

---

## 6. Integration Test Specifications

### 6.1 Daily Sync Flow (5 tests)

```
Test: Full developer sync (happy path)
  Setup: Config (developer, 8h, 2h overhead), 2 active tickets, no existing worklogs
  Flow:  ConfigManager -> ScheduleManager.is_working_day(True) ->
         JiraClient.get_my_worklogs([]) -> JiraClient.get_my_active_issues([2]) ->
         JiraClient.create_worklog(overhead) -> JiraClient.create_worklog(ticket1) ->
         JiraClient.create_worklog(ticket2)
  Assert: 3 worklogs created (2h overhead + 3h ticket1 + 3h ticket2 = 8h)

Test: Developer sync with existing worklogs (idempotent overwrite)
  Setup: Same, but 2 existing worklogs for today
  Flow:  ... -> delete_worklog(existing1) -> delete_worklog(existing2) ->
         create_worklog(overhead) -> create_worklog(ticket1) -> create_worklog(ticket2)
  Assert: 2 deletes + 3 creates

Test: PO manual activities sync
  Setup: PO config with 3 manual activities (3h + 2h + 3h)
  Flow:  ... -> TempoClient.create_worklog(activity1) -> create_worklog(activity2) ->
         create_worklog(activity3)
  Assert: 3 Tempo worklogs totaling 8h

Test: PTO day with overhead
  Setup: Developer config, date in PTO, overhead.pto_story_key set
  Flow:  ... -> is_working_day(False, "pto") -> _sync_pto_overhead ->
         JiraClient.create_worklog(OVERHEAD-2, 8h)
  Assert: 1 worklog for full daily_hours to PTO story

Test: Schedule guard skips weekend
  Setup: Date is Saturday
  Flow:  ... -> is_working_day(False, "weekend") -> return
  Assert: Zero API calls made
```

### 6.2 Monthly Submit Flow (5 tests)

```
Test: Clean submission (no gaps)
  Setup: Full month of 8h/day worklogs
  Flow:  _detect_monthly_gaps({}) -> TempoClient.submit_timesheet()
  Assert: submitted.json written, shortfall.json not created

Test: Submission blocked by gaps
  Setup: 2 days with 6h instead of 8h
  Flow:  _detect_monthly_gaps({2 gaps}) -> save shortfall.json -> return
  Assert: submit NOT called, shortfall.json has 2 entries

Test: Fix shortfall then submit
  Setup: shortfall.json exists, user fixes gaps
  Flow:  fix_shortfall() -> create worklogs -> re-detect gaps({}) -> submit
  Assert: Gaps filled, then submission succeeds

Test: Already submitted month
  Setup: monthly_submitted.json exists for current period
  Flow:  _is_already_submitted(True) -> return
  Assert: No API calls

Test: View monthly hours report
  Setup: Mix of full and partial days
  Flow:  view_monthly_hours("2026-02") -> print table
  Assert: Correct per-day breakdown with totals
```

### 6.3 CLI Dispatch (10 tests)

| # | CLI Args | Expected Method Called |
|---|----------|-----------------------|
| 1 | (no args) | sync_daily(today) |
| 2 | `--date 2026-02-15` | sync_daily("2026-02-15") |
| 3 | `--submit` | submit_timesheet() |
| 4 | `--verify-week` | verify_week() |
| 5 | `--show-schedule` | print_month_calendar("current") |
| 6 | `--show-schedule 2026-03` | print_month_calendar("2026-03") |
| 7 | `--add-pto 2026-03-10,2026-03-11` | add_pto(["2026-03-10", "2026-03-11"]) |
| 8 | `--select-overhead` | select_overhead_stories() |
| 9 | `--view-monthly` | view_monthly_hours("current") |
| 10 | `--fix-shortfall` | fix_shortfall() |

---

## 7. Edge Case & Regression Tests

### 7.1 Cross-Platform (5 tests)

| # | Test | Condition | Expected |
|---|------|-----------|----------|
| 1 | DPAPI on non-Windows | sys.platform != 'win32' | Falls back to plain text |
| 2 | winotify on Mac | sys.platform == 'darwin' | Import skipped, osascript used |
| 3 | fcntl on Windows | sys.platform == 'win32' | Win32 mutex used instead |
| 4 | LaunchAgent on Windows | sys.platform == 'win32' | Registry used instead |
| 5 | BSD date vs GNU date | Mac install.sh | Correct date command syntax |

### 7.2 Encoding (5 tests)

| # | Test | Input | Expected |
|---|------|-------|----------|
| 1 | ASCII-only print | All print() calls | No Unicode characters in output |
| 2 | UTF-8 config | Non-ASCII in config values | Read/write without corruption |
| 3 | ADF with Unicode | Jira descriptions with emojis | Extracted text is valid |
| 4 | Log file encoding | UTF-8 content | Written as UTF-8 |
| 5 | stdout None (pythonw) | sys.stdout is None | Redirected to devnull |

### 7.3 Concurrency (5 tests)

| # | Test | Scenario | Expected |
|---|------|----------|----------|
| 1 | Tray sync during animation | Sync starts while icon animating | No deadlock |
| 2 | Rapid sync clicks | Multiple _on_sync_now calls | Only one sync runs |
| 3 | Timer + manual sync | Timer fires during manual sync | Queued or skipped |
| 4 | Config write during read | Concurrent config access | No corruption |
| 5 | Tray exit during sync | Exit while background sync active | Clean shutdown |

---

## 8. Mocking Strategy Summary

### What to Mock (Always)

| Dependency | Mock Target | Why |
|------------|------------|-----|
| Jira API | `responses` library | No live API calls in tests |
| Tempo API | `responses` library | No live API calls in tests |
| Holidays URL | `responses` library | No network dependency |
| SMTP | `smtplib.SMTP` | No email sent |
| winotify | `winotify.Notification` | Windows-only |
| ctypes (DPAPI) | `ctypes.windll` | Windows-only, OS-level |
| fcntl | `fcntl.flock` | Mac-only |
| subprocess | `subprocess.run/Popen` | No OS commands |
| pystray | `pystray.Icon/Menu` | No GUI |
| datetime.now/today | `freezegun` | Deterministic dates |
| builtins.input | `unittest.mock.patch` | No interactive input |
| File writes | `tmp_path` fixture | No side effects |

### What NOT to Mock

| Component | Why |
|-----------|-----|
| ScheduleManager logic | Pure calculation, test real code |
| Hour distribution math | Core business logic |
| ADF text extraction | Pure transformation |
| Date validation | Pure logic |
| Config dict building | Pure data |
| `holidays` library | Deterministic for given country+year |

---

## 9. Test Execution

### Run All Tests
```bash
pytest tests/ -v --tb=short
```

### Run by Category
```bash
pytest tests/unit/ -v                          # Unit tests only
pytest tests/integration/ -v                   # Integration tests only
pytest tests/edge/ -v                          # Edge cases only
```

### Run Single Class
```bash
pytest tests/unit/test_schedule_manager.py -v  # ScheduleManager only
pytest tests/unit/test_jira_client.py -v       # JiraClient only
```

### Coverage Report
```bash
pytest tests/ --cov=. --cov-report=term-missing --cov-report=html
```

### Watch Mode (with pytest-watch)
```bash
ptw tests/ -- -v --tb=short
```

---

## 10. Coverage Targets

| Component | Target | Rationale |
|-----------|--------|-----------|
| ScheduleManager | **95%** | Core scheduling logic, most bug-prone |
| JiraClient | **90%** | API integration, all endpoints covered |
| TempoClient | **90%** | API integration |
| TempoAutomation | **85%** | Complex orchestration, some interactive paths |
| ConfigManager | **85%** | File I/O + setup wizard |
| CredentialManager | **80%** | Platform-dependent encryption |
| NotificationManager | **75%** | Mostly dispatch logic |
| DualWriter | **100%** | Small, fully testable |
| TrayApp | **70%** | Heavy GUI/OS mocking, diminishing returns |
| **Overall** | **85%** | Practical target for first pass |

---

## 11. Test Priority / Implementation Order

Phase 1 is the foundation; each subsequent phase adds a layer.

### Phase 1: Foundation (Week 1)
1. `conftest.py` + fixtures + mock helpers
2. `test_schedule_manager.py` (35 tests) -- highest business value
3. `test_jira_client.py` (25 tests) -- most external coupling

### Phase 2: Core Logic (Week 2)
4. `test_tempo_client.py` (15 tests)
5. `test_tempo_automation.py` (40 tests) -- largest test file
6. `test_credential_manager.py` (8 tests)

### Phase 3: Supporting & Integration (Week 3)
7. `test_config_manager.py` (12 tests)
8. `test_notification_manager.py` (10 tests)
9. `test_dual_writer.py` (5 tests)
10. `test_daily_sync_flow.py` + `test_monthly_submit_flow.py`

### Phase 4: Tray & Edge Cases (Week 4)
11. `test_tray_app.py` (20 tests)
12. `test_cli.py` (10 tests)
13. `test_cross_platform.py` + `test_encoding.py` + `test_concurrency.py`

---

## 12. CI Integration (Future)

### pytest.ini / pyproject.toml
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
addopts = "-v --tb=short --strict-markers"
markers = [
    "slow: marks tests as slow (deselect with '-m \"not slow\"')",
    "windows: Windows-only tests",
    "darwin: macOS-only tests",
    "integration: integration tests requiring full mock setup",
]
```

### GitHub Actions (future)
```yaml
name: Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest, macos-latest]
        python-version: ["3.9", "3.12", "3.14"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - run: pip install -r requirements.txt -r requirements-test.txt
      - run: pytest tests/ -v --cov=. --cov-report=xml
      - uses: codecov/codecov-action@v4
```

---

## 13. Key Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Classes not designed for testability (tight coupling) | Mocking is complex | Extract interfaces, inject dependencies incrementally |
| Single 4,224-line file | Import side effects, slow test startup | Import specific classes, mock module-level globals |
| Interactive methods (input()) | Tests hang | Always mock builtins.input |
| OS-specific code paths | Tests pass on one OS, fail on another | Platform markers (`@pytest.mark.windows`) |
| Threading in tray app | Flaky tests | Use `threading.Event` for synchronization in tests |
| Date-dependent logic | Tests fail on different days | Always use `freezegun` |
| Config file mutations | Test pollution | Use `tmp_path` for all file I/O |
| Module-level logging setup | Logger shared across tests | Reset handlers in fixtures |

---

## Appendix: Parametrized Test Examples

### Hour Distribution (pytest.mark.parametrize)

```python
@pytest.mark.parametrize("daily,overhead,tickets,expected", [
    (8, 2, 2, [3, 3]),           # 6h / 2 = 3 each
    (8, 2, 3, [2, 2, 2]),       # 6h / 3 = 2 each
    (8, 2, 4, [1, 1, 1, 3]),    # integer div + remainder on last
    (8, 0, 2, [4, 4]),           # no overhead
    (8, 2, 1, [6]),              # single ticket gets all remaining
    (8, 8, 1, [0]),              # overhead uses all hours (edge)
    (4, 2, 3, [0, 0, 2]),       # very few remaining hours
])
def test_hour_distribution(daily, overhead, tickets, expected):
    ...
```

### is_working_day Priority (pytest.mark.parametrize)

```python
@pytest.mark.parametrize("date_str,pto,working,org_hol,expected_type", [
    ("2026-02-23", [],           [],           {},               "working"),
    ("2026-02-21", [],           [],           {},               "weekend"),
    ("2026-02-23", ["2026-02-23"], [],         {},               "pto"),
    ("2026-02-21", [],           ["2026-02-21"], {},             "working_day_override"),
    ("2026-02-23", ["2026-02-23"], ["2026-02-23"], {},          "working_day_override"),
    ("2026-12-25", [],           [],           {"2026-12-25": "Xmas"}, "org_holiday"),
])
def test_is_working_day_priority(date_str, pto, working, org_hol, expected_type):
    ...
```

---

## Quick Summary

- **~200 tests** across 14 test files, organized into unit / integration / edge categories
- **4-phase rollout:** Phase 1 (ScheduleManager + JiraClient), Phase 2 (TempoClient + TempoAutomation + CredentialManager), Phase 3 (ConfigManager + NotificationManager + DualWriter + integration flows), Phase 4 (TrayApp + CLI + cross-platform + encoding + concurrency)
- **Coverage target:** 85% overall, with ScheduleManager at 95% and DualWriter at 100%
- **Key dependencies:** pytest, pytest-mock, pytest-cov, responses, freezegun
- **Mocking philosophy:** Mock all external I/O (APIs, SMTP, OS calls, GUI) but never mock pure logic (schedule calculations, hour distribution, ADF parsing) -- test the real code
- Use `tmp_path` for all file I/O to prevent test pollution
- Use `freezegun` everywhere -- date-dependent logic is pervasive
- Platform markers (`@pytest.mark.windows`, `@pytest.mark.darwin`) for OS-specific tests
- Parametrized tests for hour distribution and schedule priority -- covers combinatorial cases efficiently

The document includes detailed test tables for every class method, mocking strategies, fixture designs, CI config, and coverage targets.

---

*End of test strategy. Estimated ~200 tests across 14 files, targeting 85% coverage.*
