---
name: deploy-scheduler
description: Register or update Windows Task Scheduler jobs for Tempo Automation (DailySync, WeeklyVerify, MonthlySubmit). Use when setting up or fixing scheduled tasks.
user-invocable: true
disable-model-invocation: true
argument-hint: [optional: daily|weekly|monthly|all]
---

# Deploy Tempo Scheduler Tasks

Register Windows Task Scheduler tasks for automated Tempo sync.
Requires Administrator Command Prompt.

## Prerequisites
- Run as Administrator
- Batch wrapper files exist: `run_daily.bat`, `run_weekly.bat`, `run_monthly.bat`
- Python installed and on PATH

## Register All Tasks

### 1. Daily Sync (Weekdays at 6 PM)
```cmd
schtasks /Create /TN "TempoAutomation-DailySync" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 18:00 /TR "D:\working\AI-Tempo-automation\v2\run_daily.bat" /F
```

### 2. Weekly Verify (Fridays at 4 PM)
```cmd
schtasks /Create /TN "TempoAutomation-WeeklyVerify" /SC WEEKLY /D FRI /ST 16:00 /TR "D:\working\AI-Tempo-automation\v2\run_weekly.bat" /F
```

### 3. Monthly Submit (Days 28-31 at 11 PM)
```cmd
schtasks /Create /TN "TempoAutomation-MonthlySubmit" /SC MONTHLY /D 28,29,30,31 /ST 23:00 /TR "D:\working\AI-Tempo-automation\v2\run_monthly.bat" /F
```

## Verify Tasks
```cmd
schtasks /Query /TN "TempoAutomation-DailySync"
schtasks /Query /TN "TempoAutomation-WeeklyVerify"
schtasks /Query /TN "TempoAutomation-MonthlySubmit"
```

## Troubleshooting
- **Access Denied:** Must run Command Prompt as Administrator
- **Task doesn't run:** Check bat file path exists and Python path is correct
- **Nested quote issues:** Always use bat wrappers, never put python.exe directly in /TR

## Alternative: System Tray App (Recommended)
The tray app (`tray_app.py`) handles scheduling internally and is the preferred method:
```cmd
pythonw tray_app.py
```
Both tray app and Task Scheduler can coexist safely (sync is idempotent).
