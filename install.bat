@echo off
setlocal enabledelayedexpansion

REM -- ANSI color support (Windows 10+ CMD) --
for /f %%a in ('echo prompt $E ^| cmd') do set "ESC=%%a"
set "C_OK=!ESC![32m"
set "C_FAIL=!ESC![31m"
set "C_WARN=!ESC![33m"
set "C_INFO=!ESC![34m"
set "C_HEAD=!ESC![36;1m"
set "C_BOLD=!ESC![1m"
set "C_DIM=!ESC![2m"
set "C_R=!ESC![0m"

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
echo !C_DIM!============================================================!C_R!
echo !C_HEAD!TEMPO TIMESHEET AUTOMATION - WINDOWS INSTALLER!C_R!
echo !C_DIM!============================================================!C_R!
echo.

REM Get script directory (source location of this installer)
set SOURCE_DIR=%~dp0

REM ============================================================================
REM Detect previous installation (any folder name) and clean up all traces
REM ============================================================================
REM Detection order:
REM   Method 1 - Registry TempoTrayApp run key  (covers standard --register flow)
REM   Method 2 - Scheduled task XML             (covers installs without tray running)
REM   Method 3 - Running pythonw.exe process    (covers active tray in any folder)
REM   Method 5 - Named-folder fallback scan     (last resort for well-known paths)
REM   (Method 4 = AppData backup, config-only, handled in Phase D below)
REM ============================================================================
set IS_UPGRADE=0
set OLD_INSTALL_DIR=

REM --- Method 1: Registry (reg.exe -- batch native, no PowerShell) ---
for /f "tokens=2,*" %%A in ('reg query "HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Run" /v TempoTrayApp 2^>nul ^| findstr "TempoTrayApp"') do (
    set "REG_VAL=%%B"
)
if defined REG_VAL (
    REM Extract the directory containing tray_app.py from the registry value
    for /f "tokens=*" %%i in ('echo !REG_VAL! ^| findstr /r /c:"tray_app\.py"') do (
        for %%p in ("!REG_VAL:tray_app.py=!") do (
            set "CAND=%%~p"
            REM Strip trailing quotes and spaces
            set "CAND=!CAND:"=!"
        )
    )
    if defined CAND if exist "!CAND!tray_app.py" (
        set "OLD_INSTALL_DIR=!CAND!"
        REM Strip trailing backslash
        if "!OLD_INSTALL_DIR:~-1!"=="\" set "OLD_INSTALL_DIR=!OLD_INSTALL_DIR:~0,-1!"
    )
)
set REG_VAL=
set CAND=

REM --- Method 2: Scheduled task query (batch native via findstr) ---
if "!OLD_INSTALL_DIR!"=="" (
    for /f "tokens=*" %%i in ('schtasks /Query /TN "TempoAutomation-DailySync" /FO LIST /V 2^>nul ^| findstr /C:"Task To Run"') do (
        set "TASK_LINE=%%i"
    )
    if defined TASK_LINE (
        REM Extract path before run_daily.bat (format: "Task To Run: <path>\run_daily.bat")
        for /f "tokens=1* delims=:" %%a in ("!TASK_LINE!") do set "TASK_CMD=%%b"
        if defined TASK_CMD (
            set "TASK_CMD=!TASK_CMD:run_daily.bat=!"
            set "TASK_CMD=!TASK_CMD:"=!"
            REM Trim leading space and trailing backslash
            for /f "tokens=* delims= " %%x in ("!TASK_CMD!") do set "TASK_CMD=%%x"
            if "!TASK_CMD:~-1!"=="\" set "TASK_CMD=!TASK_CMD:~0,-1!"
            if exist "!TASK_CMD!\tray_app.py" set "OLD_INSTALL_DIR=!TASK_CMD!"
        )
    )
    set TASK_LINE=
    set TASK_CMD=
)

