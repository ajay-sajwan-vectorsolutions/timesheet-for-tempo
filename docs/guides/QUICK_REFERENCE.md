# Tempo Automation - Quick Reference

## Quick Commands

```bash
# Daily sync (manual)
python tempo_automation.py

# Sync specific date
python tempo_automation.py --date 2026-02-15

# Weekly verify & backfill
python tempo_automation.py --verify-week

# Submit monthly timesheet
python tempo_automation.py --submit

# View monthly hours per day
python tempo_automation.py --view-monthly

# Fix monthly shortfalls (interactive)
python tempo_automation.py --fix-shortfall

# Select overhead stories for PI
python tempo_automation.py --select-overhead

# Run setup wizard again
python tempo_automation.py --setup

# View help
python tempo_automation.py --help
```

## Important Files

| File | Purpose |
|------|---------|
| `config.json` | Your personal configuration (gitignored) |
| `daily-timesheet.log` | Execution output log |
| `tempo_automation.log` | Internal runtime logs |
| `tempo_automation.py` | Main script (4,224 lines) |
| `tray_app.py` | System tray app (~1,306 lines) |

## Quick Fixes

**Script not running?**
```bash
# Check Python
python --version  # Should be 3.7+

# Install dependencies
pip install -r requirements.txt
```

**Need to update credentials?**
```bash
# Edit config.json manually, or
python tempo_automation.py --setup
```

## Scheduled Tasks

**Windows:**
- View: Open Task Scheduler -> "TempoAutomation-*"
- Disable: Right-click task -> Disable
- Delete: `schtasks /Delete /TN "TempoAutomation-DailySync" /F`

**Mac/Linux:**
- View: `crontab -l`
- Edit: `crontab -e`
- Remove lines containing "tempo_automation.py"

## Logs

**View recent activity:**

Windows:
```cmd
type daily-timesheet.log | more
type tempo_automation.log | more
```

Mac/Linux:
```bash
tail -f daily-timesheet.log
tail -f tempo_automation.log
```

## What Gets Synced

**Developers:**
- Jira worklogs -> Tempo entries (automatic daily)
- 2h overhead + remaining hours across active tickets

**Product Owners / Sales:**
- Manual activities from config -> Tempo entries

**Everyone:**
- Auto-submission on last day of month (if no shortfalls)
- Weekly verify on Fridays catches missed days
- Toast notifications for sync status

## Tray App Menu

```
Sync Now
---
Configure        -> Add PTO / Select Overhead
Log and Reports  -> Daily Log / Schedule / View Monthly Hours
                    Fix Monthly Shortfall (when gaps exist)
---
Submit Timesheet   (last week of month, no gaps)
Settings
Exit
```

## Configuration

Edit `config.json` to customize:
- Daily work hours (default: 8)
- Sync time (default: 18:00)
- Country/state for holidays
- Overhead stories and distribution
- Notification preferences

## Success Indicators

After running, you should see:
```
[OK] SYNC COMPLETE
Total entries: 4
Total hours: 8.0 / 8.0
Status: [OK] Complete
```

## When to Run Manually

- Computer was off at sync time
- Need to backfill previous days (`--date YYYY-MM-DD`)
- Testing the setup
- After credential update

## Security

- Credentials stored locally only (DPAPI encrypted on Windows)
- No cloud storage of tokens
- API tokens (not passwords)
- HTTPS encryption for all API calls

---

*Keep this handy for quick reference!*
