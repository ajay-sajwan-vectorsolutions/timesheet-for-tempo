# Implementation Plan v3: Schedule Management, Holidays, PTO & Verification

**Status:** Pending Review — awaiting approval before implementation
**Created:** February 17, 2026
**Last Updated:** February 17, 2026 (added state-level holidays for India, GitHub central URL)
**Supersedes:** `WEEKLY_VERIFY_PLAN.md` (all features from that plan are included here plus new ones)

---

## Summary of Features

| # | Feature | Priority |
|---|---|---|
| 1 | Weekend guard in `sync_daily()` | Must-have |
| 2 | Org-level holidays (`org_holidays.json` + auto-fetch from URL) | Must-have |
| 3 | `holidays` library as safety net for national holidays | Must-have |
| 4 | Override system: `pto_days`, `extra_holidays`, `working_days` | Must-have |
| 5 | Schedule management CLI (`--add-pto`, `--add-holiday`, `--add-workday`, remove variants) | Must-have |
| 6 | Interactive schedule menu (`--manage`) | Must-have |
| 7 | Month calendar view (`--show-schedule`) | Must-have |
| 8 | Weekly hours verification (in `--verify-week`) | Must-have |
| 9 | Monthly hours verification (in `--submit`) | Must-have |
| 10 | MS Teams webhook notifications for shortfalls | Must-have |
| 11 | Year-end boundary handling | Must-have |
| 12 | Annual org holiday auto-refresh | Must-have |
| 13 | Updated setup wizard | Must-have |
| 14 | Historical JQL for weekly backfill | Must-have |
| 15 | Calendar fallback (Outlook/Graph API) | Deferred (Phase 2) |

**Calendar fallback (Azure AD + Outlook)** is deferred to Phase 2 — the user will set this up separately after the core features are working.

---

## Architecture Overview

### Day Classification Logic (Priority Order)

```
is_working_day(target_date):
    1. Is date in working_days?     --> YES --> WORK  (highest priority, overrides everything)
    2. Is date in pto_days?         --> YES --> SKIP
    3. Is date a weekend (Sat/Sun)? --> YES --> SKIP
    4. Is date in org_holidays?     --> YES --> SKIP  (from org_holidays.json)
    5. Is date in holidays library? --> YES --> SKIP  (national/state holidays)
    6. Is date in extra_holidays?   --> YES --> SKIP  (user's personal additions)
    7. Otherwise                    --> WORK
```

This logic is centralized in a single `is_working_day()` method so all features (daily sync, weekly verify, monthly verify, show-schedule) use the same source of truth.

### Org Holidays Architecture

```
                    +----------------------------------------------+
                    |  Central URL (GitHub public repo)             |
                    |  https://raw.githubusercontent.com/           |
                    |    {owner}/{repo}/main/org_holidays.json      |
                    +----------------------+-----------------------+
                                           |
                              auto-fetch on every run
                              (compare version string)
                                           |
        +----------------------------------+----------------------------------+
        v                                  v                                  v
   User A (US)                     User B (IN, MH/Pune)             User C (IN, TG/Hyderabad)
   gets: US common                 gets: IN common + MH state       gets: IN common + TG state
   + holidays lib (US)             + holidays lib (IN, MH)          + holidays lib (IN, TG)
   + personal overrides            + personal overrides              + personal overrides
```

- Admin maintains ONE central file on a **GitHub public repo** with holidays for all countries and states
- `org_holidays.json` has **common** holidays (apply to all in a country) + **state-specific** holidays
- Script auto-fetches on each run, compares `version` field
- If remote is newer, downloads and replaces local copy
- If fetch fails (no internet, URL down), uses cached local copy silently
- Local `org_holidays.json` ships with the script as initial seed
- **Locations map** in the file drives the city picker in setup wizard
- Adding a new office = admin adds city to `locations` + state holidays to the file

### State-Level Holiday Resolution (India)

An employee's effective holiday list is built by merging these layers:

| Layer | Source | Example (Pune employee) |
|---|---|---|
| 1. Org common | `org_holidays.json` > IN > common | Republic Day, Independence Day, Diwali |
| 2. Org state-specific | `org_holidays.json` > IN > MH | Shivaji Jayanti, Gudi Padwa, Maharashtra Day |
| 3. National/state holidays | `holidays` library (IN, state=MH) | Government gazetted holidays for Maharashtra |
| 4. Personal extra holidays | User's `extra_holidays` config | Any personal additions |

For US employees, only layer 1 (US common) + layer 3 (holidays library) + layer 4 apply (US state holidays are optional).

### Hours Verification Architecture

```
expected_hours = count_working_days(start, end) x daily_hours
actual_hours   = sum of all Jira worklogs in period
shortfall      = expected_hours - actual_hours

if shortfall > 0:
    --> print day-by-day breakdown
    --> send Teams webhook notification
    --> send email (if enabled)
```

---

## File Changes Summary