REM --- Method 3: Running pythonw.exe process (wmic -- batch native) ---
if "!OLD_INSTALL_DIR!"=="" (
    for /f "usebackq tokens=*" %%i in (`wmic process where "name='pythonw.exe'" get CommandLine /value 2^>nul ^| findstr "tray_app.py"`) do (
        set "PROC_CMD=%%i"
    )
    if defined PROC_CMD (
        REM Remove CommandLine= prefix
        set "PROC_CMD=!PROC_CMD:CommandLine=!"
        set "PROC_CMD=!PROC_CMD:~1!"
        REM Find everything before tray_app.py in the command line
        for /f "tokens=1 delims=," %%p in ("!PROC_CMD!") do set "PROC_PART=%%p"
        if defined PROC_PART (
            set "PROC_PART=!PROC_PART:tray_app.py=!"
            set "PROC_PART=!PROC_PART:"=!"
            REM Get the last quoted path segment
            for /f "tokens=* delims= " %%x in ("!PROC_PART!") do set "PROC_PART=%%x"
            if "!PROC_PART:~-1!"=="\" set "PROC_PART=!PROC_PART:~0,-1!"
            if exist "!PROC_PART!\tray_app.py" set "OLD_INSTALL_DIR=!PROC_PART!"
        )
    )
    set PROC_CMD=
    set PROC_PART=
)

REM --- Method 5: Named-folder fallback scan ---
if "!OLD_INSTALL_DIR!"=="" (
    for %%D in (
        "C:\tempo-timesheet"
        "%USERPROFILE%\tempo-timesheet"
        "%USERPROFILE%\Desktop\tempo-timesheet"
        "%USERPROFILE%\Documents\tempo-timesheet"
        "%USERPROFILE%\Downloads\tempo-timesheet"
    ) do (
        if "!OLD_INSTALL_DIR!"=="" (
            if exist "%%~D\tray_app.py" if exist "%%~D\tempo_automation.py" (
                if /i not "%%~D"=="C:\tempo-timesheet" set "OLD_INSTALL_DIR=%%~D"
            )
        )
    )
)

REM ============================================================================
REM Phase A: Save config to temp before touching anything
REM ============================================================================
if not "!OLD_INSTALL_DIR!"=="" (
    echo !C_INFO![INFO]!C_R! Found previous installation at: !OLD_INSTALL_DIR!
    if exist "!OLD_INSTALL_DIR!\config.json" (
        copy /Y "!OLD_INSTALL_DIR!\config.json" "%TEMP%\_tempo_migrated_config.json" >nul
        echo !C_OK![OK]!C_R! Previous config saved - credentials will be carried over
    )
    set IS_UPGRADE=1
    echo.
)

REM ============================================================================
REM Phase B: Stop old tray (signal + wait + hard-kill fallback)
REM ============================================================================
if "!IS_UPGRADE!"=="1" (
    echo Stopping previous tray app instance...
    echo stop > "!OLD_INSTALL_DIR!\_tray_stop.signal"
    timeout /t 5 /nobreak >nul
    REM Hard-kill fallback: find pythonw.exe running from old dir via wmic, kill via taskkill
    set FORCE_KILLED=0
    for /f "usebackq tokens=*" %%i in (`wmic process where "name='pythonw.exe'" get ProcessId^,CommandLine /format:csv 2^>nul ^| findstr /i "tray_app.py"`) do (
        set "LINE=%%i"
        REM Check if this process command line contains the old install dir
        echo !LINE! | findstr /i /c:"!OLD_INSTALL_DIR!" >nul 2>&1
        if !errorlevel! equ 0 (
            for /f "tokens=1 delims=" %%p in ("!LINE!") do (
                REM Extract PID from the last CSV field
                for %%x in (!LINE!) do set "LAST_FIELD=%%x"
                taskkill /F /PID !LAST_FIELD! >nul 2>&1
                if !errorlevel! equ 0 set FORCE_KILLED=1
            )
        )
    )
    if !FORCE_KILLED! equ 1 (
        echo !C_OK![OK]!C_R! Old tray process force-stopped
    ) else (
        echo !C_OK![OK]!C_R! Old tray instance stopped gracefully
    )
    set LINE=
    set LAST_FIELD=
    set FORCE_KILLED=
    echo.
)

