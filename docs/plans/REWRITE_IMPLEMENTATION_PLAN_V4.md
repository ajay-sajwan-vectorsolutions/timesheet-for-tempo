# Implementation Plan: Tempo Timesheet Automation v4.0 Rewrite

**Status:** Draft | **Date:** February 28, 2026
**Prerequisite:** PRD_V4.md (approved)

---

## 1. Problems with the Current Architecture

| Problem | Impact | Example |
|---------|--------|---------|
| **Single 4,200-line file** | Cannot navigate, cannot review PRs, cannot onboard | 8 classes + CLI in one file |
| **Tight coupling** | Changing one class risks breaking others | TempoAutomation directly constructs JiraClient, TempoClient, etc. |
| **No dependency injection** | Tests require `object.__new__()` hacks to bypass constructors | TempoAutomation.__init__ makes live API calls (fetches account_id) |
| **Mixed concerns** | Business logic interleaved with I/O, printing, and user interaction | `submit_timesheet()` does gap detection + printing + file I/O + API call + notification |
| **Duplicated logic** | Tray app re-implements schedule checks, submission eligibility | `_submit_visible()` duplicates `submit_timesheet()` window logic |
| **No config model** | Raw dict access with .get() everywhere, no validation | Easy to misspell keys, no IDE autocomplete, no type safety |
| **Hardcoded paths** | Module-level constants make testing difficult | `SCRIPT_DIR`, `CONFIG_FILE`, `SHORTFALL_FILE` at module level |
| **Cross-platform code scattered** | Windows/Mac branches sprinkled throughout | if/else blocks for registry vs LaunchAgent, VBScript vs AppleScript |
| **No error hierarchy** | Generic Exception catching hides real failures | Submit fails silently, tray shows "will happen later" |
| **Module-level side effects** | Importing the module configures logging, sets up stdout | Breaks test isolation |

---

## 2. Target Architecture

### 2.1 Package Structure

```
tempo/
├── __init__.py                    # Version string only
├── __main__.py                    # Entry point: python -m tempo
│
├── config/
│   ├── __init__.py
│   ├── model.py                   # Dataclass config model (AppConfig, UserConfig, etc.)
│   ├── manager.py                 # Load, save, migrate config.json
│   ├── credentials.py             # DPAPI encrypt/decrypt (CredentialManager)
│   └── setup_wizard.py            # Interactive first-time setup
│
├── schedule/
│   ├── __init__.py
│   ├── manager.py                 # ScheduleManager (is_working_day, count_working_days)
│   ├── holidays.py                # OrgHolidayLoader, CountryHolidayLoader
│   └── calendar_display.py        # Calendar formatting and display
│
├── clients/
│   ├── __init__.py
│   ├── jira.py                    # JiraClient (all Jira REST API calls)
│   ├── tempo.py                   # TempoClient (all Tempo REST API calls)
│   └── adf.py                     # ADF (Atlassian Document Format) parser
│
├── sync/
│   ├── __init__.py
│   ├── daily.py                   # DailySyncService (orchestrates daily sync)
│   ├── weekly.py                  # WeeklyVerifyService (verify + backfill)
│   ├── distribution.py            # Hour distribution algorithm
│   └── descriptions.py            # Smart description generator
│
├── overhead/
│   ├── __init__.py
│   ├── manager.py                 # OverheadManager (5 cases, PI detection)
│   ├── pi_calendar.py             # PI date parsing and planning week logic
│   └── selector.py                # Interactive overhead story selection
│
├── monthly/
│   ├── __init__.py
│   ├── submission.py              # MonthlySubmissionService
│   ├── gap_detection.py           # Gap detection and shortfall analysis
│   └── shortfall_fix.py           # Interactive shortfall fix
│
├── notifications/
│   ├── __init__.py
│   ├── manager.py                 # NotificationManager (dispatch to channels)
│   ├── email.py                   # SMTP email sender
│   ├── teams.py                   # Teams webhook sender
│   └── toast.py                   # Desktop toast (winotify / osascript)
│
├── platform/
│   ├── __init__.py
│   ├── base.py                    # PlatformAdapter abstract base
│   ├── windows.py                 # Windows: registry, mutex, VBScript, cmd /k
│   ├── macos.py                   # Mac: LaunchAgent, fcntl, AppleScript, Terminal
│   └── factory.py                 # get_platform() -> PlatformAdapter
│
├── logging.py                     # DualWriter, logging setup (no side effects)
├── errors.py                      # Error hierarchy (ApiError, ConfigError, etc.)
├── state.py                       # State file management (shortfall, submitted markers)
│
├── cli/
│   ├── __init__.py
│   ├── parser.py                  # argparse definition (18 arguments)
│   ├── commands.py                # Command dispatch (one function per command)
│   └── quiet.py                   # Quiet console handler suppression
│
└── tray/
    ├── __init__.py
    ├── app.py                     # TrayApp class (menu, state, callbacks)
    ├── icon.py                    # Icon generation and animation
    ├── dialogs.py                 # Input/confirm dialogs (delegates to platform/)
    └── menu.py                    # Menu builder with dynamic visibility
```

