# Tempo Timesheet Automation

**Vector Solutions Engineering | February 2026 | v3.9**

---

## The Problem

### $1.2M Wasted Annually on Manual Timesheets

200 employees spend **15-20 minutes every day** on manual timesheet entry.

| Metric | Current State |
|--------|--------------|
| Daily effort per person | 15-20 min manual entry |
| On-time submissions | 62% |
| Manager follow-up time | 2 hours/week |
| Late submissions | 38% |
| Annual cost of wasted time | **$1,215,260** |

### Cost Breakdown

| Role | Headcount | Time Lost/Month | Annual Cost |
|------|-----------|-----------------|-------------|
| Developers | 150 | 825 hrs | $693,000 |
| Product Owners | 30 | 220 hrs | $211,200 |
| Sales Team | 20 | 147 hrs | $114,660 |
| Manager Follow-ups | 15 | 130 hrs | $140,400 |
| **Total** | **215** | **1,322 hrs** | **$1,159,260** |

Plus hidden costs: payroll delays ($6K), compliance risk ($50K), employee frustration.

---

## The Solution

### Local Script Automation - Zero Cost, Zero Hosting

A Python script runs on each employee's computer. No servers. No subscriptions. No cloud dependency.

**How it works in 3 steps:**

```
1. READ       Script queries your active Jira tickets via REST API
               (IN DEVELOPMENT / CODE REVIEW status)

2. DISTRIBUTE  Splits your 8-hour day equally across tickets
               Generates smart descriptions from ticket content

3. SUBMIT      Creates Jira worklogs (Tempo auto-syncs)
               At month-end, submits timesheet for approval
```

**What employees see:**
- Daily toast: "8 hours logged across 3 tickets"
- End of month: "Timesheet submitted for approval"
- **No manual action required**

---

## Key Features

### Core Automation
- **Daily Auto-Sync** -- distributes hours across active Jira tickets at 6 PM
- **Weekly Verification** -- Friday check catches missed days, backfills from historical data
- **Monthly Submission** -- verifies total hours and submits (blocks on shortfalls)
- **Early Submission** -- submits mid-month when remaining days are all non-working
- **Smart Descriptions** -- meaningful worklog comments from ticket description + recent comments
- **Idempotent** -- safe to re-run; deletes previous entries then creates fresh

### Schedule Management
- **Holiday Detection** -- org holidays (auto-fetched) + national holidays (100+ countries)
- **PTO Management** -- add/remove PTO via CLI or tray app
- **Override System** -- extra holidays and compensatory working days
- **Calendar View** -- visual month calendar with working days, holidays, PTO

### Overhead Story Support
1. Default daily overhead (2h configurable)
2. No active tickets -- all hours to overhead
3. Manual overhead preserved
4. PTO and holidays -- log overhead hours
5. Planning week -- uses upcoming PI's overhead stories

### System Tray App (Windows + Mac)
- Persistent tray icon with color-coded status
- One-click sync, Add PTO dialog, toast notifications
- Configure submenu: Add PTO, Select Overhead, Change Sync Time
- Log and Reports submenu: Daily Log, Schedule, Monthly Hours, Fix Shortfall
- Smart exit with hours verification
- Auto-start on login

### Distribution & Installation
| Zip Type | Size | Python Required? | Use Case |
|----------|------|-----------------|----------|
| Windows Full | ~40-50MB | No (embedded Python 3.12) | Team members without Python |
| Windows Lite | ~200KB | Yes (system Python 3.7+) | Team members with Python |
| Mac | ~200KB | Yes (system python3) | macOS users |

---

## Who Benefits

### Developer
- **What happens:** Hours auto-distributed across active Jira tickets
- **Tokens needed:** Jira + Tempo
- **Time saved:** 15-20 min/day
- **Overhead:** 2h/day default, configurable

### Product Owner
- **What happens:** Hours logged via configured manual activities
- **Tokens needed:** Tempo only
- **Time saved:** 10-15 min/day

### Sales
- **What happens:** Pre-configured activities synced via Tempo API
- **Tokens needed:** Tempo only
- **Time saved:** 10-15 min/day

---

## Business Case

### ROI Summary

