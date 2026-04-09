@echo off
REM ============================================================================
REM Tempo Automation - Distribution Builder
REM ============================================================================
REM Builds distribution zip files for team members.
REM
REM Options:
REM   [1] Windows + Embedded Python  (~40-50MB zip, no Python needed)
REM   [2] Windows (Python required)  (~200KB zip)
REM   [3] Mac/Linux                  (~200KB zip)
REM   [A] Build all three
REM
REM First build of option 1 downloads Python + deps (~1-2 min).
REM Subsequent builds use cached python/ and lib/ (~5 sec).
REM ============================================================================

setlocal enabledelayedexpansion

set VERSION=4.0
set PYTHON_EMBED_VER=3.12.8
set PYTHON_EMBED_URL=https://www.python.org/ftp/python/%PYTHON_EMBED_VER%/python-%PYTHON_EMBED_VER%-embed-amd64.zip
set PYTHON_PTH=python312._pth
set GET_PIP_URL=https://bootstrap.pypa.io/get-pip.py

set SCRIPT_DIR=%~dp0
set BUILD_TMP=%SCRIPT_DIR%build_tmp
set DIST_DIR=%SCRIPT_DIR%dist

REM Generate timestamp (YYYYMMDD-HHMM)
for /f %%I in ('powershell -Command "Get-Date -Format yyyyMMdd-HHmm"') do set TIMESTAMP=%%I

echo.
echo ============================================================
echo TEMPO AUTOMATION - DISTRIBUTION BUILDER  v%VERSION%
echo ============================================================
echo.
echo   [1] Windows + Embedded Python  (~40-50MB zip)
echo   [2] Windows (Python required)  (~200KB zip)
echo   [3] Mac/Linux                  (~200KB zip)
echo   [A] Build all three
echo   [Q] Quit
echo.
set /p BUILD_CHOICE="Select option: "

if /i "%BUILD_CHOICE%"=="1" goto :build_win_full
if /i "%BUILD_CHOICE%"=="2" goto :build_win_lite
if /i "%BUILD_CHOICE%"=="3" goto :build_mac
if /i "%BUILD_CHOICE%"=="A" goto :build_all
if /i "%BUILD_CHOICE%"=="Q" goto :done
echo [FAIL] Invalid choice
goto :done

:build_all
call :build_win_full
call :build_win_lite
call :build_mac
echo.
echo ============================================================
echo [OK] All 3 distribution zips built in dist\
echo ============================================================
dir /b "%DIST_DIR%\*.zip" 2>nul
goto :done

REM ============================================================================
REM Option 1: Windows + Embedded Python
REM ============================================================================
:build_win_full
echo.
echo [->] Building Windows Full (with embedded Python %PYTHON_EMBED_VER%)...
echo.

set STAGE=%BUILD_TMP%\dist_win_full
set PYTHON_CACHE=%BUILD_TMP%\python_cache
set LIB_CACHE=%BUILD_TMP%\lib_cache

REM Clean previous staging area
if exist "%STAGE%" rmdir /s /q "%STAGE%"
mkdir "%STAGE%"

REM -- Check if cached python/ and lib/ exist from a previous build --
if exist "%PYTHON_CACHE%\python.exe" if exist "%LIB_CACHE%\requests" (
    echo [1/3] Using cached python\ and lib\ from previous build...
    echo       [To rebuild: delete build_tmp\python_cache\ and build_tmp\lib_cache\]
    robocopy "%PYTHON_CACHE%" "%STAGE%\python" /E /NFL /NDL /NJH /NJS >nul
    robocopy "%LIB_CACHE%" "%STAGE%\lib" /E /NFL /NDL /NJH /NJS >nul
    echo [OK] Copied from cache
    echo.

    REM -- Copy distribution files --
    echo [2/3] Copying distribution files...
    call :copy_common_files "%STAGE%"
    call :copy_windows_files "%STAGE%"
    echo [OK] Files copied

    REM -- Create zip --
    echo [3/3] Creating zip...
    if not exist "%DIST_DIR%" mkdir "%DIST_DIR%"
    set ZIP_NAME=TempoAutomation-v%VERSION%-Windows-Full-%TIMESTAMP%.zip
    if exist "%DIST_DIR%\!ZIP_NAME!" del "%DIST_DIR%\!ZIP_NAME!"
    powershell -Command "Compress-Archive -Path '%STAGE%\*' -DestinationPath '%DIST_DIR%\!ZIP_NAME!'"
    echo.
    echo [OK] Built: dist\!ZIP_NAME!
    for %%F in ("%DIST_DIR%\!ZIP_NAME!") do echo     Size: %%~zF bytes
    echo.
    exit /b 0
)

REM -- First-time build: download, extract, install deps via embedded pip --
if not exist "%BUILD_TMP%" mkdir "%BUILD_TMP%"