**Companion files (root level, unchanged):**
```
tempo_automation.py                # Thin wrapper: from tempo.cli import main; main()
tray_app.py                        # Thin wrapper: from tempo.tray import main; main()
confirm_and_run.py                 # Kept as-is (simple Task Scheduler entry point)
install.bat / install.sh           # Updated paths if needed
build_dist.bat                     # Updated to include tempo/ package
config_template.json               # Unchanged
org_holidays.json                  # Unchanged
```

### 2.2 Key Design Decisions

**D1: Dataclass Config Model**
```python
@dataclass
class ScheduleConfig:
    daily_hours: float = 8.0
    daily_sync_time: str = "18:00"
    country_code: str = "US"
    state: str = ""
    pto_days: list[str] = field(default_factory=list)
    extra_holidays: list[str] = field(default_factory=list)
    working_days: list[str] = field(default_factory=list)

@dataclass
class AppConfig:
    user: UserConfig
    jira: JiraConfig
    tempo: TempoConfig
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    notifications: NotificationConfig = field(default_factory=NotificationConfig)
    overhead: OverheadConfig = field(default_factory=OverheadConfig)
    # ...
```
- Replace all `.get('key', {}).get('nested', default)` with typed attribute access
- `from_dict(d: dict) -> AppConfig` for backward-compatible loading of existing config.json
- `to_dict() -> dict` for saving back to JSON
- Validation in `__post_init__` (email format, hour ranges, date formats)

**D2: Dependency Injection**
```python
class DailySyncService:
    def __init__(self, config: AppConfig, jira: JiraClient,
                 tempo: TempoClient, schedule: ScheduleManager,
                 overhead: OverheadManager, notifier: NotificationManager):
        ...
```
- No class constructs its own collaborators
- Factory function `create_app(config_path) -> AppContext` wires everything together
- Tests inject mocks directly -- no `object.__new__()` hacks needed

**D3: Error Hierarchy**
```python
class TempoAppError(Exception): ...
class ConfigError(TempoAppError): ...
class ApiError(TempoAppError): ...
class JiraApiError(ApiError): ...
class TempoApiError(ApiError): ...
class ScheduleError(TempoAppError): ...
class SubmissionError(TempoAppError): ...
```
- Specific exceptions instead of generic try/except returning False
- Callers can handle specific failures (e.g., tray app shows correct error toast)
- No more silent failures like the submit_timesheet bug

**D4: Platform Abstraction**
```python
class PlatformAdapter(ABC):
    @abstractmethod
    def show_input_dialog(self, title, prompt, default) -> Optional[str]: ...
    @abstractmethod
    def show_confirm_dialog(self, title, message) -> bool: ...
    @abstractmethod
    def show_toast(self, title, message, icon_path) -> None: ...
    @abstractmethod
    def open_in_terminal(self, command: list[str]) -> subprocess.Popen: ...
    @abstractmethod
    def open_file(self, path: str) -> None: ...
    @abstractmethod
    def register_autostart(self, name, command) -> None: ...
    @abstractmethod
    def unregister_autostart(self, name) -> None: ...
    @abstractmethod
    def acquire_single_instance(self, name) -> bool: ...
    @abstractmethod
    def schedule_restart(self, delay_minutes, command) -> None: ...
```
- WindowsPlatform and MacPlatform implement all methods
- `get_platform() -> PlatformAdapter` factory based on sys.platform
- All platform-specific code lives in one place instead of scattered if/else branches

