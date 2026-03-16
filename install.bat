@echo off
setlocal enabledelayedexpansion
REM ============================================================================
REM Tempo Automation - Windows Installer
REM ============================================================================
REM This script will:
REM 1. Detect Python (embedded or system)
REM 2. Install dependencies (skip if pre-bundled)
REM 3. Run setup wizard
REM 4. Configure overhead stories
REM 5. Generate wrapper scripts and schedule tasks
REM 6. Set up system tray app (auto-start on login)
REM 7. Optionally run a test sync
REM ============================================================================

REM ============================================================================
REM Check for Administrator privileges (required for schtasks)
REM ============================================================================
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting Administrator privileges...
    powershell -Command "Start-Process -Verb RunAs -FilePath cmd.exe -ArgumentList ('/c cd /d \"' + '%~dp0.' + '\" && \"' + '%~f0' + '\"')"
    exit /b
)

echo.
echo ============================================================
echo TEMPO TIMESHEET AUTOMATION - WINDOWS INSTALLER
echo ============================================================
echo.

REM Get script directory
set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%"

REM ============================================================================
REM Detect Python: embedded first, then system PATH
REM ============================================================================

echo [1/7] Detecting Python...

set PYTHON_EXE=
set PYTHONW_EXE=

REM Check 1: Embedded Python (shipped in zip)
if exist "%SCRIPT_DIR%python\python.exe" (
    set "PYTHON_EXE=%SCRIPT_DIR%python\python.exe"
    set "PYTHONW_EXE=%SCRIPT_DIR%python\pythonw.exe"
    echo [OK] Found embedded Python
    "%SCRIPT_DIR%python\python.exe" --version
    echo.
    goto :python_found
)

REM Check 2: System Python in PATH
python --version >nul 2>&1
if %errorlevel% equ 0 (
    REM Resolve full path to python.exe
    for %%i in (python.exe) do set "PYTHON_EXE=%%~$PATH:i"
    REM Derive pythonw.exe from same directory
    for %%i in (python.exe) do set "PYTHONW_DIR=%%~dp$PATH:i"
    set "PYTHONW_EXE=!PYTHONW_DIR!pythonw.exe"
    if not exist "!PYTHONW_EXE!" (
        echo [!] pythonw.exe not found, falling back to python.exe
        set "PYTHONW_EXE=!PYTHON_EXE!"
    )
    echo [OK] Found system Python
    python --version
    echo.
    goto :python_found
)

REM Check 3: Neither found
echo.
echo ERROR: Python is not installed or not in PATH
echo.
echo Option A: Re-download the "Windows Full" zip (includes embedded Python)
echo Option B: Install Python 3.7+ from https://www.python.org/downloads/
echo           Make sure to check "Add Python to PATH" during installation!
echo.
pause
exit /b 1

:python_found
echo   python.exe:  %PYTHON_EXE%
echo   pythonw.exe: %PYTHONW_EXE%
echo.

REM ============================================================================
REM Install dependencies (skip if lib/ exists from embedded zip)
REM ============================================================================

echo [2/7] Installing Python dependencies...
echo.

if exist "%SCRIPT_DIR%lib" (
    echo [OK] Pre-bundled lib\ directory found -- skipping pip install
) else (
    "%PYTHON_EXE%" -m pip install --upgrade pip
    "%PYTHON_EXE%" -m pip install -r requirements.txt

    if !errorlevel! neq 0 (
        echo.
        echo ERROR: Failed to install dependencies
        pause
        exit /b 1
    )
    echo [OK] Dependencies installed ^(requests, holidays, pystray, Pillow, winotify^)
)
echo.

REM ============================================================================
REM Restore previous config from AppData backup (re-installation support)
REM ============================================================================

if not exist "%SCRIPT_DIR%config.json" (
    if exist "%APPDATA%\TempoAutomation\config.json" (
        echo [INFO] Previous installation detected - restoring config from AppData backup...
        copy "%APPDATA%\TempoAutomation\config.json" "%SCRIPT_DIR%config.json" >nul
        echo [OK] Config restored - existing credentials will be revalidated automatically
        echo.
    )
)

REM ============================================================================
REM Run setup wizard
REM ============================================================================

echo [3/7] Running setup wizard...
echo.
"%PYTHON_EXE%" tempo_automation.py --setup

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
REM Select overhead stories (developers only)
REM ============================================================================

echo [4/7] Configuring overhead stories...
echo.
echo Overhead stories are used for daily default hours (e.g., 2h/day),
echo PTO days, holidays, and days with no active tickets.
echo.
set /p SELECT_OH="Configure overhead stories now? (y/n, default: y): "
if /i "%SELECT_OH%"=="n" (
    echo Skipped. You can configure later: python tempo_automation.py --select-overhead
) else (
    "%PYTHON_EXE%" tempo_automation.py --select-overhead
    if !errorlevel! neq 0 (
        echo.
        echo [!] Overhead selection skipped or failed
        echo     You can configure later: python tempo_automation.py --select-overhead
    )
)
echo.