echo [1/8] Downloading embedded Python %PYTHON_EMBED_VER%...
if not exist "%BUILD_TMP%\python-embed.zip" (
    curl.exe -L -o "%BUILD_TMP%\python-embed.zip" "%PYTHON_EMBED_URL%"
    if !errorlevel! neq 0 (
        echo [FAIL] Download failed. Check internet connection.
        if exist "%BUILD_TMP%\python-embed.zip" del "%BUILD_TMP%\python-embed.zip"
        exit /b 1
    )
) else (
    echo [OK] Using cached python-embed.zip
)

echo [2/8] Extracting embedded Python...
mkdir "%STAGE%\python"
powershell -Command "Expand-Archive -Path '%BUILD_TMP%\python-embed.zip' -DestinationPath '%STAGE%\python' -Force"
echo [OK] Extracted

echo [3/8] Enabling pip support in embedded Python...
set PTH_FILE=%STAGE%\python\%PYTHON_PTH%
if not exist "%PTH_FILE%" (
    echo [FAIL] %PYTHON_PTH% not found
    exit /b 1
)
REM Only uncomment import site -- keep original paths so pip installs locally
powershell -Command "(Get-Content '%PTH_FILE%') -replace '^#import site', 'import site' | Set-Content '%PTH_FILE%'"
echo [OK] Enabled import site

echo [4/8] Downloading get-pip.py...
if not exist "%BUILD_TMP%\get-pip.py" (
    curl.exe -L -o "%BUILD_TMP%\get-pip.py" "%GET_PIP_URL%"
)
echo [OK] Ready

echo [5/8] Installing pip into embedded Python...
"%STAGE%\python\python.exe" "%BUILD_TMP%\get-pip.py" --no-warn-script-location >nul 2>&1
if !errorlevel! neq 0 (
    echo [FAIL] pip installation failed
    exit /b 1
)
echo [OK] pip installed

echo [6/8] Installing dependencies into lib\...
mkdir "%STAGE%\lib"
"%STAGE%\python\python.exe" -m pip install -r "%SCRIPT_DIR%requirements.txt" --target "%STAGE%\lib" --no-warn-script-location --quiet
if !errorlevel! neq 0 (
    echo [FAIL] Dependency installation failed
    exit /b 1
)
echo [OK] Dependencies installed

echo [7/8] Configuring paths and cleaning up pip...
REM Write final _pth (adds ../lib and ../ so deps + scripts are importable)
(
    echo python312.zip
    echo ../lib
    echo ../
    echo .
    echo import site
) > "%PTH_FILE%"
REM Remove pip/setuptools from embedded Python (saves ~5MB, not needed at runtime)
if exist "%STAGE%\python\Lib" rmdir /s /q "%STAGE%\python\Lib"
if exist "%STAGE%\python\Scripts" rmdir /s /q "%STAGE%\python\Scripts"
echo [OK] Paths configured, pip removed

REM -- Cache python/ and lib/ for future builds --
echo [8/8] Building zip and caching for future builds...
if exist "%PYTHON_CACHE%" rmdir /s /q "%PYTHON_CACHE%"
if exist "%LIB_CACHE%" rmdir /s /q "%LIB_CACHE%"
robocopy "%STAGE%\python" "%PYTHON_CACHE%" /E /NFL /NDL /NJH /NJS >nul
robocopy "%STAGE%\lib" "%LIB_CACHE%" /E /NFL /NDL /NJH /NJS >nul

call :copy_common_files "%STAGE%"
call :copy_windows_files "%STAGE%"

if not exist "%DIST_DIR%" mkdir "%DIST_DIR%"
set ZIP_NAME=TempoAutomation-v%VERSION%-Windows-Full-%TIMESTAMP%.zip
if exist "%DIST_DIR%\%ZIP_NAME%" del "%DIST_DIR%\%ZIP_NAME%"
powershell -Command "Compress-Archive -Path '%STAGE%\*' -DestinationPath '%DIST_DIR%\%ZIP_NAME%'"
echo.
echo [OK] Built: dist\%ZIP_NAME%
for %%F in ("%DIST_DIR%\%ZIP_NAME%") do echo     Size: %%~zF bytes
echo [OK] Cached python\ and lib\ for future builds
echo.
exit /b 0

REM ============================================================================
REM Option 2: Windows (Python required)
REM ============================================================================
:build_win_lite
echo.
echo [->] Building Windows Lite (system Python required)...
echo.

set STAGE=%BUILD_TMP%\dist_win_lite

if exist "%STAGE%" rmdir /s /q "%STAGE%"
mkdir "%STAGE%"

echo [1/2] Copying distribution files...
call :copy_common_files "%STAGE%"
call :copy_windows_files "%STAGE%"
echo [OK] Files copied