**D5: Service Layer Pattern**
- Services like `DailySyncService.sync(date) -> SyncResult` do pure logic, return data
- CLI and tray layers handle display (print, toast, icon updates)
- Services don't print, don't show toasts, don't write files
- Services raise exceptions on failure (no silent `return False`)

---

## 3. Implementation Phases

### Phase 1: Foundation (config, errors, platform)
**Goal:** Infrastructure that everything else builds on.

| Task | New Files | Lines (est.) |
|------|-----------|-------------|
| Config dataclass model | tempo/config/model.py | ~200 |
| Config manager (load/save/migrate) | tempo/config/manager.py | ~150 |
| Credential manager (DPAPI) | tempo/config/credentials.py | ~120 |
| Error hierarchy | tempo/errors.py | ~40 |
| Platform abstraction + Windows impl | tempo/platform/ | ~350 |
| Mac platform impl | tempo/platform/macos.py | ~150 |
| Logging setup (DualWriter) | tempo/logging.py | ~60 |
| State file manager | tempo/state.py | ~80 |
| Package init + __main__ | tempo/__init__.py, __main__.py | ~20 |

**Tests:** Config model validation, config load/save round-trip, credential encrypt/decrypt,
platform factory.

**Verification:** `from tempo.config import AppConfig` works. Existing config.json loads
without errors. Encrypted tokens decrypt correctly.

---

### Phase 2: Schedule and Holidays
**Goal:** ScheduleManager with full is_working_day() chain.

| Task | New Files | Lines (est.) |
|------|-----------|-------------|
| Holiday loaders (org + country) | tempo/schedule/holidays.py | ~200 |
| ScheduleManager | tempo/schedule/manager.py | ~300 |
| Calendar display | tempo/schedule/calendar_display.py | ~120 |

**Tests:** Port all 86 existing ScheduleManager tests. Ensure identical behavior for
is_working_day priority chain, count_working_days, PTO/holiday CRUD.

**Verification:** `--show-schedule`, `--manage`, `--add-pto`, `--remove-pto` all work
identically to v3.9.

---

### Phase 3: API Clients
**Goal:** JiraClient and TempoClient as clean HTTP wrappers.

| Task | New Files | Lines (est.) |
|------|-----------|-------------|
| Jira client | tempo/clients/jira.py | ~350 |
| Tempo client | tempo/clients/tempo.py | ~180 |
| ADF parser | tempo/clients/adf.py | ~50 |

**Key changes from v3.9:**
- TempoClient.submit_timesheet uses `self.account_id` (not email) -- existing bug fixed
- All methods raise `JiraApiError`/`TempoApiError` instead of returning False
- No logger.info inside clients -- caller decides what to log
- Session creation extracted (easier to mock)

**Tests:** Port 53 JiraClient + 25 TempoClient tests. Add tests for error raising.

**Verification:** `python -c "from tempo.clients.jira import JiraClient"` -- no side effects.

---

### Phase 4: Daily Sync + Overhead
**Goal:** Core daily workflow.

| Task | New Files | Lines (est.) |
|------|-----------|-------------|
| Hour distribution algorithm | tempo/sync/distribution.py | ~60 |
| Smart description generator | tempo/sync/descriptions.py | ~80 |
| PI calendar logic | tempo/overhead/pi_calendar.py | ~100 |
| Overhead manager (5 cases) | tempo/overhead/manager.py | ~250 |
| Daily sync service | tempo/sync/daily.py | ~300 |
| Overhead selector (interactive) | tempo/overhead/selector.py | ~200 |

**Tests:** Port 51 TempoAutomation tests + write new tests for distribution edge cases.
Test all 5 overhead cases independently.

**Verification:** `python tempo_automation.py` daily sync works identically.
`--select-overhead` and `--show-overhead` work.