| File | Action | Description |
|---|---|---|
| `tempo_automation.py` | Modify | Weekend/holiday guard, ScheduleManager class, CLI commands, verification, Teams webhook |
| `org_holidays.json` | **New** | Central org holiday definitions for US and IN |
| `config.json` | Modify | Add schedule, notifications, organization fields |
| `config_template.json` | Modify | Add new config sections |
| `requirements.txt` | Modify | Add `holidays>=0.40` |
| `run_weekly.bat` | **New** | Task Scheduler wrapper for weekly verify |
| `examples/developer_config.json` | Modify | Add new schedule/notification fields |
| `examples/product_owner_config.json` | Modify | Same |
| `examples/sales_config.json` | Modify | Same |
| `SETUP_GUIDE.md` | Modify | New setup steps, schedule management section |
| `CLAUDE.md` | Modify | Updated architecture, config, CLI reference |
| `MEMORY.md` | Modify | Session decisions and status |
| `FUTURE_ENHANCEMENTS.md` | Modify | Mark holidays as in-progress |
| `WEEKLY_VERIFY_PLAN.md` | Modify | Mark as superseded |

---

## Implementation Steps (in order)

### Step 1: Create `org_holidays.json`

New file shipped with the script. Contains org-wide holidays per country per year, with **common** (all employees in that country) and **state-specific** sections. Also includes a **locations** map for the setup wizard city picker.

**Central URL:** Hosted on a GitHub public repo. The raw URL is configured in each user's `config.json` as `organization.holidays_url`. Example:
```
https://raw.githubusercontent.com/{owner}/{repo}/main/org_holidays.json
```

```json
{
    "version": "2026-v1",
    "updated": "2026-02-17",
    "updated_by": "Admin",
    "description": "Organization holiday calendar. Auto-fetched from central GitHub URL on each run.",
    "holidays": {
        "US": {
            "2026": {
                "common": [
                    {"date": "2026-01-01", "name": "New Year's Day"},
                    {"date": "2026-01-19", "name": "Martin Luther King Jr. Day"},
                    {"date": "2026-02-16", "name": "Presidents' Day"},
                    {"date": "2026-05-25", "name": "Memorial Day"},
                    {"date": "2026-07-03", "name": "Independence Day (Observed)"},
                    {"date": "2026-09-07", "name": "Labor Day"},
                    {"date": "2026-11-26", "name": "Thanksgiving Day"},
                    {"date": "2026-11-27", "name": "Day after Thanksgiving"},
                    {"date": "2026-12-25", "name": "Christmas Day"}
                ]
            }
        },
        "IN": {
            "2026": {
                "common": [
                    {"date": "2026-01-26", "name": "Republic Day"},
                    {"date": "2026-03-14", "name": "Holi"},
                    {"date": "2026-04-02", "name": "Ram Navami"},
                    {"date": "2026-04-14", "name": "Dr. Ambedkar Jayanti"},
                    {"date": "2026-08-15", "name": "Independence Day"},
                    {"date": "2026-10-02", "name": "Gandhi Jayanti"},
                    {"date": "2026-10-20", "name": "Diwali"},
                    {"date": "2026-10-21", "name": "Diwali (Day 2)"},
                    {"date": "2026-11-04", "name": "Guru Nanak Jayanti"},
                    {"date": "2026-12-25", "name": "Christmas Day"}
                ],
                "MH": [
                    {"date": "2026-02-19", "name": "Shivaji Jayanti"},
                    {"date": "2026-03-30", "name": "Gudi Padwa"},
                    {"date": "2026-05-01", "name": "Maharashtra Day"}
                ],
                "TG": [
                    {"date": "2026-06-02", "name": "Telangana Formation Day"},
                    {"date": "2026-10-06", "name": "Bathukamma"}
                ],
                "GJ": [
                    {"date": "2026-01-14", "name": "Uttarayan"},
                    {"date": "2026-05-01", "name": "Gujarat Day"}
                ]
            }
        }
    },
    "locations": {
        "Pune": {"country": "IN", "state": "MH"},
        "Hyderabad": {"country": "IN", "state": "TG"},
        "Gandhinagar": {"country": "IN", "state": "GJ"}
    }
}
```

**Notes:**
- The actual dates for 2026 Indian holidays (Holi, Diwali, etc.) are approximate and should be verified by the admin before production use
- Admin should update this file with the correct org-approved holiday list
- The version string format is `YYYY-vN` (e.g., `2026-v1`, `2026-v2` for mid-year updates)
- **common** holidays apply to ALL employees in that country
- **State codes** (MH, TG, GJ) contain holidays specific to that state's office
- An employee gets: common + their state holidays (merged, deduplicated)
- **locations** map drives the city picker in the setup wizard — admin adds new offices here
- Adding a new office (e.g., Bangalore): add `"Bangalore": {"country": "IN", "state": "KA"}` to locations + add `"KA": [...]` holidays under IN

### Adding a new office location (admin workflow):
1. Open `org_holidays.json` on the GitHub repo
2. Add city to `locations`: `"Bangalore": {"country": "IN", "state": "KA"}`
3. Add state holidays: `"KA": [{"date": "...", "name": "Kannada Rajyotsava"}, ...]` under IN > 2026
4. Bump `version` to `2026-v2`
5. Commit and push
6. All users auto-fetch on next run. Bangalore employees see the new city in `--setup`

---

### Step 2: Add `holidays` library to `requirements.txt`

```
# HTTP requests
requests>=2.31.0

# Holiday detection by country/state (US, India, 100+ countries)
holidays>=0.40
```

---

### Step 3: Add `ScheduleManager` class to `tempo_automation.py`