| Metric | Value |
|--------|-------|
| **Annual Savings** | **$1,215,260** |
| Total Investment (Year 1) | $8,000 |
| **ROI** | **15,091%** |
| Payback Period | Immediate |
| 5-Year Net Savings | $6,034,300 |

### Before vs After

| Metric | Before | After |
|--------|--------|-------|
| Daily effort per person | 15-20 min | < 2 min |
| On-time submissions | 62% | 98% |
| Manager follow-up/week | 2 hours | < 15 min |
| Employee frustration | 6.8/10 | 2.0/10 |
| Accuracy | Guesswork | Automatic from Jira |
| Infrastructure cost | N/A | $0 |

### Investment Required

```
Development:         $6,000  (one-time, already completed)
Hosting:                $0  (runs locally)
Licenses:               $0  (no subscriptions)
Maintenance:        $2,000  (annual)
────────────────────────────
TOTAL YEAR 1:       $8,000
```

---

## Development Timeline

21 days from idea to production. 10 releases in 3 weeks.

| Version | Date | Milestone |
|---------|------|-----------|
| v1.0 | Feb 3 | Initial release -- daily sync, Jira/Tempo integration |
| v2.0 | Feb 12 | Production hardening -- ASCII output, DualWriter, batch wrappers |
| v3.0 | Feb 17 | Schedule management -- holidays (100+ countries), PTO, weekly verify, monthly submit |
| v3.1 | Feb 18 | System tray app -- tray icon, animated sync, toast notifications, smart exit |
| v3.2 | Feb 19 | Installer polish -- install.bat rewrite, welcome toast, --stop flag |
| v3.3 | Feb 19 | Documentation -- .claude/rules, .claude/skills, doc reorganization |
| v3.4 | Feb 20 | Overhead stories -- 5 cases, PI support, hybrid Jira+Tempo detection |
| v3.5 | Feb 22 | Mac support -- tray app, install.sh, cron, osascript toasts/dialogs |
| v3.6 | Feb 22 | Monthly shortfall -- gap detection, --view-monthly, --fix-shortfall |
| v3.7 | Feb 22 | Tempo as source of truth -- 4 methods use Tempo API primary, Jira fallback |
| v3.8 | Feb 23 | Distribution zips -- build_dist.bat, 3 zip types, embedded Python 3.12 |
| v3.9 | Feb 23 | Early submission -- bypasses 7-day window when remaining days are non-working |

### Tech Stack
- Python 3.7+ (4,253 lines main script + 1,458 lines tray app)
- Jira REST API v3 (Basic auth)
- Tempo API v4 (Bearer token)
- 385 automated tests (pytest + responses + freezegun)
- 8 classes covering all functionality

---

## Screenshots

### Daily Sync Output

```
============================================================
TEMPO DAILY SYNC - 2026-02-20 (started 2026-02-20 18:00:05)
============================================================

Found 3 active ticket(s):
  - TS-101: Implement search feature
  - TS-102: Fix login validation
  - TS-103: Update API endpoint

8h / 3 tickets = 2.67h each

  [OK] Logged 2.67h on TS-101
    Description: Implement search feature for the dashboard...
  [OK] Logged 2.67h on TS-102
    Description: Fix login validation for edge cases...
  [OK] Logged 2.66h on TS-103
    Description: Update API endpoint response format...

============================================================
[OK] SYNC COMPLETE (18:00:12)
============================================================
Total entries: 3
Total hours: 8.00 / 8
Status: [OK] Complete
```

### Schedule Calendar

```
February 2026
================================================
Mon  Tue  Wed  Thu  Fri  | Sat  Sun
                               1
                               .
 2    3    4    5    6   |  7    8
 W    W    W    W    W   |  .    .
 9   10   11   12   13   | 14   15
 W    W    W    W    W   |  .    .
16   17   18   19   20   | 21   22
 H    W    W    W    W   |  .    .
23   24   25   26   27   | 28
 W    W    W    W    W   |  .

Legend: W=Working  H=Holiday  PTO=PTO  CW=Comp. Working  .=Weekend

Summary:
  Working days: 19  |  Expected hours: 152.0h
  Holidays: 1 (Presidents' Day - Feb 16)
```

### Monthly Hours Report