---

### Phase 5: Monthly + Weekly
**Goal:** Monthly submission, gap detection, weekly verify.

| Task | New Files | Lines (est.) |
|------|-----------|-------------|
| Gap detection | tempo/monthly/gap_detection.py | ~150 |
| Monthly submission service | tempo/monthly/submission.py | ~200 |
| Shortfall fix (interactive) | tempo/monthly/shortfall_fix.py | ~120 |
| Weekly verify service | tempo/sync/weekly.py | ~180 |

**Tests:** Port integration tests for monthly submit flow. Test early submission,
shortfall blocking, marker file behavior.

**Verification:** `--submit`, `--view-monthly`, `--fix-shortfall`, `--verify-week` all
work identically.

---

### Phase 6: Notifications
**Goal:** Multi-channel notification system.

| Task | New Files | Lines (est.) |
|------|-----------|-------------|
| Notification manager (dispatcher) | tempo/notifications/manager.py | ~80 |
| Email sender | tempo/notifications/email.py | ~100 |
| Teams sender | tempo/notifications/teams.py | ~60 |
| Desktop toast | tempo/notifications/toast.py | ~80 |

**Tests:** Port 36 NotificationManager/DualWriter tests.

**Verification:** Desktop toasts work on Windows. Email sends (if enabled).

---

### Phase 7: CLI
**Goal:** Full CLI with all 18 arguments.

| Task | New Files | Lines (est.) |
|------|-----------|-------------|
| Argument parser | tempo/cli/parser.py | ~100 |
| Command dispatch | tempo/cli/commands.py | ~300 |
| Quiet console mode | tempo/cli/quiet.py | ~30 |
| Setup wizard | tempo/config/setup_wizard.py | ~250 |

**Key change:** Each command is a function that creates only what it needs.
Schedule commands don't instantiate JiraClient/TempoClient.

**Tests:** Port 27 CLI dispatch tests.

**Verification:** Every CLI command works identically to v3.9. Thin wrapper
`tempo_automation.py` calls `from tempo.cli import main; main()`.

---

### Phase 8: Tray App
**Goal:** System tray with all menus and callbacks.

| Task | New Files | Lines (est.) |
|------|-----------|-------------|
| Tray app class | tempo/tray/app.py | ~350 |
| Icon generation + animation | tempo/tray/icon.py | ~80 |
| Dialog wrappers | tempo/tray/dialogs.py | ~40 |
| Menu builder | tempo/tray/menu.py | ~120 |

**Key changes from v3.9:**
- Delegates to PlatformAdapter for dialogs, toasts, terminal launch
- Uses service layer (DailySyncService, MonthlySubmissionService) instead of raw TempoAutomation
- Proper error handling: submit failure shows correct error toast
- No logic duplication with CLI

**Tests:** Port 37 TrayApp tests.

**Verification:** Tray app launches, all menu items work, sync/submit work from tray.
Dynamic menu items show/hide correctly.

---

### Phase 9: E2E Test Suite
**Goal:** True end-to-end tests that exercise full stack (config -> construction -> HTTP -> logic -> file I/O).

The existing v3.9 "integration" tests mock all collaborators and only verify call order.
They are effectively unit tests with larger scope. This phase adds real E2E tests.

**Key principle:** Mock HTTP at the network boundary (via `responses` library), but
construct all real objects. No `object.__new__()`, no mocking internal methods.

| Test File | Scenarios | What It Verifies |
|-----------|-----------|-----------------|
| test_e2e_daily_sync.py | E2E-001 to E2E-003 | Full developer sync, PTO+overhead, PO/Sales sync |
| test_e2e_monthly.py | E2E-004 to E2E-007 | Happy submission, gap blocking, early submission, shortfall fix |
| test_e2e_weekly.py | E2E-008 | Weekly verify with backfill |
| test_e2e_lifecycle.py | E2E-009 to E2E-010 | Marker file lifecycle, config change propagation |
| test_e2e_edge_cases.py | E2E-011 to E2E-015 | View monthly, overhead selection, max(jira,tempo), gap threshold, partial failure |
| test_e2e_tray.py | E2E-020 to E2E-025 | Tray sync, submit, smart exit, dynamic menu, confirm_and_run |
| test_e2e_platform.py | E2E-030 to E2E-031 | Windows and Mac platform adapter methods |

