@echo off
REM ============================================================================
REM Tempo Automation - Windows Installer
REM ============================================================================
REM This script will:
REM 1. Check Python installation
REM 2. Install dependencies
REM 3. Run setup wizard
REM 4. Schedule daily and monthly tasks
REM 5. Optionally set up system tray app
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

echo ✓ Python found
python --version
echo.

REM ============================================================================
REM Install dependencies
REM ============================================================================

echo [2/6] Installing Python dependencies...
python -m pip install --upgrade pip --quiet
python -m pip install -r requirements.txt --quiet

if %errorlevel% neq 0 (
    echo.
    echo ERROR: Failed to install dependencies
    pause
    exit /b 1
)

echo ✓ Dependencies installed
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
echo ✓ Setup complete
echo.

REM ============================================================================
REM Create scheduled tasks
REM ============================================================================

echo [4/6] Setting up scheduled tasks...
echo.

REM Get full path to Python and script
for %%i in (python.exe) do set PYTHON_PATH=%%~$PATH:i
set SCRIPT_PATH=%SCRIPT_DIR%tempo_automation.py

REM Daily sync task (6:00 PM every day)
echo Creating daily sync task (runs at 6:00 PM)...
schtasks /Create /TN "TempoAutomation-DailySync" /TR "\"%PYTHON_PATH%\" \"%SCRIPT_PATH%\"" /SC DAILY /ST 18:00 /F >nul 2>&1

if %errorlevel% equ 0 (
    echo ✓ Daily sync task created
) else (
    echo ✗ Failed to create daily sync task
    echo   You may need to run this as Administrator
)

REM Monthly submission task (11:00 PM on last day of month)
echo Creating monthly submission task (runs at 11:00 PM on last day)...

REM Note: Windows Task Scheduler doesn't have direct "last day of month" trigger
REM So we create a task that runs on the 28th-31st and the script will check if it's the last day
schtasks /Create /TN "TempoAutomation-MonthlySubmit" /TR "\"%PYTHON_PATH%\" \"%SCRIPT_PATH%\" --submit" /SC MONTHLY /D 28,29,30,31 /ST 23:00 /F >nul 2>&1

if %errorlevel% equ 0 (
    echo ✓ Monthly submission task created
) else (
    echo ✗ Failed to create monthly submission task
    echo   You may need to run this as Administrator
)

echo.

REM ============================================================================
REM Tray App Setup (optional)
REM ============================================================================

echo [5/6] System Tray App (optional)
echo.
echo The tray app shows a notification at your configured sync time
echo and lives in your system tray for quick access.
echo.
set /p TRAY_SETUP="Set up tray app? (y/n): "

if /i "%TRAY_SETUP%"=="y" (
    echo.
    echo Installing tray app dependencies...
    python -m pip install pystray Pillow --quiet
    echo Registering auto-start on login...
    for %%i in (python.exe) do set PYTHONW_PATH=%%~dp$PATH:ipythonw.exe
    "%PYTHON_PATH%" "%SCRIPT_DIR%tray_app.py" --register
    echo Starting tray app...
    start "" "%PYTHONW_PATH%" "%SCRIPT_DIR%tray_app.py"
    echo [OK] Tray app is running
    echo.
    echo NOTE: If using the tray app only, you can disable the Task Scheduler daily task:
    echo   schtasks /Change /TN "TempoAutomation-DailySync" /DISABLE
)

echo.

REM ============================================================================
REM Test run
REM ============================================================================

echo [6/6] Running test...
echo.
echo Would you like to test the automation now? (This will sync today's timesheet)
set /p TEST_RUN="Run test? (y/n): "

if /i "%TEST_RUN%"=="y" (
    echo.
    echo Running test sync...
    python tempo_automation.py
)

echo.

REM ============================================================================
REM Installation complete
REM ============================================================================

echo.
echo ============================================================
echo ✓ INSTALLATION COMPLETE!
echo ============================================================
echo.
echo Your automation is now set up and will run automatically:
echo   - Daily: 6:00 PM (sync timesheets via dialog or tray app)
echo   - Monthly: 11:00 PM on last day (submit for approval)
echo.
echo Configuration file: %SCRIPT_DIR%config.json
echo Log file: %SCRIPT_DIR%tempo_automation.log
echo.
echo You can manually run the script anytime:
echo   python tempo_automation.py          (sync today)
echo   python tempo_automation.py --submit (submit timesheet)
echo.
echo Tray app commands:
echo   python tray_app.py --register       (auto-start on login)
echo   python tray_app.py --unregister     (remove auto-start)
echo.
echo To view scheduled tasks:
echo   Open Task Scheduler and look for "TempoAutomation-*"
echo.
echo To uninstall:
echo   Run: schtasks /Delete /TN "TempoAutomation-DailySync" /F
echo   Run: schtasks /Delete /TN "TempoAutomation-MonthlySubmit" /F
echo   Run: python tray_app.py --unregister
echo.
echo ============================================================
echo.

pause