**Insert location:** After `ConfigManager` class, before `JiraClient` class (~line 278)

This new class centralizes all day-classification logic:

```python
class ScheduleManager:
    """Manages working days, holidays, PTO, and schedule overrides."""

    def __init__(self, config: Dict):
        self.config = config
        self.schedule = config.get('schedule', {})
        self.daily_hours = self.schedule.get('daily_hours', 8)
        self.country_code = self.schedule.get('country_code', 'US')
        self.state = self.schedule.get('state', '')
        self.pto_days = set(self.schedule.get('pto_days', []))
        self.extra_holidays = set(self.schedule.get('extra_holidays', []))
        self.working_days = set(self.schedule.get('working_days', []))
        self._org_holidays = {}      # loaded from org_holidays.json
        self._country_holidays = None  # from holidays library
        self._load_org_holidays()
        self._load_country_holidays()

    def _load_org_holidays(self):
        """Load org holidays from local org_holidays.json, auto-fetch if URL configured."""
        # 1. Try auto-fetch from GitHub URL (if configured)
        # 2. Fall back to local file
        # 3. Parse into self._org_holidays as {date_str: name}
        # 4. Merge: common holidays + state-specific holidays for user's state
        #    e.g., for IN/MH: merge IN>2026>common + IN>2026>MH

    def _parse_org_holidays(self):
        """Parse org_holidays_data into flat {date_str: name} dict for user's country+state."""
        # year = current year (and next year if December)
        # country_data = self._org_holidays_data['holidays'][country_code][year]
        # common = country_data.get('common', [])
        # state_specific = country_data.get(self.state, []) if self.state else []
        # self._org_holidays = {h['date']: h['name'] for h in common + state_specific}

    def _fetch_remote_org_holidays(self):
        """Fetch org_holidays.json from central GitHub URL if version is newer."""
        # Compare local version with remote version
        # If remote newer, download and save locally
        # If fetch fails, log warning and continue with local

    def get_locations(self) -> Dict:
        """Return locations map from org_holidays.json for setup wizard city picker."""
        # return self._org_holidays_data.get('locations', {})

    def _load_country_holidays(self):
        """Load country holidays from holidays library."""
        # import holidays
        # self._country_holidays = holidays.country_holidays(
        #     self.country_code, state=self.state if self.state else None
        # )

    def is_working_day(self, target_date: str) -> Tuple[bool, str]:
        """
        Determine if a date is a working day.
        Returns: (is_working: bool, reason: str)
        Reason examples: "Working day", "Weekend (Saturday)", "Holiday: Diwali",
                         "PTO", "Extra holiday", "Compensatory working day"
        """
        # Priority order:
        # 1. working_days override -> WORK
        # 2. pto_days -> SKIP
        # 3. weekend -> SKIP
        # 4. org_holidays -> SKIP
        # 5. country holidays (library) -> SKIP
        # 6. extra_holidays -> SKIP
        # 7. default -> WORK

    def get_holiday_name(self, target_date: str) -> Optional[str]:
        """Get the holiday name for a date, or None if not a holiday."""

    def count_working_days(self, start_date: str, end_date: str) -> int:
        """Count working days in a date range (inclusive)."""

    def get_expected_hours(self, start_date: str, end_date: str) -> float:
        """Calculate expected hours for a date range."""
        # count_working_days() * self.daily_hours

    def get_month_calendar(self, year: int, month: int) -> List[Dict]:
        """
        Generate calendar data for --show-schedule display.
        Returns list of {date, day_name, status, label} for each day in month.
        status: 'working', 'weekend', 'holiday', 'pto', 'extra_holiday', 'comp_working'
        """

    def check_year_end_warning(self) -> Optional[str]:
        """
        If current month is December, check if org_holidays.json has next year's data.
        Returns warning message or None.
        """

    # --- Config modification methods ---

    def add_pto(self, dates: List[str]) -> List[str]:
        """Add PTO dates to config. Returns list of actually added dates."""

    def remove_pto(self, dates: List[str]) -> List[str]:
        """Remove PTO dates from config. Returns list of actually removed dates."""

    def add_extra_holidays(self, dates: List[str]) -> List[str]:
        """Add extra holiday dates to config."""

    def remove_extra_holidays(self, dates: List[str]) -> List[str]:
        """Remove extra holiday dates from config."""

    def add_working_days(self, dates: List[str]) -> List[str]:
        """Add compensatory working day dates to config."""

    def remove_working_days(self, dates: List[str]) -> List[str]:
        """Remove compensatory working day dates from config."""

    def _save_schedule_to_config(self):
        """Persist schedule changes back to config.json."""
```

**Why a separate class?** This logic is needed by `sync_daily()`, `verify_week()`, `submit_timesheet()`, `--show-schedule`, and `--manage`. Putting it in one place avoids duplication and ensures consistent behavior.

---

### Step 4: Update `sync_daily()` with guards (~line 811)

Add at the top of `sync_daily()`, before any API calls:

```python
def sync_daily(self, target_date=None):
    if target_date is None:
        target_date = datetime.now().strftime('%Y-%m-%d')

    # --- Schedule guard ---
    is_working, reason = self.schedule_mgr.is_working_day(target_date)
    if not is_working:
        print(f"\n[SKIP] {target_date} is not a working day: {reason}")
        print("       Use --add-workday to override if this day should be worked.")
        logger.info(f"Skipped {target_date}: {reason}")
        return
    # --- End guard ---

    # ... existing sync logic continues ...
```