```
============================================================
MONTHLY HOURS REPORT - February 2026
============================================================

  Date         Day         Logged Expected     Status
  --------------------------------------------------
  2026-02-02   Monday        8.0h     8.0h       [OK]
  2026-02-03   Tuesday       8.0h     8.0h       [OK]
  2026-02-04   Wednesday     8.0h     8.0h       [OK]
  2026-02-05   Thursday      8.0h     8.0h       [OK]
  2026-02-06   Friday        8.0h     8.0h       [OK]
  ...
  --------------------------------------------------
  TOTAL                    120.0h   120.0h

  [OK] All hours accounted for
```

### Tray Menu Structure

```
+------------------------------+
|  Ajay Sajwan                 |
|  Next sync: 18:00            |
+------------------------------+
|  Sync Now                    |
+------------------------------+
|  Configure              >    |
|    +- Add PTO                |
|    +- Select Overhead        |
|    +- Change Sync Time       |
|  Log and Reports        >    |
|    +- Daily Log              |
|    +- Schedule               |
|    +- View Monthly Hours     |
|    +- Fix Monthly Shortfall  |
+------------------------------+
|  Submit Timesheet            |
|  Settings                    |
|  Exit                        |
+------------------------------+
```

---

## Architecture

### Class Diagram

```
                    +--------+
                    |  CLI   |
                    +--------+
                        |
                +---------------+
                |TempoAutomation|  (orchestration, ~2,100 lines)
                +---------------+
                   /    |    \
         +--------+ +------+ +---------------+
         | Jira   | |Tempo | | Schedule      |
         | Client | |Client| | Manager       |
         +--------+ +------+ +---------------+
              |         |            |
    +---------+---------+------------+---------+
    |         |         |                      |
+-------+ +-------+ +----------+ +------------+
|Config | |Cred   | |Notif.    | |DualWriter  |
|Manager| |Manager| |Manager   | |            |
+-------+ +-------+ +----------+ +------------+
```

### Data Flow
```
Jira (tickets) --> Python Script --> Jira (worklogs) --> Tempo (auto-sync)
                                 --> Tempo (submit timesheet)
```

### Key Technical Details
- **4,253 lines** main script, **1,458 lines** tray app
- **8 classes** covering all functionality
- **385 automated tests** (unit + integration)
- **Idempotent:** delete-then-create pattern (safe to re-run)
- **Tempo as source of truth:** `max(jira, tempo)` pattern for reliable hours
- **Smart descriptions:** Extracts from ADF (Atlassian Document Format) content

---

## Quick Start

### For Team Members

```bash
# 1. Download the appropriate zip
#    - Windows Full (no Python needed)
#    - Windows Lite (Python 3.7+ required)
#    - Mac (python3 required)

# 2. Extract and run installer
install.bat          # Windows
./install.sh         # Mac

# 3. Done! Tray app starts automatically.
#    Timesheets will be filled daily at 6 PM.
```

### Common Commands

```bash
python tempo_automation.py                          # Daily sync
python tempo_automation.py --date 2026-02-15        # Sync specific date
python tempo_automation.py --show-schedule           # View calendar
python tempo_automation.py --add-pto 2026-03-10     # Add PTO
python tempo_automation.py --view-monthly            # Monthly hours
python tempo_automation.py --submit                  # Submit timesheet
python tempo_automation.py --setup                   # Re-run setup
```

---

## Future Roadmap

| Enhancement | Status | Priority |
|-------------|--------|----------|
| PyInstaller .exe | Planned | High |
| Retry logic (exponential backoff) | Planned | Medium |
| Teams webhook notifications | Ready (code exists) | Medium |
| --dry-run mode | Planned | Low |
| Weighted time distribution | Planned | Low |
| Date range backfill (--from / --to) | Planned | Low |

---

## Contact

**Technical Lead:** Ajay Sajwan (Front End Team Lead, Vector Solutions)
**Slack:** #tempo-automation
**Repository:** [github.com/ajay-sajwan-vectorsolutions/timesheet-for-tempo](https://github.com/ajay-sajwan-vectorsolutions/timesheet-for-tempo)

---

*Built by Vector Solutions Engineering | February 2026*
