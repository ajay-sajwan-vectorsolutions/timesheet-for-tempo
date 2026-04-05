# E004: install.bat PowerShell Cleanup Plan

**Goal:** Reduce AV/EDR flagging by replacing PowerShell calls in install.bat with batch-native equivalents.

**Current state:** 13 PowerShell invocations. VirusTotal score: 7/72 on Lite zip.

---

## All PowerShell Calls in install.bat

| # | Line | What it does | AV Risk | Batch replacement |
|---|------|-------------|---------|-------------------|
| 1 | 22 | **UAC elevation** (`Start-Process -Verb RunAs`) | HIGH | Not easily — batch has no native UAC. Could remove if install runs from admin prompt |
| 2 | 51 | **Detect old install via Registry** read | MEDIUM | `reg query "HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Run" /v TempoTrayApp` |
| 3 | 60 | **Detect old install via schtasks XML** parse | MEDIUM | `schtasks /Query` + `findstr` |
| 4 | 70 | **Detect old install via running process** (`Get-CimInstance Win32_Process`) | HIGH | `tasklist` + `wmic` or just skip (Methods 1-2 usually find it) |
| 5 | 115 | **Hard-kill old tray** (`Stop-Process -Force` via WMI) | HIGH | `taskkill /F /PID` after finding PID with `tasklist` |
| 6 | 133 | **Remove old registry autostart** entry | MEDIUM | `reg delete "HKCU\...\Run" /v TempoTrayApp /f` |
| 7-9 | 346,359,372 | **Get date** (`Get-Date -Format yyyy-MM`) in generated .bat files | LOW | `%date:~0,4%-%date:~5,2%` or `wmic os get localdatetime` |
| 10 | 387 | **Read config.json** (`ConvertFrom-Json`) | LOW | `findstr` + string parsing (too fragile — keep PowerShell) |
| 11 | 446 | **Launch tray hidden** (`Start-Process -WindowStyle Hidden`) | HIGH | `start /B "" pythonw.exe tray_app.py` (pythonw is already windowless) |

---

## Summary

| Category | Count | Can replace? |
|----------|-------|-------------|
| Process detection/kill (WMI) | 2 | Yes — `tasklist`/`taskkill` |
| Registry read/write | 2 | Yes — `reg query`/`reg delete` |
| Hidden process launch | 1 | Yes — `start /B` with pythonw |
| UAC elevation | 1 | Partially — can prompt user to run as admin instead |
| Date formatting | 3 | Yes — `wmic` or batch date parsing |
| JSON config read | 1 | Keep PowerShell (too fragile with batch parsing) |
| schtasks XML parse | 1 | Yes — `schtasks /Query` + `findstr` |

**Realistic plan:** Replace 10 of 13 calls. Keep PowerShell for JSON config read (#10), UAC (#1), and possibly schtasks XML (#3). That would drop from 13 to 2-3 PowerShell invocations.

**Expected impact:** ~40-50% reduction in AV flagging score.
