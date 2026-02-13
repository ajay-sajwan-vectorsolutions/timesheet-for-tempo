# Tempo Automation - Future Enhancements

**Created:** February 12, 2026

---

## 1. Packaging & Distribution (Priority: High)

### Option A: PyInstaller .exe (Recommended first step)
- **Effort:** ~30 minutes, zero code changes
- **What:** Bundle Python + dependencies into a single `.exe`
- **User experience:** Double-click to run, no Python install needed
- **Limitation:** Windows-only (separate build for Mac), still needs Task Scheduler
- **Command:** `pyinstaller --onefile tempo_automation.py`
- **Notes:**
  - config.json would live next to the .exe
  - Need to handle SCRIPT_DIR path resolution for bundled mode
  - Can use `--icon` flag for custom icon

### Option B: System Tray App with built-in scheduler
- **Effort:** 1-2 days
- **What:** Same .exe but runs as a tray icon app with built-in daily scheduler
- **Libraries:** `pystray` or `PyQt` for tray, `schedule` or `APScheduler` for timer
- **User experience:** Install once, auto-starts with Windows, runs silently in tray
- **Features:**
  - Tray icon shows status (green = OK, red = error)
  - Right-click menu: Run Now, View Log, Settings, Exit
  - Auto-starts with Windows (registry entry or Start Menu shortcut)
  - Built-in scheduler eliminates need for Task Scheduler
- **Limitation:** Windows-only unless cross-platform GUI framework used

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

## 2. Mac/Linux Support (Priority: Medium)

### Current State
- Python script is fully cross-platform, works on Mac/Linux as-is
- Only the wrapper scripts and scheduling are Windows-specific

### What's Needed
1. **Shell scripts** (`run_daily.sh`, `run_monthly.sh`) equivalent to .bat files
2. **Cron jobs** instead of Task Scheduler:
   ```
   # Daily at 6 PM
   0 18 * * * /path/to/run_daily.sh

   # Last day of month at 11 PM
   0 23 28-31 * * [ "$(date -v+1d +\%d)" = "01" ] && /path/to/run_monthly.sh
   ```
3. **`python3`** instead of `python` in shell scripts
4. **`install.sh`** equivalent to `install.bat`

### No Python Code Changes Needed
- UTF-8 is native on Mac/Linux
- All libraries are cross-platform
- Path handling uses `pathlib.Path` (cross-platform)

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

### Holiday/Leave Calendar
- Skip logging on holidays and leave days
- Integrate with company calendar or manual config
- Avoid unnecessary API calls on non-work days

### Backfill Mode
- `--from 2026-02-01 --to 2026-02-10` to log multiple days at once
- Useful for catching up after vacation

### Slack Notifications
- Alternative to email notifications
- Webhook-based, simpler setup than SMTP
