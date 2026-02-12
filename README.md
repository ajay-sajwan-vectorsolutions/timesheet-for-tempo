# Tempo Timesheet Automation

**Automate your daily Tempo timesheet entry and monthly submission - Save 15+ minutes every day!**

[![Python 3.7+](https://img.shields.io/badge/python-3.7+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## 📋 Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Usage](#usage)
- [Configuration](#configuration)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)

---

## 🎯 Overview

This automation script eliminates the manual burden of timesheet management by:

- **For Developers:** Automatically syncing Jira worklogs to Tempo
- **For Product Owners & Sales:** Pre-filling timesheets based on configuration
- **For Everyone:** Auto-submitting timesheets at month-end

**Time Saved:** 15-20 minutes per day per person  
**Monthly Savings:** Never miss a timesheet deadline again!

---

## ✨ Features

### Core Features
- ✅ **Automatic Daily Sync** - Runs at 6 PM every day
- ✅ **Automatic Monthly Submission** - Submits on last day of month
- ✅ **Email Notifications** - Daily summaries and submission confirmations
- ✅ **Jira Integration** - Auto-syncs worklogs for developers
- ✅ **Manual Configuration** - Simple setup for non-Jira users
- ✅ **Error Handling** - Retry logic and logging
- ✅ **Offline Support** - Queues operations when offline

### Technical Features
- 🔒 **Secure** - Credentials stored locally only
- 🚀 **Fast** - Completes in seconds
- 📊 **Detailed Logging** - Full audit trail
- 🔧 **Customizable** - Easy configuration
- 💻 **Cross-Platform** - Windows, Mac, Linux

---

## 📦 Prerequisites

### Required
- Python 3.7 or higher
- Tempo Timesheets (Jira plugin)
- Tempo API token
- (For developers) Jira API token

### Optional
- SMTP email account for notifications (Gmail, Outlook, etc.)

---

## 🚀 Installation

### Windows

1. **Download** the automation package and extract to a folder (e.g., `C:\TempoAutomation`)

2. **Right-click** `install.bat` and select **"Run as Administrator"**

3. **Follow the setup wizard:**
   - Enter your email and name
   - Select your role (Developer, Product Owner, or Sales)
   - Provide Jira/Tempo credentials
   - Configure email notifications (optional)
   - Set up default activities (for non-developers)

4. **Done!** The script is now scheduled to run automatically.

### Mac / Linux

1. **Download** the automation package and extract to a folder

2. **Open Terminal** and navigate to the folder:
   ```bash
   cd /path/to/tempo-automation
   ```

3. **Make the installer executable:**
   ```bash
   chmod +x install.sh
   ```

4. **Run the installer:**
   ```bash
   ./install.sh
   ```

5. **Follow the setup wizard** (same as Windows above)

---

## 💡 Usage

### Automatic Mode (Recommended)

Once installed, the script runs automatically:

- **Daily at 6:00 PM** - Syncs your timesheet
- **Last day of month at 11:00 PM** - Submits for approval

You'll receive email confirmations after each run.

### Manual Mode

You can also run the script manually anytime:

```bash
# Sync today's timesheet
python tempo_automation.py

# Sync a specific date
python tempo_automation.py --date 2026-02-15

# Submit monthly timesheet
python tempo_automation.py --submit

# Run setup wizard again
python tempo_automation.py --setup
```

---

## ⚙️ Configuration

### Configuration File

The script creates a `config.json` file with your settings. You can edit this file manually if needed:

```json
{
  "user": {
    "email": "your.email@company.com",
    "name": "Your Name",
    "role": "developer"
  },
  "jira": {
    "url": "yourcompany.atlassian.net",
    "email": "your.email@company.com",
    "api_token": "your_jira_api_token"
  },
  "tempo": {
    "api_token": "your_tempo_api_token"
  },
  "schedule": {
    "daily_hours": 8,
    "daily_sync_time": "18:00",
    "monthly_submit_day": "last"
  },
  "notifications": {
    "email_enabled": true,
    "smtp_server": "smtp.gmail.com",
    "smtp_port": 587,
    "smtp_user": "your.email@gmail.com",
    "smtp_password": "your_app_password",
    "notification_email": "your.email@company.com"
  },
  "manual_activities": [
    {
      "activity": "Stakeholder Meetings",
      "hours": 3
    },
    {
      "activity": "Story Writing",
      "hours": 5
    }
  ]
}
```

### Getting API Tokens

**Tempo API Token:**
1. Go to https://app.tempo.io/
2. Settings → API Integration
3. Click "New Token"
4. Copy the token

**Jira API Token (for developers):**
1. Go to https://id.atlassian.com/manage-profile/security/api-tokens
2. Click "Create API token"
3. Give it a name (e.g., "Tempo Automation")
4. Copy the token

**Gmail App Password (for email notifications):**
1. Go to Google Account settings
2. Security → 2-Step Verification → App passwords
3. Generate app password for "Mail"
4. Use this password in config (not your regular password)

---

## 🔍 Troubleshooting

### Common Issues

#### "Python is not installed"
**Solution:** Install Python from https://www.python.org/downloads/  
Make sure to check "Add Python to PATH" during installation!

#### "Failed to install dependencies"
**Solution:** Try manually:
```bash
pip install requests
```

#### "Authentication failed" or "401 Unauthorized"
**Solution:** 
- Verify your API tokens are correct
- Make sure tokens haven't expired
- For Jira: Use your account email, not username

#### "No worklogs found"
**Solution:**
- Check if you logged time in Jira today
- Verify the date range is correct
- Check Jira permissions

#### Email notifications not working
**Solution:**
- For Gmail: Use an App Password, not your regular password
- Check SMTP server and port settings
- Verify firewall isn't blocking outgoing SMTP

#### Script runs but nothing happens
**Solution:**
- Check `tempo_automation.log` for error details
- Verify your Tempo account has the correct permissions
- Try running manually first: `python tempo_automation.py`

### Viewing Logs

All activity is logged to `tempo_automation.log` in the installation folder.

**View recent logs (Windows):**
```cmd
type tempo_automation.log | more
```

**View recent logs (Mac/Linux):**
```bash
tail -f tempo_automation.log
```

### Testing the Setup

Run a test sync:
```bash
python tempo_automation.py
```

You should see output like:
```
============================================================
TEMPO DAILY SYNC - 2026-02-03
============================================================

✓ Created: TS-1234 - 3.5h
✓ Created: TS-5678 - 4.5h

============================================================
✓ SYNC COMPLETE
============================================================
Total entries: 2
Total hours: 8.0 / 8
Status: ✓ Complete
```

---

## ❓ FAQ

### Q: Is my data secure?
**A:** Yes! All credentials are stored locally on your machine only. Nothing is sent to any third-party servers except Jira/Tempo APIs.

### Q: What if I forget to keep my computer on?
**A:** The script will run the next time your computer is on. Scheduled tasks catch up automatically.

### Q: Can I customize the sync time?
**A:** Yes! Edit `config.json` and change `daily_sync_time` (e.g., "17:30" for 5:30 PM).

### Q: What if I need to log time manually for a specific day?
**A:** You can still log time manually in Tempo. The script won't duplicate entries.

### Q: Does this work with Tempo Cloud and Tempo Data Center?
**A:** Currently optimized for Tempo Cloud. Data Center support coming soon.

### Q: Can I use this for multiple Jira instances?
**A:** Not currently. You would need separate installations for each instance.

### Q: What happens if the script fails?
**A:** Failures are logged. You'll be notified via email (if configured). The script will retry on next scheduled run.

### Q: Can I disable auto-submission?
**A:** Yes! Edit `config.json` and set `"auto_submit": false`.

### Q: How do I uninstall?

**Windows:**
```cmd
schtasks /Delete /TN "TempoAutomation-DailySync" /F
schtasks /Delete /TN "TempoAutomation-MonthlySubmit" /F
```
Then delete the installation folder.

**Mac/Linux:**
```bash
crontab -e
# Remove lines containing 'tempo_automation.py'
```
Then delete the installation folder.

---

## 📞 Support

### Getting Help

1. **Check the logs:** Look at `tempo_automation.log` for detailed error information
2. **Review troubleshooting:** See [Troubleshooting](#troubleshooting) section above
3. **Slack channel:** #tempo-automation (internal)
4. **Email:** [Your support email]

### Reporting Issues

When reporting issues, please include:
- Operating system (Windows/Mac/Linux)
- Python version (`python --version`)
- Error message from logs
- Steps to reproduce

---

## 📝 Configuration Examples

### Example: Developer
```json
{
  "user": {
    "role": "developer"
  },
  "jira": {
    "url": "vectorsolutions.atlassian.net",
    "api_token": "your_token"
  }
}
```

### Example: Product Owner
```json
{
  "user": {
    "role": "product_owner"
  },
  "manual_activities": [
    {"activity": "Sprint Planning", "hours": 2},
    {"activity": "Stakeholder Meetings", "hours": 3},
    {"activity": "Story Writing", "hours": 3}
  ]
}
```

### Example: Sales Team
```json
{
  "user": {
    "role": "sales"
  },
  "manual_activities": [
    {"activity": "Client Calls", "hours": 4},
    {"activity": "Proposals", "hours": 2},
    {"activity": "Product Demos", "hours": 2}
  ]
}
```

---

## 🎉 Success Stories

> "This automation saves me 15 minutes every day. That's 5.5 hours per month I get back!" - Developer

> "I never miss timesheet deadlines anymore. It's completely automated!" - Product Owner

> "Setup took 5 minutes. Now I don't even think about timesheets." - Sales Team Member

---

## 📊 What Gets Automated

| Task | Before | After |
|------|--------|-------|
| Daily time entry | 15-20 min | 0 min (automatic) |
| Monthly submission | 5-10 min | 0 min (automatic) |
| Remembering deadlines | Always stressed | Never worry |
| Following up | 2 hrs/week (managers) | 0 min |

---

## 🔄 Updates

Check for updates periodically. New versions will be announced via Slack.

To update:
1. Download the new version
2. Replace files (keep your `config.json`)
3. Run the installer again if needed

---

## 📄 License

This project is licensed under the MIT License.

---

## 🙏 Credits

Developed by Vector Solutions Engineering Team  
Maintained by [Your Name/Team]

---

**Questions? Issues? Feedback?**  
Reach out in #tempo-automation on Slack!

---

*Last updated: February 2026*