REM ============================================================================
REM Phase C: Remove all traces of previous installation
REM ============================================================================
if "!IS_UPGRADE!"=="1" (
    echo Removing previous installation traces...

    REM C1: Remove registry autostart entry
    reg delete "HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Run" /v TempoTrayApp /f >nul 2>&1
    echo   !C_OK![OK]!C_R! Registry autostart entry removed

    REM C2: Delete scheduled tasks
    schtasks /Delete /TN "TempoAutomation-DailySync"     /F >nul 2>&1
    schtasks /Delete /TN "TempoAutomation-WeeklyVerify"  /F >nul 2>&1
    schtasks /Delete /TN "TempoAutomation-MonthlySubmit" /F >nul 2>&1
    echo   !C_OK![OK]!C_R! Scheduled tasks removed

    REM C3: Remove artefact files from old dir
    del /f /q "!OLD_INSTALL_DIR!\_tray_stop.signal" >nul 2>&1
    del /f /q "!OLD_INSTALL_DIR!\.tray_app.lock"    >nul 2>&1

    REM C4: Remove AppData backup (prevent stale config bleeding into future installs)
    del /f /q "%APPDATA%\TempoAutomation\config.json" >nul 2>&1
    echo   !C_OK![OK]!C_R! AppData backup cleared

    REM C5: Delete old folder -- only if it is NOT the new install destination
    if /i not "!OLD_INSTALL_DIR!"=="C:\tempo-timesheet" (
        rmdir /s /q "!OLD_INSTALL_DIR!" >nul 2>&1
        if !errorlevel! equ 0 (
            echo   !C_OK![OK]!C_R! Old installation folder removed: !OLD_INSTALL_DIR!
        ) else (
            echo   !C_WARN![!]!C_R! Could not fully remove old folder ^(files still in use^)
            echo       Please delete manually: !OLD_INSTALL_DIR!
        )
    )
    echo.
)

REM Cleanup temp Python helper scripts
del "%TEMP%\_tempo_det2.py" >nul 2>&1
del "%TEMP%\_tempo_det3.py" >nul 2>&1
del "%TEMP%\_tempo_chk.py"  >nul 2>&1

REM ============================================================================
REM Step 0: Copy files to fixed install location
REM ============================================================================
set INSTALL_DIR=C:\tempo-timesheet
if "!IS_UPGRADE!"=="1" (
    echo Updating files at %INSTALL_DIR%...
) else (
    echo Installing files to %INSTALL_DIR%...
)
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"

copy /Y "%SOURCE_DIR%tempo_automation.py"  "%INSTALL_DIR%\" >nul
copy /Y "%SOURCE_DIR%tray_app.py"          "%INSTALL_DIR%\" >nul
copy /Y "%SOURCE_DIR%confirm_and_run.py"   "%INSTALL_DIR%\" >nul
copy /Y "%SOURCE_DIR%config_template.json" "%INSTALL_DIR%\" >nul
copy /Y "%SOURCE_DIR%requirements.txt"     "%INSTALL_DIR%\" >nul

if not exist "%INSTALL_DIR%\assets" mkdir "%INSTALL_DIR%\assets"
if exist "%SOURCE_DIR%assets\favicon.ico" copy /Y "%SOURCE_DIR%assets\favicon.ico" "%INSTALL_DIR%\assets\" >nul 2>&1

if exist "%SOURCE_DIR%python" xcopy /E /I /Y "%SOURCE_DIR%python" "%INSTALL_DIR%\python\" >nul
if exist "%SOURCE_DIR%lib"    xcopy /E /I /Y "%SOURCE_DIR%lib"    "%INSTALL_DIR%\lib\"    >nul

REM Redefine SCRIPT_DIR to install location; all subsequent steps use this
set SCRIPT_DIR=%INSTALL_DIR%\
cd /d "%INSTALL_DIR%"
echo !C_OK![OK]!C_R! Files installed to %INSTALL_DIR%
echo.

