@echo off
REM ============================================================================
REM Tempo Automation - Windows Installer
REM ============================================================================
REM This script will:
REM 1. Check Python installation
REM 2. Install dependencies
REM 3. Run setup wizard
REM 4. Schedule daily, weekly, and monthly tasks
REM 5. Set up system tray app (auto-start on login)
REM 6. Optionally run a test sync
REM ============================================================================

echo.
echo ============================================================
echo TEMPO TIMESHEET AUTOMATION - WINDOWS INSTALLER
echo ============================================================
echo.

REM Get script directory
set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%"

REM ============================================================================
REM Check Python installation
REM ============================================================================

echo [1/6] Checking Python installation...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo ERROR: Python is not installed or not in PATH
    echo.
    echo Please install Python 3.7 or higher from:
    echo https://www.python.org/downloads/
    echo.
    echo Make sure to check "Add Python to PATH" during installation!
    echo.
    pause
    exit /b 1
)

echo [OK] Python found
python --version
echo.

REM ============================================================================
REM Install dependencies
REM ============================================================================

echo [2/6] Installing Python dependencies...
echo.
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if %errorlevel% neq 0 (
    echo.
    echo ERROR: Failed to install dependencies
    pause
    exit /b 1
)

echo [OK] Dependencies installed (requests, holidays, pystray, Pillow, winotify)
echo.

REM ============================================================================
REM Run setup wizard
REM ============================================================================

echo [3/6] Running setup wizard...
echo.
python tempo_automation.py --setup

if %errorlevel% neq 0 (
    echo.
    echo ERROR: Setup failed
    pause
    exit /b 1
)

echo.
echo [OK] Setup complete
echo.

REM ============================================================================
REM Create scheduled tasks
REM ============================================================================

echo [4/6] Setting up scheduled tasks...
echo.

REM Daily sync task (weekdays only at 6:00 PM, uses OK/Cancel dialog wrapper)
echo Creating daily sync task (Mon-Fri at 6:00 PM)...
schtasks /Create /TN "TempoAutomation-DailySync" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 18:00 /TR "\"%SCRIPT_DIR%run_daily.bat\"" /F >nul 2>&1

if %errorlevel% equ 0 (
    echo [OK] Daily sync task created (weekdays only)
) else (
    echo [FAIL] Failed to create daily sync task
    echo   You may need to run this as Administrator
)

REM Weekly verification task (Friday at 4:00 PM)
echo Creating weekly verification task (Fridays at 4:00 PM)...
schtasks /Create /TN "TempoAutomation-WeeklyVerify" /SC WEEKLY /D FRI /ST 16:00 /TR "\"%SCRIPT_DIR%run_weekly.bat\"" /F >nul 2>&1

if %errorlevel% equ 0 (
    echo [OK] Weekly verification task created
) else (
    echo [FAIL] Failed to create weekly verification task
    echo   You may need to run this as Administrator
)

REM Monthly submission task (11:00 PM on days 28-31, script checks if last day)
echo Creating monthly submission task (last day of month at 11:00 PM)...
schtasks /Create /TN "TempoAutomation-MonthlySubmit" /SC MONTHLY /D 28,29,30,31 /ST 23:00 /TR "\"%SCRIPT_DIR%run_monthly.bat\"" /F >nul 2>&1

if %errorlevel% equ 0 (
    echo [OK] Monthly submission task created
) else (
    echo [FAIL] Failed to create monthly submission task
    echo   You may need to run this as Administrator
)

echo.

REM ============================================================================
REM Tray App Setup (recommended)
REM ============================================================================

echo [5/6] Setting up System Tray App...
echo.
echo The tray app lives in your system tray, shows a notification at your
echo configured sync time, and lets you sync with one click.
echo It will start automatically every time you log in to Windows.
echo.

REM Find pythonw.exe by looking next to python.exe (always installed together)
for %%i in (python.exe) do set PYTHON_DIR=%%~dp$PATH:i
set PYTHONW_PATH=%PYTHON_DIR%pythonw.exe

if not exist "%PYTHONW_PATH%" (
    echo [!] pythonw.exe not found at %PYTHONW_PATH%
    echo     Falling back to python.exe (a console window will appear)
    for %%i in (python.exe) do set PYTHONW_PATH=%%~$PATH:i
)
echo Using: %PYTHONW_PATH%

REM Stop any existing tray app instance before starting fresh
echo Stopping any existing tray app...
python "%SCRIPT_DIR%tray_app.py" --stop >nul 2>&1
timeout /t 2 /nobreak >nul

REM Register auto-start on login
python "%SCRIPT_DIR%tray_app.py" --register

REM Start the tray app now (detached -- no console window, no terminal tab)
echo Starting tray app...
echo CreateObject("WScript.Shell").Run """%PYTHONW_PATH%"" ""%SCRIPT_DIR%tray_app.py""", 0, False > "%TEMP%\_tempo_launch.vbs"
wscript "%TEMP%\_tempo_launch.vbs"
del "%TEMP%\_tempo_launch.vbs" >nul 2>&1
timeout /t 3 /nobreak >nul
echo [OK] Tray app is running in the system tray
echo.
echo NOTE: The tray app and Task Scheduler can coexist safely.
echo       The sync is idempotent (re-running overwrites previous entries).

echo.

REM ============================================================================
REM Test run
REM ============================================================================

echo [6/6] Test sync (optional)
echo.
echo Would you like to test the automation now?
echo This will sync today's timesheet to verify everything works.
echo.
set /p TEST_RUN="Run test? (y/n): "

if /i "%TEST_RUN%"=="y" (
    echo.
    echo Running test sync...
    echo.
    python tempo_automation.py
)

echo.

REM ============================================================================
REM Installation complete
REM ============================================================================

echo.
echo ============================================================
echo [OK] INSTALLATION COMPLETE!
echo ============================================================
echo.
echo Your automation is now set up and will run automatically:
echo.
echo   Tray App:
echo     - Starts on Windows login (system tray icon)
echo     - Notifies at your configured sync time (default 6:00 PM)
echo     - Right-click for menu: Sync Now, Add PTO, View Schedule, etc.
echo.
echo   Task Scheduler:
echo     - Daily:   Mon-Fri at 6:00 PM (sync via OK/Cancel dialog)
echo     - Weekly:  Fridays at 4:00 PM (verify hours, backfill gaps)
echo     - Monthly: Last day at 11:00 PM (verify + submit timesheet)
echo.
echo Files:
echo   Config:  %SCRIPT_DIR%config.json
echo   Log:     %SCRIPT_DIR%daily-timesheet.log
echo   Runtime: %SCRIPT_DIR%tempo_automation.log
echo.
echo Manual commands:
echo   python tempo_automation.py              (sync today)
echo   python tempo_automation.py --date DATE  (sync specific date)
echo   python tempo_automation.py --verify-week (verify this week)
echo   python tempo_automation.py --submit     (submit monthly)
echo   python tempo_automation.py --show-schedule (view calendar)
echo   python tempo_automation.py --manage     (schedule menu)
echo.
echo Uninstall:
echo   python tray_app.py --unregister
echo   schtasks /Delete /TN "TempoAutomation-DailySync" /F
echo   schtasks /Delete /TN "TempoAutomation-WeeklyVerify" /F
echo   schtasks /Delete /TN "TempoAutomation-MonthlySubmit" /F
echo   Then delete this folder.
echo.
echo ============================================================
echo.
echo This window will close in:
for /l %%i in (10,-1,1) do (
    echo   %%i...
    timeout /t 1 /nobreak >nul
)