REM ============================================================================
REM Generate wrapper scripts and create scheduled tasks
REM ============================================================================

echo [5/7] Setting up scheduled tasks...
echo.

REM -- Generate run_daily.bat with detected Python path --
echo Generating run_daily.bat...
(
    echo @echo off
    echo echo ============================================ ^>^> "%SCRIPT_DIR%daily-timesheet.log"
    echo echo Run: %%date%% %%time%% ^>^> "%SCRIPT_DIR%daily-timesheet.log"
    echo echo ============================================ ^>^> "%SCRIPT_DIR%daily-timesheet.log"
    echo "%PYTHONW_EXE%" "%SCRIPT_DIR%confirm_and_run.py"
) > "%SCRIPT_DIR%run_daily.bat"

REM -- Generate run_weekly.bat with detected Python path --
echo Generating run_weekly.bat...
(
    echo @echo off
    echo echo ============================================ ^>^> "%SCRIPT_DIR%daily-timesheet.log"
    echo echo Weekly Verify Run: %%date%% %%time%% ^>^> "%SCRIPT_DIR%daily-timesheet.log"
    echo echo ============================================ ^>^> "%SCRIPT_DIR%daily-timesheet.log"
    echo "%PYTHON_EXE%" "%SCRIPT_DIR%tempo_automation.py" --verify-week --logfile "%SCRIPT_DIR%daily-timesheet.log"
) > "%SCRIPT_DIR%run_weekly.bat"

REM -- Generate run_monthly.bat with detected Python path --
echo Generating run_monthly.bat...
(
    echo @echo off
    echo echo ============================================ ^>^> "%SCRIPT_DIR%daily-timesheet.log"
    echo echo Run: %%date%% %%time%% ^(Monthly Submit^) ^>^> "%SCRIPT_DIR%daily-timesheet.log"
    echo echo ============================================ ^>^> "%SCRIPT_DIR%daily-timesheet.log"
    echo "%PYTHON_EXE%" "%SCRIPT_DIR%tempo_automation.py" --submit --logfile "%SCRIPT_DIR%daily-timesheet.log"
) > "%SCRIPT_DIR%run_monthly.bat"

echo [OK] Wrapper scripts generated with detected Python path
echo.

REM Daily sync task (weekdays only at 6:00 PM, uses OK/Cancel dialog wrapper)
echo Creating daily sync task ^(Mon-Fri at 6:00 PM^)...
schtasks /Create /TN "TempoAutomation-DailySync" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 18:00 /TR "\"%SCRIPT_DIR%run_daily.bat\"" /F >nul 2>&1

if %errorlevel% equ 0 (
    echo [OK] Daily sync task created ^(weekdays only^)
) else (
    echo [FAIL] Failed to create daily sync task
    echo   You may need to run this as Administrator
)

REM Weekly verification task (Friday at 4:00 PM)
echo Creating weekly verification task ^(Fridays at 4:00 PM^)...
schtasks /Create /TN "TempoAutomation-WeeklyVerify" /SC WEEKLY /D FRI /ST 16:00 /TR "\"%SCRIPT_DIR%run_weekly.bat\"" /F >nul 2>&1

if %errorlevel% equ 0 (
    echo [OK] Weekly verification task created
) else (
    echo [FAIL] Failed to create weekly verification task
    echo   You may need to run this as Administrator
)

REM Monthly submission task (11:00 PM on last day of each month)
echo Creating monthly submission task ^(last day of month at 11:00 PM^)...
schtasks /Create /TN "TempoAutomation-MonthlySubmit" /SC MONTHLY /MO LASTDAY /M * /ST 23:00 /TR "\"%SCRIPT_DIR%run_monthly.bat\"" /F >nul 2>&1

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

echo [6/7] Setting up System Tray App...
echo.
echo The tray app lives in your system tray, shows a notification at your
echo configured sync time, and lets you sync with one click.
echo It will start automatically every time you log in to Windows.
echo.

REM Stop any existing tray app instance before starting fresh
echo Stopping any existing tray app...
"%PYTHON_EXE%" "%SCRIPT_DIR%tray_app.py" --stop >nul 2>&1
timeout /t 2 /nobreak >nul

REM Register auto-start on login
"%PYTHON_EXE%" "%SCRIPT_DIR%tray_app.py" --register

REM Start the tray app now (detached -- no console window, no terminal tab)
echo Starting tray app...
echo CreateObject("WScript.Shell").Run """%PYTHONW_EXE%"" ""%SCRIPT_DIR%tray_app.py""", 0, False > "%TEMP%\_tempo_launch.vbs"
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

echo [7/7] Test sync (optional)
echo.
echo Would you like to test the automation now?
echo This will sync today's timesheet to verify everything works.
echo.
set /p TEST_RUN="Run test? (y/n): "

if /i "%TEST_RUN%"=="y" (
    echo.
    echo Running test sync...
    echo.
    "%PYTHON_EXE%" tempo_automation.py
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
echo   Python: %PYTHON_EXE%
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
endlocal