REM ============================================================================
REM Detect Python: embedded first, then system PATH
REM ============================================================================

echo !C_BOLD![1/7] Detecting Python...!C_R!

set PYTHON_EXE=
set PYTHONW_EXE=

REM Check 1: Embedded Python (shipped in zip)
if exist "%SCRIPT_DIR%python\python.exe" (
    set "PYTHON_EXE=%SCRIPT_DIR%python\python.exe"
    set "PYTHONW_EXE=%SCRIPT_DIR%python\pythonw.exe"
    echo !C_OK![OK]!C_R! Found embedded Python
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
        echo !C_WARN![!]!C_R! pythonw.exe not found, falling back to python.exe
        set "PYTHONW_EXE=!PYTHON_EXE!"
    )
    echo !C_OK![OK]!C_R! Found system Python
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

echo !C_BOLD![2/7] Installing Python dependencies...!C_R!
echo.

if exist "%SCRIPT_DIR%lib" (
    echo !C_OK![OK]!C_R! Pre-bundled lib\ directory found -- skipping pip install
) else (
    "%PYTHON_EXE%" -m pip install --upgrade pip
    "%PYTHON_EXE%" -m pip install -r requirements.txt

    if !errorlevel! neq 0 (
        echo.
        echo ERROR: Failed to install dependencies
        pause
        exit /b 1
    )
    echo !C_OK![OK]!C_R! Dependencies installed ^(requests, holidays, pystray, Pillow, winotify^)
)
echo.

REM ============================================================================
REM Phase D: Restore previous config into new install location
REM Priority: 1) migrated from old folder  2) AppData backup
REM ============================================================================

if not exist "%SCRIPT_DIR%config.json" (
    if exist "%TEMP%\_tempo_migrated_config.json" (
        copy /Y "%TEMP%\_tempo_migrated_config.json" "%SCRIPT_DIR%config.json" >nul
        del "%TEMP%\_tempo_migrated_config.json" >nul 2>&1
        echo !C_OK![OK]!C_R! Previous config restored from old installation - wizard will skip credential prompts
        echo.
    ) else if "!IS_UPGRADE!"=="1" (
        if exist "%APPDATA%\TempoAutomation\config.json" (
            copy /Y "%APPDATA%\TempoAutomation\config.json" "%SCRIPT_DIR%config.json" >nul
            echo !C_OK![OK]!C_R! Previous config restored from AppData backup - wizard will skip credential prompts
            echo.
        )
    )
)

REM ============================================================================
REM Run setup wizard
REM ============================================================================

echo !C_BOLD![3/7] Running setup wizard...!C_R!
echo.
"%PYTHON_EXE%" tempo_automation.py --setup

if %errorlevel% neq 0 (
    echo.
    echo ERROR: Setup failed
    pause
    exit /b 1
)

echo.
echo !C_OK![OK]!C_R! Setup complete
echo.

REM ============================================================================
REM Select overhead stories (developers only)
REM ============================================================================

echo !C_BOLD![4/7] Configuring overhead stories...!C_R!
echo.
echo Overhead stories are used for daily default hours (e.g., 2h/day),
echo PTO days, holidays, and days with no active tickets.
echo.
set /p SELECT_OH="Configure overhead stories now? (y/n, default: y): "
if /i "%SELECT_OH%"=="n" (
    echo Skipping overhead story setup
) else (
    "%PYTHON_EXE%" tempo_automation.py --select-overhead
    if !errorlevel! neq 0 (
        echo.
        echo !C_WARN![!]!C_R! Overhead selection skipped or failed
        echo     Skipping overhead story setup
    )
)
echo.

REM ============================================================================
REM Generate wrapper scripts and create scheduled tasks
REM ============================================================================

echo !C_BOLD![5/7] Setting up scheduled tasks...!C_R!
echo.