---

### Step 5: Add `TempoAutomation.__init__()` changes (~line 800)

```python
def __init__(self, config_path=None):
    # ... existing init ...
    self.schedule_mgr = ScheduleManager(self.config)
    # ... existing init continues (JiraClient, TempoClient, etc.) ...
```

---

### Step 6: Add weekly verification methods to `TempoAutomation`

**`verify_week()`** — main orchestration (after `submit_timesheet()`, ~line 1074):

```python
def verify_week(self):
    """Verify and backfill current week (Mon-Fri)."""
    # 1. Calculate Monday of current week
    # 2. Loop Mon-Fri:
    #    - Skip future dates
    #    - Check is_working_day() -> skip non-working days
    #    - Call _check_day_hours(date) for each working day
    # 3. Calculate totals
    # 4. Print weekly summary table
    # 5. If any shortfall: send notification (Teams + email)

def _check_day_hours(self, target_date: str) -> Dict:
    """
    Check if a day has sufficient hours logged.
    Returns: {
        date, status ('complete'|'shortfall'|'backfilled'),
        existing_hours, expected_hours, gap_hours,
        worklogs: [{issue_key, hours}]
    }
    """
    # 1. Fetch existing worklogs via JiraClient.get_my_worklogs()
    # 2. Sum existing hours
    # 3. Compare with daily_hours
    # 4. Return day status dict

def _backfill_day(self, target_date: str, gap_seconds: int, existing_keys: set) -> Dict:
    """
    Backfill a day with missing hours.
    Uses historical JQL to find stories, then calendar fallback, then OVERHEAD.
    Returns: {status, created_count, hours_added, method}
    """
    # (Same logic as WEEKLY_VERIFY_PLAN.md dedup algorithm)
```

---

### Step 7: Add monthly hours verification to `submit_timesheet()` (~line 1045)

Before the actual submission API call, add a verification step:

```python
def submit_timesheet(self):
    # ... existing last-day-of-month guard ...

    # --- Hours verification ---
    first_day = target_date.replace(day=1).strftime('%Y-%m-%d')
    last_day = target_date.strftime('%Y-%m-%d')
    expected = self.schedule_mgr.get_expected_hours(first_day, last_day)
    actual = self._get_month_actual_hours(first_day, last_day)
    shortfall = expected - actual

    print(f"\nMonthly Hours Check:")
    print(f"  Expected: {expected:.1f}h ({self.schedule_mgr.count_working_days(first_day, last_day)} working days x {self.schedule_mgr.daily_hours}h)")
    print(f"  Actual:   {actual:.1f}h")

    if shortfall > 0:
        print(f"  [!] SHORTFALL: {shortfall:.1f}h missing")
        self._send_shortfall_notification('monthly', first_day, last_day, expected, actual, details)
    else:
        print(f"  [OK] Hours complete")

    # ... existing submission logic continues ...
```

---

### Step 8: Add MS Teams webhook notification

Add to `NotificationManager` class (~line 695):

```python
def send_teams_notification(self, title: str, body: str, facts: List[Dict] = None):
    """
    Send notification to MS Teams via incoming webhook.
    Uses Adaptive Card format for rich display.
    Silently skips if webhook URL not configured.
    """
    webhook_url = self.config.get('notifications', {}).get('teams_webhook_url', '')
    if not webhook_url:
        logger.info("Teams webhook not configured, skipping notification")
        return

    # Build Adaptive Card payload
    card = {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard",
                "version": "1.4",
                "body": [
                    {"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium"},
                    {"type": "TextBlock", "text": body, "wrap": True}
                ]
            }
        }]
    }
    # Add facts table if provided
    # POST to webhook_url with timeout=30
```

**Shortfall notification method** in `TempoAutomation`:

```python
def _send_shortfall_notification(self, period_type, start, end, expected, actual, day_details):
    """Send shortfall notification via Teams and/or email."""
    shortfall = expected - actual
    title = f"Tempo Hours Shortfall - {period_type.title()}"
    body = f"Expected: {expected:.1f}h | Actual: {actual:.1f}h | Missing: {shortfall:.1f}h"

    # Build day-by-day breakdown
    # Send via Teams webhook
    # Send via email if enabled
```

---

### Step 9: CLI changes in `main()` (~lines 1082-1137)

**New arguments:**

```python
# Schedule management
parser.add_argument('--add-pto', nargs='+', metavar='DATE',
                    help='Add PTO date(s) (YYYY-MM-DD)')
parser.add_argument('--remove-pto', nargs='+', metavar='DATE',
                    help='Remove PTO date(s)')
parser.add_argument('--add-holiday', nargs='+', metavar='DATE',
                    help='Add extra holiday date(s)')
parser.add_argument('--remove-holiday', nargs='+', metavar='DATE',
                    help='Remove extra holiday date(s)')
parser.add_argument('--add-workday', nargs='+', metavar='DATE',
                    help='Add compensatory working day date(s)')
parser.add_argument('--remove-workday', nargs='+', metavar='DATE',
                    help='Remove compensatory working day date(s)')
parser.add_argument('--manage', action='store_true',
                    help='Interactive schedule management menu')
parser.add_argument('--show-schedule', nargs='?', const='current', metavar='YYYY-MM',
                    help='Show working/non-working days for a month (default: current month)')
parser.add_argument('--verify-week', action='store_true',
                    help='Verify and backfill current week (Mon-Fri)')
```