**Test infrastructure needed:**
- `e2e_helpers.py` -- factory that constructs full object graph with `responses`-mocked HTTP
- Reusable HTTP response builders for common Jira/Tempo API patterns
- tmp_path for config.json, shortfall/submitted markers, log files
- Fixture that registers all standard API stubs (GET /myself, GET /user, etc.)

**Estimated:** ~25-30 E2E test cases, ~800-1000 lines of test code.

**Coverage target:** E2E tests should cover the critical paths that unit tests miss:
- Full object construction and wiring
- Config -> ScheduleManager -> is_working_day chain with real holiday data
- Multi-step workflows (sync -> verify -> submit)
- File state management (shortfall/submitted markers)
- Error propagation from API layer to UI layer

---

### Phase 10: Wrappers, Distribution, and Cleanup
**Goal:** Final integration, wrapper scripts, distribution builds.

| Task | Details |
|------|---------|
| Thin wrappers | Update tempo_automation.py and tray_app.py root scripts |
| Installer updates | Update install.bat/install.sh if paths changed |
| build_dist.bat | Update to include tempo/ package in all zip types |
| confirm_and_run.py | Update imports |
| Coverage report | Run pytest --cov, verify 85%+ (unit + E2E combined) |
| Archive old monolith | Move to archive/tempo_automation_v3.py |

---

## 4. Migration Strategy

### 4.1 Backward Compatibility
- Existing `config.json` files load without any changes (AppConfig.from_dict handles all optional fields with defaults)
- Thin wrapper scripts (`tempo_automation.py`, `tray_app.py`) keep the same filenames and entry points
- CLI interface is identical (all 18 arguments, same help text, same output format)
- Task Scheduler tasks and cron jobs continue working without changes (same script paths)
- Log file names and locations unchanged
- State files (shortfall, submitted markers) same format and location

### 4.2 Cutover Plan
1. Build new `tempo/` package alongside existing monolith (both work simultaneously)
2. Phase by phase, move existing tests to import from new modules
3. When all 385 tests pass against new package, update the thin wrappers to import from `tempo/`
4. Move old monolith to `archive/tempo_automation_v3.py` (keep for reference)
5. Run installer on one machine to verify Task Scheduler integration
6. Build and test all 3 distribution zip types
7. Deploy to one team member for 1 week of parallel validation
8. Roll out to full team

### 4.3 Risk Mitigation
- **Risk:** Subtle behavior change breaks daily sync for 200 users
  - **Mitigation:** Run new and old side-by-side for 1 week with `--date` on past dates, compare output line-by-line.
- **Risk:** Config migration breaks existing setups
  - **Mitigation:** `AppConfig.from_dict()` tested against all 3 example configs + real production config.
- **Risk:** Tray app platform-specific regressions
  - **Mitigation:** Platform abstraction tested individually. Manual QA checklist for every tray feature.
- **Risk:** Embedded Python distribution breaks with new package structure
  - **Mitigation:** Build and test all 3 zip types in Phase 9 before any deployment.

---

## 5. File Size Estimates

| Module Group | Files | Total Lines (est.) |
|-------------|-------|-------------------|
| config/ | 4 | ~720 |
| schedule/ | 3 | ~620 |
| clients/ | 3 | ~580 |
| sync/ | 4 | ~620 |
| overhead/ | 3 | ~550 |
| monthly/ | 3 | ~470 |
| notifications/ | 4 | ~320 |
| platform/ | 4 | ~540 |
| cli/ | 3 | ~430 |
| tray/ | 4 | ~590 |
| Root modules | 4 | ~200 |
| **Total** | **39 files** | **~5,640 lines** |

Current: 2 files, ~5,700 lines. After rewrite: 39 files, ~5,640 lines.
Total code doesn't shrink (same features), but each file is under 400 lines with a
single, clear responsibility.

---