REM -- Generate _get_month.py helper: returns current YYYY-MM --
REM    Used by wrapper scripts to reliably name log files (no wmic, no delayed expansion)
(
    echo from datetime import date
    echo d = date.today^(^)
    echo print^(f"{d.year}-{d.month:02d}"^)
) > "%SCRIPT_DIR%_get_month.py"

REM -- Generate run_daily.bat with detected Python path --
REM    Log file rotates monthly: daily-timesheet-YYYY-MM.log
(
    echo @echo off
    echo for /f "tokens=*" %%%%i in ^('""%PYTHON_EXE%" "%SCRIPT_DIR%_get_month.py""'^) do set YYYYMM=%%%%i
    echo set LOGFILE=%SCRIPT_DIR%daily-timesheet-%%YYYYMM%%.log
    echo echo ============================================ ^>^> "%%LOGFILE%%"
    echo echo Run: %%date%% %%time%% ^>^> "%%LOGFILE%%"
    echo echo ============================================ ^>^> "%%LOGFILE%%"
    echo "%PYTHONW_EXE%" "%SCRIPT_DIR%confirm_and_run.py"
) > "%SCRIPT_DIR%run_daily.bat"

REM -- Generate run_weekly.bat with detected Python path --
(
    echo @echo off
    echo for /f "tokens=*" %%%%i in ^('""%PYTHON_EXE%" "%SCRIPT_DIR%_get_month.py""'^) do set YYYYMM=%%%%i
    echo set LOGFILE=%SCRIPT_DIR%weekly-log-%%YYYYMM%%.log
    echo echo ============================================ ^>^> "%%LOGFILE%%"
    echo echo Weekly Verify Run: %%date%% %%time%% ^>^> "%%LOGFILE%%"
    echo echo ============================================ ^>^> "%%LOGFILE%%"
    echo "%PYTHON_EXE%" "%SCRIPT_DIR%tempo_automation.py" --verify-week --logfile "%%LOGFILE%%"
) > "%SCRIPT_DIR%run_weekly.bat"

REM -- Generate run_monthly.bat with detected Python path --
(
    echo @echo off
    echo for /f "tokens=*" %%%%i in ^('""%PYTHON_EXE%" "%SCRIPT_DIR%_get_month.py""'^) do set YYYYMM=%%%%i
    echo set LOGFILE=%SCRIPT_DIR%monthly-log-%%YYYYMM%%.log
    echo echo ============================================ ^>^> "%%LOGFILE%%"
    echo echo Run: %%date%% %%time%% ^(Monthly Submit^) ^>^> "%%LOGFILE%%"
    echo echo ============================================ ^>^> "%%LOGFILE%%"
    echo "%PYTHON_EXE%" "%SCRIPT_DIR%tempo_automation.py" --submit --logfile "%%LOGFILE%%"
) > "%SCRIPT_DIR%run_monthly.bat"

echo !C_OK![OK]!C_R! Wrapper scripts generated with detected Python path
echo.

REM Read sync time from config if it exists, otherwise default to 18:00
set SYNC_TIME=18:00
if exist "%SCRIPT_DIR%config.json" (
    for /f "usebackq delims=" %%T in (`powershell -Command "try { $c = Get-Content '%SCRIPT_DIR%config.json' -Raw | ConvertFrom-Json; if ($c.schedule.daily_sync_time) { Write-Output $c.schedule.daily_sync_time } } catch {}"`) do (
        set "SYNC_TIME=%%T"
    )
)
echo Creating daily sync task ^(Mon-Fri at !SYNC_TIME!^)...
schtasks /Create /TN "TempoAutomation-DailySync" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST !SYNC_TIME! /TR "\"%SCRIPT_DIR%run_daily.bat\"" /F >nul 2>&1

if %errorlevel% equ 0 (
    echo !C_OK![OK]!C_R! Daily sync task created ^(weekdays only^)
) else (
    echo !C_FAIL![FAIL]!C_R! Failed to create daily sync task
    echo   You may need to run this as Administrator
)