echo [2/2] Creating zip...
if not exist "%DIST_DIR%" mkdir "%DIST_DIR%"
set ZIP_NAME=TempoAutomation-v%VERSION%-Windows-Lite-%TIMESTAMP%.zip
if exist "%DIST_DIR%\%ZIP_NAME%" del "%DIST_DIR%\%ZIP_NAME%"
powershell -Command "Compress-Archive -Path '%STAGE%\*' -DestinationPath '%DIST_DIR%\%ZIP_NAME%'"
echo.
echo [OK] Built: dist\%ZIP_NAME%
for %%F in ("%DIST_DIR%\%ZIP_NAME%") do echo     Size: %%~zF bytes
echo.
exit /b 0

REM ============================================================================
REM Option 3: Mac/Linux
REM ============================================================================
:build_mac
echo.
echo [->] Building Mac/Linux distribution...
echo.

set STAGE=%BUILD_TMP%\dist_mac

if exist "%STAGE%" rmdir /s /q "%STAGE%"
mkdir "%STAGE%"

echo [1/2] Copying distribution files...
REM Mac zip: only essential runtime files (no docs, no examples, no README)
copy "%SCRIPT_DIR%tempo_automation.py"  "%STAGE%\" >nul
copy "%SCRIPT_DIR%tray_app.py"          "%STAGE%\" >nul
copy "%SCRIPT_DIR%org_holidays.json"    "%STAGE%\" >nul
copy "%SCRIPT_DIR%requirements.txt"     "%STAGE%\" >nul
if not exist "%STAGE%\assets" mkdir "%STAGE%\assets"
copy "%SCRIPT_DIR%assets\favicon.ico"   "%STAGE%\assets\" >nul
REM Mac-specific files
copy "%SCRIPT_DIR%install.sh"           "%STAGE%\" >nul
copy "%SCRIPT_DIR%setup.command"        "%STAGE%\" >nul
copy "%SCRIPT_DIR%confirm_and_run.py"   "%STAGE%\" >nul
copy "%SCRIPT_DIR%run_daily.sh"         "%STAGE%\" >nul
copy "%SCRIPT_DIR%run_weekly.sh"        "%STAGE%\" >nul
copy "%SCRIPT_DIR%run_monthly.sh"       "%STAGE%\" >nul
echo [OK] Files copied (runtime + Mac scripts only, no docs/examples)

echo [2/2] Creating zip...
if not exist "%DIST_DIR%" mkdir "%DIST_DIR%"
set ZIP_NAME=TempoAutomation-v%VERSION%-Mac-%TIMESTAMP%.zip
if exist "%DIST_DIR%\%ZIP_NAME%" del "%DIST_DIR%\%ZIP_NAME%"
powershell -Command "Compress-Archive -Path '%STAGE%\*' -DestinationPath '%DIST_DIR%\%ZIP_NAME%'"
echo.
echo [OK] Built: dist\%ZIP_NAME%
for %%F in ("%DIST_DIR%\%ZIP_NAME%") do echo     Size: %%~zF bytes
echo.
exit /b 0

REM ============================================================================
REM Helper: Copy common files (all platforms)
REM ============================================================================
:copy_common_files
set DEST=%~1

copy "%SCRIPT_DIR%tempo_automation.py" "%DEST%\" >nul
copy "%SCRIPT_DIR%tray_app.py" "%DEST%\" >nul
copy "%SCRIPT_DIR%config_template.json" "%DEST%\" >nul
copy "%SCRIPT_DIR%org_holidays.json" "%DEST%\" >nul
copy "%SCRIPT_DIR%requirements.txt" "%DEST%\" >nul
copy "%SCRIPT_DIR%README.md" "%DEST%\" >nul

if not exist "%DEST%\assets" mkdir "%DEST%\assets"
copy "%SCRIPT_DIR%assets\favicon.ico" "%DEST%\assets\" >nul

if not exist "%DEST%\examples" mkdir "%DEST%\examples"
copy "%SCRIPT_DIR%examples\*.json" "%DEST%\examples\" >nul

if not exist "%DEST%\docs\guides" mkdir "%DEST%\docs\guides"
copy "%SCRIPT_DIR%docs\guides\*.md" "%DEST%\docs\guides\" >nul
exit /b 0

REM ============================================================================
REM Helper: Copy Windows-specific files
REM ============================================================================
:copy_windows_files
set DEST=%~1

copy "%SCRIPT_DIR%confirm_and_run.py" "%DEST%\" >nul
copy "%SCRIPT_DIR%install.bat" "%DEST%\" >nul
copy "%SCRIPT_DIR%run_daily.bat" "%DEST%\" >nul
copy "%SCRIPT_DIR%run_weekly.bat" "%DEST%\" >nul
copy "%SCRIPT_DIR%run_monthly.bat" "%DEST%\" >nul
exit /b 0

:done
echo.
pause
endlocal
