@echo off
setlocal enabledelayedexpansion
for /f %%I in ('powershell -Command "Get-Date -Format yyyy-MM"') do set MONTH=%%I
set LOGFILE=D:\working\AI-Tempo-automation\v2\daily-timesheet-!MONTH!.log
echo ============================================ >> "!LOGFILE!"
echo Monthly Submit Run: %date% %time% >> "!LOGFILE!"
echo ============================================ >> "!LOGFILE!"
"C:\Users\asajwan.DESKTOP-TN8HNF1\AppData\Local\Programs\Python\Python314\python.exe" "D:\working\AI-Tempo-automation\v2\tempo_automation.py" --submit --logfile "!LOGFILE!"
endlocal