REM Weekly verification task (Friday at 4:00 PM)
echo Creating weekly verification task ^(Fridays at 4:00 PM^)...
schtasks /Create /TN "TempoAutomation-WeeklyVerify" /SC WEEKLY /D FRI /ST 16:00 /TR "\"%SCRIPT_DIR%run_weekly.bat\"" /F >nul 2>&1

if %errorlevel% equ 0 (
    echo !C_OK![OK]!C_R! Weekly verification task created
) else (
    echo !C_FAIL![FAIL]!C_R! Failed to create weekly verification task
    echo   You may need to run this as Administrator
)

REM Monthly submission task (11:00 PM on last day of each month)
echo Creating monthly submission task ^(last day of month at 11:00 PM^)...
schtasks /Create /TN "TempoAutomation-MonthlySubmit" /SC MONTHLY /MO LASTDAY /M * /ST 23:00 /TR "\"%SCRIPT_DIR%run_monthly.bat\"" /F >nul 2>&1

if %errorlevel% equ 0 (
    echo !C_OK![OK]!C_R! Monthly submission task created
) else (
    echo !C_FAIL![FAIL]!C_R! Failed to create monthly submission task
    echo   You may need to run this as Administrator
)

echo.

REM ============================================================================
REM Tray App Setup (recommended)
REM ============================================================================

echo !C_BOLD![6/7] Setting up System Tray App...!C_R!
echo.
echo The tray app lives in your system tray, shows a notification at your
echo configured sync time, and lets you sync with one click.
echo It will start automatically every time you log in to Windows.
echo.

REM Silent safety-net stop (Phase B already handled upgrades; this catches edge cases)
"%PYTHON_EXE%" "%SCRIPT_DIR%tray_app.py" --stop >nul 2>&1

REM Register auto-start on login
"%PYTHON_EXE%" "%SCRIPT_DIR%tray_app.py" --register

REM Start the tray app now (detached -- no console window, no terminal tab)
echo Starting tray app...
set TRAY_EXTRA=
if "!IS_UPGRADE!"=="1" set TRAY_EXTRA= --upgraded
start "" /B "!PYTHONW_EXE!" "!SCRIPT_DIR!tray_app.py"!TRAY_EXTRA!
timeout /t 3 /nobreak >nul
echo !C_OK![OK]!C_R! Tray app is running in the system tray
echo.

REM ============================================================================
REM Test run
REM ============================================================================

echo !C_BOLD![7/8] Test sync (optional)!C_R!
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
REM Post-install shortfall check
REM ============================================================================

echo !C_BOLD![8/8] Checking for missing hours this month...!C_R!
echo.
"%PYTHON_EXE%" tempo_automation.py --post-install-check
echo.

REM ============================================================================
REM Installation complete
REM ============================================================================

echo.
echo !C_DIM!============================================================!C_R!
echo !C_HEAD![OK] INSTALLATION COMPLETE!!C_R!
echo !C_DIM!============================================================!C_R!
echo.
echo Your automation is now set up and will run automatically:
echo.
echo   Python: %PYTHON_EXE%
echo.
echo   Tray App:
echo     - Starts on Windows login (system tray icon)
echo     - Notifies at your configured sync time (!SYNC_TIME!)
echo     - Right-click for menu: Sync Now, Add PTO, View Schedule, etc.
echo.
echo   Task Scheduler:
echo     - Daily:   Mon-Fri at !SYNC_TIME! (sync via OK/Cancel dialog)
echo     - Weekly:  Fridays at 4:00 PM (verify hours, backfill gaps)
echo     - Monthly: Last day at 11:00 PM (verify + submit timesheet)
echo.
echo Files:
echo   Config:  %INSTALL_DIR%\config.json
echo   Log:     %INSTALL_DIR%\daily-timesheet-YYYY-MM.log  (rotates monthly)
echo   Runtime: %INSTALL_DIR%\tempo_automation.log
echo.
echo ============================================================
echo.
echo Press any key to exit...
pause >nul
endlocal