## 6. Testing Strategy

### 6.1 Test Organization
```
tests/
├── conftest.py                    # Shared fixtures, config builders
├── fixtures/                      # Sample JSON responses
├── unit/
│   ├── config/
│   │   ├── test_model.py          # Config dataclass validation
│   │   ├── test_manager.py        # Load/save/migrate
│   │   └── test_credentials.py    # DPAPI encrypt/decrypt
│   ├── schedule/
│   │   ├── test_manager.py        # is_working_day, count_working_days
│   │   ├── test_holidays.py       # Org + country holiday loading
│   │   └── test_calendar.py       # Calendar display
│   ├── clients/
│   │   ├── test_jira.py           # All JiraClient methods
│   │   ├── test_tempo.py          # All TempoClient methods
│   │   └── test_adf.py            # ADF parser
│   ├── sync/
│   │   ├── test_daily.py          # Daily sync orchestration
│   │   ├── test_distribution.py   # Hour distribution algorithm
│   │   ├── test_descriptions.py   # Smart descriptions
│   │   └── test_weekly.py         # Weekly verify + backfill
│   ├── overhead/
│   │   ├── test_manager.py        # 5 overhead cases
│   │   └── test_pi_calendar.py    # PI parsing, planning week
│   ├── monthly/
│   │   ├── test_submission.py     # Submission flow
│   │   ├── test_gap_detection.py  # Gap detection
│   │   └── test_shortfall_fix.py  # Interactive fix
│   ├── notifications/
│   │   ├── test_email.py
│   │   ├── test_teams.py
│   │   └── test_toast.py
│   ├── test_cli.py                # CLI dispatch
│   └── test_tray.py               # Tray app
├── integration/
│   ├── test_daily_sync_flow.py    # Orchestration-level daily sync (ported from v3.9)
│   └── test_monthly_submit_flow.py # Orchestration-level monthly submit (ported from v3.9)
└── e2e/
    ├── e2e_helpers.py             # Full object graph factory with responses-mocked HTTP
    ├── test_e2e_daily_sync.py     # Full-stack: config -> construct -> HTTP -> sync -> verify
    ├── test_e2e_monthly.py        # Full-stack: submission, gaps, early submit, shortfall fix
    ├── test_e2e_weekly.py         # Full-stack: weekly verify with backfill
    ├── test_e2e_lifecycle.py      # Marker file lifecycle, config change propagation
    ├── test_e2e_edge_cases.py     # max(jira,tempo), gap threshold, partial failure, view monthly
    ├── test_e2e_tray.py           # Tray sync, submit, smart exit, dynamic menu, confirm_and_run
    └── test_e2e_platform.py       # Windows and Mac platform adapter methods
```

### 6.2 Testing Principles
- **No `object.__new__()` hacks.** Dependency injection makes all classes directly testable.
- **No module-level side effects.** Importing a module doesn't configure logging or open files.
- **Mock at the boundary.** Mock HTTP responses (via `responses` library), not internal methods.
- **Config fixtures use dataclasses.** `developer_config()` returns `AppConfig(...)`, not a raw dict.
- **Freeze time with `freezegun`.** All date-dependent tests use `@freeze_time`.

---

## 7. Definition of Done

Each phase is complete when:
1. All new module files written with type hints and docstrings
2. All existing tests ported and passing
3. New tests added for previously untested edge cases
4. No file exceeds 500 lines
5. `pytest tests/ -v --tb=short` passes with 0 failures
6. `pytest tests/ --cov=tempo --cov-report=term-missing` shows 85%+ for new modules
7. Manual verification of affected CLI commands and tray features

Final phase (Phase 10) is complete when:
- All 385+ ported tests pass (unit + integration)
- All ~25-30 new E2E tests pass
- Total coverage >= 85% (unit + integration + E2E combined)
- All CLI commands work identically to v3.9
- Tray app works on Windows (Mac: best effort without hardware)
- E2E tests verify: full daily sync, monthly submission, shortfall fix, weekly verify,
  marker file lifecycle, tray sync/submit, config propagation, error handling
- All 3 distribution zips build successfully
- Old monolith archived