**Handler priority in main():**

```python
if args.setup:
    config_mgr.setup_wizard()
elif args.manage:
    schedule_mgr.interactive_menu()
elif args.show_schedule:
    schedule_mgr.print_month_calendar(args.show_schedule)
elif args.add_pto:
    schedule_mgr.add_pto(args.add_pto)
elif args.remove_pto:
    schedule_mgr.remove_pto(args.remove_pto)
elif args.add_holiday:
    schedule_mgr.add_extra_holidays(args.add_holiday)
elif args.remove_holiday:
    schedule_mgr.remove_extra_holidays(args.remove_holiday)
elif args.add_workday:
    schedule_mgr.add_working_days(args.add_workday)
elif args.remove_workday:
    schedule_mgr.remove_working_days(args.remove_workday)
elif args.verify_week:
    automation.verify_week()
elif args.submit:
    automation.submit_timesheet()
else:
    automation.sync_daily(target_date=args.date)
```

---

### Step 10: Interactive `--manage` menu

```python
def interactive_menu(self):
    """Interactive schedule management menu."""
    while True:
        print("\nSchedule Management")
        print("=" * 40)
        print("1. Add PTO day(s)")
        print("2. Remove PTO day")
        print("3. Add extra holiday")
        print("4. Remove extra holiday")
        print("5. Add compensatory working day")
        print("6. Remove compensatory working day")
        print("7. View month schedule")
        print("8. List all PTO days")
        print("9. List all extra holidays")
        print("10. List all compensatory working days")
        print("0. Exit")
        print()

        choice = input("Choice: ").strip()

        if choice == '0':
            break
        elif choice == '1':
            dates_str = input("Enter date(s) (YYYY-MM-DD, comma-separated): ").strip()
            dates = [d.strip() for d in dates_str.split(',')]
            added = self.add_pto(dates)
            print(f"[OK] Added {len(added)} PTO day(s): {', '.join(added)}")
        # ... similar for other choices ...
        elif choice == '7':
            month_str = input("Month (YYYY-MM, or Enter for current): ").strip()
            self.print_month_calendar(month_str or 'current')
```

---

### Step 11: `--show-schedule` calendar display

```python
def print_month_calendar(self, month_str: str):
    """Print month calendar with day classifications."""
    # Parse month_str -> year, month
    # Generate calendar grid
    # For each day: call is_working_day() to get status

    # Output format:
    # March 2026
    # ==============================
    # Mon  Tue  Wed  Thu  Fri  Sat  Sun
    #  2    3    4    5    6    7    8
    #  W    W    W    W    W    .    .
    #  9   10   11   12   13   14   15
    #  W   PTO  PTO  W    W    .    .
    # ...
    #
    # Legend: W=Working  H=Holiday  PTO=PTO  CW=Comp. Working  .=Weekend
    # Working days: 19  |  Expected hours: 152h
    # Holidays: 1 (Holi)  |  PTO: 2
```

---

### Step 12: Update setup wizard (`ConfigManager.setup_wizard()`)

Add these questions to the existing wizard flow (after daily_hours prompt):

```python
# --- Schedule settings ---
print("\n--- Schedule Settings ---")

# Load locations from org_holidays.json for city picker
locations = schedule_mgr.get_locations()

country = input("Country code for holidays (US, IN, etc.) [US]: ").strip().upper() or 'US'

# For countries with multiple offices, show city picker
country_cities = {city: info for city, info in locations.items()
                  if info.get('country') == country}

state = ''
if country_cities:
    print(f"\nSelect your office location:")
    city_list = sorted(country_cities.keys())
    for i, city in enumerate(city_list, 1):
        state_code = country_cities[city]['state']
        print(f"  {i}. {city} ({state_code})")
    print(f"  {len(city_list) + 1}. Other (enter state code manually)")

    choice = input(f"Choice [1-{len(city_list) + 1}]: ").strip()
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(city_list):
            city = city_list[idx]
            state = country_cities[city]['state']
            print(f"[OK] Set to {country} - {city} ({state})")
        else:
            state = input("Enter state/province code: ").strip().upper()
    except ValueError:
        state = input("Enter state/province code (optional): ").strip().upper()
else:
    state = input("State/province for regional holidays (optional, press Enter to skip): ").strip()

# Auto-fetch org holidays
holidays_url = self.config.get('organization', {}).get('holidays_url', '')
if holidays_url:
    print(f"Fetching org holidays from {holidays_url}...")
    # fetch and save
    # count common + state holidays
    print(f"[OK] Loaded {year} holidays ({common_count} common + {state_count} state for {country}/{state})")
else:
    print("[INFO] No org holidays URL configured. Using built-in org_holidays.json.")

# --- Notifications ---
teams_url = input("MS Teams webhook URL (optional, press Enter to skip): ").strip()
```

