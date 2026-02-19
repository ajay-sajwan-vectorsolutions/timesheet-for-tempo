# Tempo Automation - Quick Reference

## 🚀 Quick Commands

```bash
# Daily sync (manual)
python tempo_automation.py

# Sync specific date
python tempo_automation.py --date 2026-02-15

# Submit monthly timesheet
python tempo_automation.py --submit

# Run setup wizard again
python tempo_automation.py --setup

# View help
python tempo_automation.py --help
```

## 📁 Important Files

| File | Purpose |
|------|---------|
| `config.json` | Your personal configuration |
| `tempo_automation.log` | Execution logs |
| `tempo_automation.py` | Main script |

## 🔧 Quick Fixes

**Script not running?**
```bash
# Check Python
python --version  # Should be 3.7+

# Install dependencies
pip install -r requirements.txt
```

**Email not working?**
```bash
# For Gmail, use App Password:
# Google Account → Security → App Passwords
```

**Need to update credentials?**
```bash
# Edit config.json manually, or
python tempo_automation.py --setup
```

## 📅 Scheduled Tasks

**Windows:**
- View: Open Task Scheduler → "TempoAutomation-*"
- Disable: Right-click task → Disable
- Delete: `schtasks /Delete /TN "TempoAutomation-DailySync" /F`

**Mac/Linux:**
- View: `crontab -l`
- Edit: `crontab -e`
- Remove lines containing "tempo_automation.py"

## 🔍 Logs

**View recent activity:**

Windows:
```cmd
type tempo_automation.log | more
```

Mac/Linux:
```bash
tail -f tempo_automation.log
```

## 🆘 Get Help

1. Check `tempo_automation.log`
2. See README.md troubleshooting section
3. Slack: #tempo-automation
4. Email: [support email]

## 📊 What Gets Synced

**Developers:**
- Jira worklogs → Tempo entries
- Automatic daily

**Product Owners / Sales:**
- Manual activities from config
- Automatic daily

**Everyone:**
- Auto-submission on last day of month
- Email summaries

## ⚙️ Configuration

Edit `config.json` to customize:
- Daily work hours
- Sync time (default 6 PM)
- Email notifications
- Manual activities
- Auto-submit preference

## 🎯 Success Indicators

After running, you should see:
```
✓ SYNC COMPLETE
Total entries: 2
Total hours: 8.0 / 8
Status: ✓ Complete
```

And receive an email summary.

## ⚠️ When to Run Manually

- Computer was off at 6 PM
- Need to backfill previous days
- Testing the setup
- Credential update

## 🔐 Security

- Credentials stored locally only
- No cloud storage
- API tokens (not passwords)
- HTTPS encryption for API calls

## 📞 Emergency Contact

If completely broken:
1. Stop scheduled tasks
2. Revert to manual entry
3. Contact support
4. Check logs for error details

---

*Keep this handy for quick reference!*
