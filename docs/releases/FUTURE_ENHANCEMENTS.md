# Tempo Automation - Future Enhancements

**Created:** February 12, 2026

---

## 1. Packaging & Distribution (Priority: High)

### Option A: PyInstaller .exe (Recommended first step)
- **Status:** NOT YET IMPLEMENTED -- still a future enhancement
- **Effort:** ~30 minutes, zero code changes
- **What:** Bundle Python + dependencies into a single `.exe`
- **User experience:** Double-click to run, no Python install needed
- **Limitation:** Windows-only (separate build for Mac), still needs Task Scheduler
- **Command:** `pyinstaller --onefile tempo_automation.py`
- **Notes:**
  - config.json would live next to the .exe
  - Need to handle SCRIPT_DIR path resolution for bundled mode
  - Can use `--icon` flag for custom icon
- **Workaround (v3.8):** Windows Full distribution zip includes embedded Python 3.12 -- no system Python install needed

### Option B: System Tray App with built-in scheduler -- IMPLEMENTED (v3.1)
- **Implemented in:** v3.1 (Feb 18, 2026), enhanced through v3.9
- **What was built:** Full system tray app (`tray_app.py`, ~1,458 lines) with:
  - pystray-based tray icon with company favicon
  - Color-coded status (green=idle, orange=pending, animated orange/red=syncing, red=error)
  - Right-click menu: Sync Now, Configure submenu, Log and Reports submenu, Submit Timesheet, Settings, Exit
  - Auto-starts with Windows (registry) and Mac (LaunchAgent)
  - Built-in scheduler with configurable sync time
  - Toast notifications (winotify on Windows, osascript on Mac)
  - Smart exit with hours verification
  - Dynamic menu items (Submit Timesheet, Fix Shortfall) with auto-refresh

### Option C: Chrome Extension
- **Effort:** 1-2 weeks (full rewrite)
- **What:** Rewrite entire script in JavaScript as a Chrome extension
- **User experience:** Install from Chrome Web Store, works on any OS
- **Architecture:**
  - Manifest V3 service worker for background scheduling
  - Chrome alarms API for daily triggers
  - Popup UI for config and status
  - Direct API calls to Jira/Tempo from extension
- **Challenges:**
  - Full rewrite from Python to JavaScript
  - CORS restrictions with Jira API (may need proxy or declarativeNetRequest)
  - Chrome must be open for extension to run
  - Chrome Web Store review process
- **Not recommended** for this use case due to effort vs. benefit

### Option D: Electron Desktop App
- **Effort:** 2-3 weeks
- **What:** Full desktop app with UI, cross-platform
- **User experience:** Native installer (Windows .exe, Mac .dmg), full GUI
- **Features:**
  - Visual config editor (no more editing JSON)
  - Real-time log viewer
  - Calendar view of logged time
  - System tray integration
- **Limitation:** Heavy (~100MB+), complex build pipeline
- **Not recommended** unless team grows significantly

### Recommendation
Start with **Option A** (PyInstaller .exe) for immediate wins. Upgrade to **Option B** (tray app) when ready to eliminate Task Scheduler dependency. Skip C and D unless requirements change.

---

## 2. Mac/Linux Support (Priority: Medium) -- IMPLEMENTED (v3.5)

**Implemented in:** v3.5 (Feb 22, 2026)

Everything listed below was built:
- `install.sh` -- 7-step Mac installer (deps, setup wizard, overhead, cron, tray app)
- Cron jobs for daily sync, weekly verify, monthly submit (BSD date compatible)
- `tray_app.py` -- full Mac support (osascript toasts/dialogs, LaunchAgent auto-start, fcntl mutex)
- `tempo_automation.py` -- Mac toast notifications via osascript
- Platform guards: `sys.platform == 'win32'` / `== 'darwin'`
- `winotify` marked Windows-only in requirements.txt

---

## 3. Additional Feature Ideas (Priority: Low)

### Retry Logic
- Exponential backoff for failed API calls
- Max 3 retries per call
- Log each retry attempt

### --dry-run Flag
- Show what would be logged without actually creating worklogs
- Useful for testing and verification

### Token Validation on Startup
- Quick API call to verify both Jira and Tempo tokens are valid
- Fail fast with clear error message instead of failing mid-run

### Weighted Time Distribution
- Instead of equal split, allow configuring weight per ticket
- e.g., higher priority tickets get more hours
- Could read from ticket priority field or custom config

### Holiday/Leave Calendar -- IMPLEMENTED (v3.0)
- **Implemented in:** v3.0 (Feb 17, 2026)
- All items completed: weekend guard, org holidays (auto-fetch), country/state holidays (100+ countries), PTO management, override system, schedule CLI, interactive menu, weekly/monthly verification
- Enhanced in v3.4: overhead story logging on PTO/holidays
- Enhanced in v3.6: monthly shortfall detection, --view-monthly, --fix-shortfall

### Backfill Mode -- PARTIALLY IMPLEMENTED (v3.0)
- `--verify-week` handles weekly backfill automatically (v3.0)
- Date-range backfill (`--from --to`) deferred as a future enhancement

### Slack Notifications
- Alternative to email notifications
- Webhook-based, simpler setup than SMTP