**Example setup wizard interaction (India employee):**
```
--- Schedule Settings ---
Country code for holidays (US, IN, etc.) [US]: IN

Select your office location:
  1. Gandhinagar (GJ)
  2. Hyderabad (TG)
  3. Pune (MH)
  4. Other (enter state code manually)
Choice [1-4]: 3

[OK] Set to IN - Pune (MH)
[OK] Loaded 2026 holidays (10 common + 3 state for IN/MH)

MS Teams webhook URL (optional, press Enter to skip):
```

**New config fields saved by wizard:**

```json
"schedule": {
    "daily_hours": 8,
    "daily_sync_time": "18:00",
    "monthly_submit_day": "last",
    "country_code": "IN",
    "state": "MH",
    "pto_days": [],
    "extra_holidays": [],
    "working_days": []
},
"organization": {
    "default_issue_key": "GENERAL-001",
    "holidays_url": "https://raw.githubusercontent.com/{owner}/{repo}/main/org_holidays.json"
},
"notifications": {
    "email_enabled": false,
    "teams_webhook_url": "",
    "notify_on_shortfall": true
}
```

---

### Step 13: Org holidays auto-fetch mechanism

**Central URL:** GitHub public repo raw URL, e.g.:
`https://raw.githubusercontent.com/{owner}/{repo}/main/org_holidays.json`

```python
def _fetch_remote_org_holidays(self):
    """
    Fetch org_holidays.json from central GitHub URL if version is newer.
    Called on every script run (fast — just a version check + conditional download).
    """
    holidays_url = self.config.get('organization', {}).get('holidays_url', '')
    if not holidays_url:
        return  # No URL configured, use local file only

    try:
        response = requests.get(holidays_url, timeout=10)
        response.raise_for_status()
        remote_data = response.json()
        remote_version = remote_data.get('version', '')

        local_version = self._org_holidays_data.get('version', '')
        if remote_version != local_version:
            # Save new version locally
            org_holidays_path = os.path.join(SCRIPT_DIR, 'org_holidays.json')
            with open(org_holidays_path, 'w') as f:
                json.dump(remote_data, f, indent=2)
            self._org_holidays_data = remote_data
            self._parse_org_holidays()  # Re-merge common + state holidays
            logger.info(f"Org holidays updated: {local_version} -> {remote_version}")
            print(f"[INFO] Org holidays updated to version {remote_version}")

            # Check if new locations were added
            new_locations = remote_data.get('locations', {})
            logger.info(f"Available office locations: {list(new_locations.keys())}")
        else:
            logger.debug(f"Org holidays up to date: {local_version}")
    except Exception as e:
        logger.warning(f"Could not fetch remote org holidays: {e}")
        # Continue with local cached copy — no crash
```

**What happens when admin pushes an update:**
1. Admin edits `org_holidays.json` on GitHub (e.g., adds election day holiday)
2. Bumps version to `2026-v2`, commits and pushes
3. Next time ANY user runs the script, it detects new version
4. Downloads updated file, replaces local copy
5. Re-parses common + state holidays for that user's location
6. User sees: `[INFO] Org holidays updated to version 2026-v2`

---

### Step 14: Year-end warning

In `ScheduleManager.__init__()`:

```python
def check_year_end_warning(self):
    """Warn if org_holidays.json doesn't have next year's data and it's December."""
    today = datetime.now()
    if today.month == 12:
        next_year = str(today.year + 1)
        country_data = self._org_holidays_data.get('holidays', {}).get(self.country_code, {})
        if next_year not in country_data:
            msg = (f"[!] WARNING: org_holidays.json does not contain {next_year} holidays "
                   f"for {self.country_code}.\n"
                   f"    Please ask your admin to update the central holiday file on GitHub.")
            print(msg)
            logger.warning(msg)
            return msg
        # Also check if state data exists for next year
        elif self.state:
            next_year_data = country_data.get(next_year, {})
            if self.state not in next_year_data and self.state != '':
                msg = (f"[!] WARNING: org_holidays.json has {next_year} common holidays "
                       f"but no state holidays for {self.state}.\n"
                       f"    Please ask your admin to add {self.state} holidays for {next_year}.")
                print(msg)
                logger.warning(msg)
                return msg
    return None
```

Called at the start of `sync_daily()`, `verify_week()`, and `submit_timesheet()`.

---

### Step 15: Historical JQL for weekly backfill

Add to `JiraClient` (after `get_my_active_issues()`, ~line 412):

```python
def get_issues_in_status_on_date(self, target_date: str) -> List[Dict]:
    """
    Fetch issues that were IN DEVELOPMENT or CODE REVIEW on a specific past date.
    Uses historical JQL: status WAS "X" ON "YYYY-MM-DD"
    """
    jql = (
        f'assignee = currentUser() AND ('
        f'status WAS "IN DEVELOPMENT" ON "{target_date}" OR '
        f'status WAS "CODE REVIEW" ON "{target_date}"'
        f')'
    )
    # Same API call pattern as get_my_active_issues()
    # Returns: [{issue_key, issue_summary}]
```

---

### Step 16: Create `run_weekly.bat`

