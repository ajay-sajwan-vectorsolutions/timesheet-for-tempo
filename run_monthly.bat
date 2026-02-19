@echo off
echo ============================================ >> "D:\working\AI-Tempo-automation\v2\daily-timesheet.log"
echo Run: %date% %time% (Monthly Submit) >> "D:\working\AI-Tempo-automation\v2\daily-timesheet.log"
echo ============================================ >> "D:\working\AI-Tempo-automation\v2\daily-timesheet.log"
"C:\Users\asajwan.DESKTOP-TN8HNF1\AppData\Local\Programs\Python\Python314\python.exe" "D:\working\AI-Tempo-automation\v2\tempo_automation.py" --submit --logfile "D:\working\AI-Tempo-automation\v2\daily-timesheet.log"