```bat
@echo off
echo ============================================ >> "D:\working\AI-Tempo-automation\v2\daily-timesheet.log"
echo Run: %date% %time% (Weekly Verify) >> "D:\working\AI-Tempo-automation\v2\daily-timesheet.log"
echo ============================================ >> "D:\working\AI-Tempo-automation\v2\daily-timesheet.log"
"C:\Users\asajwan.DESKTOP-TN8HNF1\AppData\Local\Programs\Python\Python314\python.exe" "D:\working\AI-Tempo-automation\v2\tempo_automation.py" --verify-week --logfile "D:\working\AI-Tempo-automation\v2\daily-timesheet.log"
```

---

### Step 17: Update Task Scheduler

**Modify daily task to weekday-only:**
```cmd
schtasks /Delete /TN "TempoAutomation-DailySync" /F
schtasks /Create /TN "TempoAutomation-DailySync" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 18:00 /TR "D:\working\AI-Tempo-automation\v2\run_daily.bat" /F
```

**Add weekly verify task:**
```cmd
schtasks /Create /TN "TempoAutomation-WeeklyVerify" /SC WEEKLY /D FRI /ST 16:00 /TR "D:\working\AI-Tempo-automation\v2\run_weekly.bat" /F
```

---

## Updated Config Structure (complete)

```json
{
    "user": {
        "email": "ajay.sajwan-ctr@vectorsolutions.com",
        "name": "Ajay Sajwan",
        "role": "developer"
    },
    "jira": {
        "url": "lmsportal.atlassian.net",
        "email": "ajay.sajwan-ctr@vectorsolutions.com",
        "api_token": "***"
    },
    "tempo": {
        "api_token": "***"
    },
    "organization": {
        "default_issue_key": "GENERAL-001",
        "holidays_url": "https://raw.githubusercontent.com/{owner}/{repo}/main/org_holidays.json"
    },
    "schedule": {
        "daily_hours": 8,
        "daily_sync_time": "18:00",
        "monthly_submit_day": "last",
        "country_code": "IN",
        "state": "MH",
        "pto_days": [],
        "extra_holidays": [],
        "working_days": []
    },
    "notifications": {
        "email_enabled": false,
        "teams_webhook_url": "",
        "notify_on_shortfall": true,
        "smtp_server": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_user": "",
        "smtp_password": "",
        "notification_email": ""
    },
    "manual_activities": [],
    "options": {
        "auto_submit": true,
        "require_confirmation": false,
        "sync_on_startup": false
    }
}
```

**Config field notes:**
- `country_code`: `US` or `IN` (drives which section of org_holidays.json to use)
- `state`: State code (`MH` for Maharashtra/Pune, `TG` for Telangana/Hyderabad, `GJ` for Gujarat/Gandhinagar). Set during setup via city picker.
- `holidays_url`: GitHub raw URL to central org_holidays.json. Replace `{owner}/{repo}` with actual repo path.
- Employee gets: common holidays (all-India) + state holidays (MH only) + holidays library (IN, state=MH)

---

## Expected CLI Reference (after implementation)

```bash
# --- Core operations ---
python tempo_automation.py                        # Daily sync (today)
python tempo_automation.py --date 2026-02-15      # Daily sync (specific date)
python tempo_automation.py --submit               # Monthly submit (with hours check)
python tempo_automation.py --verify-week           # Weekly verify & backfill
python tempo_automation.py --setup                 # Initial setup wizard

# --- Schedule management (quick CLI) ---
python tempo_automation.py --add-pto 2026-03-10 2026-03-11 2026-03-12
python tempo_automation.py --remove-pto 2026-03-10
python tempo_automation.py --add-holiday 2026-04-14
python tempo_automation.py --remove-holiday 2026-04-14
python tempo_automation.py --add-workday 2026-11-08
python tempo_automation.py --remove-workday 2026-11-08

# --- Schedule viewing ---
python tempo_automation.py --show-schedule          # Current month
python tempo_automation.py --show-schedule 2026-03  # Specific month
python tempo_automation.py --manage                 # Interactive menu

# --- Logging ---
python tempo_automation.py --logfile daily-timesheet.log   # Dual output
```

---

## Expected Console Outputs

### Daily sync with skip:
```
[SKIP] 2026-02-16 is not a working day: Holiday: Presidents' Day
       Use --add-workday to override if this day should be worked.
```

### Weekly verification with shortfall:
```
============================================================
TEMPO WEEKLY VERIFICATION
Week of February 10, 2026
============================================================

--- Monday (2026-02-10) ---
  Existing: 8.00h (4 worklogs)
  [OK] Complete (8.00h / 8h)

--- Tuesday (2026-02-11) ---
  Existing: 0.00h
  [!] Gap: 8.00h needed
  Found 3 stories for 2026-02-11 (historical):
    - TS-36389: Implement search feature
    - TS-36344: Fix login validation
    - TS-36320: Update API endpoint
  [OK] Backfilled 2.67h on TS-36389
  [OK] Backfilled 2.67h on TS-36344
  [OK] Backfilled 2.66h on TS-36320

--- Wednesday (2026-02-12) ---
  Existing: 8.00h (4 worklogs)
  [OK] Complete (8.00h / 8h)

--- Thursday (2026-02-13) ---
  Existing: 8.00h (4 worklogs)
  [OK] Complete (8.00h / 8h)

--- Friday (2026-02-14) ---
  [SKIP] Weekend (Saturday)

============================================================
WEEKLY SUMMARY
============================================================
Day          Date         Status                  Existing    Added
------------------------------------------------------------
Monday       2026-02-10   [OK] Complete              8.00h    0.00h
Tuesday      2026-02-11   [+] Backfilled (stories)   0.00h    8.00h
Wednesday    2026-02-12   [OK] Complete              8.00h    0.00h
Thursday     2026-02-13   [OK] Complete              8.00h    0.00h
Friday       2026-02-14   [--] Weekend               0.00h    0.00h
------------------------------------------------------------
Working days: 4  |  Expected: 32.00h  |  Actual: 32.00h
Status: [OK] All hours accounted for
============================================================
```

### Monthly submit with shortfall notification:
```
Monthly Hours Check:
  Expected: 160.0h (20 working days x 8h)
  Actual:   152.0h
  [!] SHORTFALL: 8.0h missing

  Sending Teams notification... [OK]
```

### PTO management:
```
> python tempo_automation.py --add-pto 2026-03-10 2026-03-11 2026-03-12

[OK] Added 3 PTO day(s):
  - 2026-03-10 (Tuesday)
  - 2026-03-11 (Wednesday)
  - 2026-03-12 (Thursday)

Updated config.json saved.
```

### Show schedule:
```
> python tempo_automation.py --show-schedule 2026-03

March 2026
=========================================
Mon  Tue  Wed  Thu  Fri  | Sat  Sun
                          |
 2    3    4    5    6   |  7    8
 W    W    W    W    W   |  .    .
 9   10   11   12   13  | 14   15
 W   PTO  PTO  PTO  W   |  .    .
16   17   18   19   20  | 21   22
 W    W    W    W    W   |  .    .
23   24   25   26   27  | 28   29
 W    W    W    H    W   |  .    .
30   31
 W    W

Legend: W=Working  H=Holiday  PTO=PTO  CW=Comp. Working  .=Weekend

Summary:
  Working days: 19  |  Expected hours: 152.0h
  Holidays: 1 (Holi - Mar 26)
  PTO days: 3 (Mar 10-12)
  Compensatory working days: 0
```

---

## Error Handling

| Scenario | Behavior |
|---|---|
| `holidays` library not installed | ImportError caught, fallback to org_holidays.json only |
| org_holidays.json missing | Created from built-in defaults, warning logged |
| Remote URL fetch fails | Use cached local copy, log warning |
| Invalid date format in CLI | Print error with correct format example |
| PTO date already exists | Skip silently, report what was actually added |
| Year-end: next year missing | Print warning in December, continue with current data |
| Teams webhook fails | Log error, continue (don't block sync) |
| Email notification fails | Log error, continue (don't block sync) |

---

## Testing Plan

1. **Weekend guard:** Run `--date 2026-02-14` (Saturday) -- should skip
2. **Holiday guard:** Run `--date 2026-02-16` (Presidents' Day) -- should skip
3. **PTO guard:** Add PTO, run that date -- should skip
4. **Working day override:** Add a Saturday as working day, run -- should work
5. **Show schedule:** `--show-schedule 2026-02` -- verify layout
6. **Add/remove PTO:** Verify config.json updates correctly
7. **Weekly verify:** `--verify-week` -- check day-by-day output
8. **Monthly verify:** `--submit` in test mode -- check hours calculation
9. **Teams notification:** Send test message to webhook URL
10. **Org holidays fetch:** Test with a mock URL
11. **Year-end warning:** Set system to December, verify warning

---

## Dependencies

| Package | Version | Purpose | Required? |
|---|---|---|---|
| `requests` | >=2.31.0 | HTTP API calls | Yes (existing) |
| `holidays` | >=0.40 | Country/state holiday detection | Yes (new) |

---

## Estimated Code Changes

| Location | Lines added (approx) |
|---|---|
| `ScheduleManager` class (new) | ~250 lines |
| `sync_daily()` guard | ~10 lines |
| `verify_week()` + helpers | ~120 lines |
| Monthly verification in `submit_timesheet()` | ~30 lines |
| Teams webhook in `NotificationManager` | ~40 lines |
| CLI arguments + handlers | ~50 lines |
| `--manage` interactive menu | ~80 lines |
| `--show-schedule` display | ~50 lines |
| Setup wizard additions | ~20 lines |
| Historical JQL method | ~30 lines |
| **Total** | **~680 lines** |

Script will grow from ~1,137 to ~1,817 lines.

---

## Implementation Order

1. Create `org_holidays.json` (seed file)
2. Update `requirements.txt` (add holidays)
3. Add `ScheduleManager` class (core logic)
4. Update `TempoAutomation.__init__()` to use ScheduleManager
5. Add guards to `sync_daily()`
6. Add `--add-pto`, `--remove-pto` and other CLI commands
7. Add `--manage` interactive menu
8. Add `--show-schedule` display
9. Add `verify_week()` with historical JQL
10. Add monthly verification to `submit_timesheet()`
11. Add Teams webhook to `NotificationManager`
12. Add `_send_shortfall_notification()`
13. Update setup wizard
14. Add org holidays auto-fetch
15. Add year-end warning
16. Create `run_weekly.bat`
17. Update config templates and examples
18. Update Task Scheduler commands

---

*To implement: review this plan, approve, then follow steps 1-18 in order.*
