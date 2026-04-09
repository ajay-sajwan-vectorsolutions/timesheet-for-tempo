#!/bin/bash
# ============================================================================
# Tempo Automation - Mac/Linux Installer
# ============================================================================
# This script will:
# 1. Detect Python installation
# 2. Install dependencies (skip if pre-bundled)
# 3. Run setup wizard
# 4. Configure overhead stories
# 5. Generate wrapper scripts and schedule cron jobs
# 6. Set up system tray app (auto-start on login)
# 7. Optionally run a test sync
# 8. Post-install shortfall check
# ============================================================================

# Note: not using 'set -e' because we have explicit error checks below

echo ""
echo "============================================================"
echo "TEMPO TIMESHEET AUTOMATION - MAC/LINUX INSTALLER"
echo "============================================================"
echo ""

# Get script directory (source location of this installer)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

INSTALL_DIR="$HOME/tempo-timesheet"
IS_UPGRADE=false
OLD_INSTALL_DIR=""

# ============================================================================
# Detect previous installation (multiple methods, mirrors install.bat)
# ============================================================================
# Detection order:
#   Method 1 - LaunchAgent plist path extraction
#   Method 2 - Crontab entry parsing
#   Method 3 - Running process detection
#   Method 4 - Well-known path scan
# ============================================================================

# --- Method 1: LaunchAgent plist ---
PLIST="$HOME/Library/LaunchAgents/com.tempo.trayapp.plist"
if [ -z "$OLD_INSTALL_DIR" ] && [ -f "$PLIST" ]; then
    OLD_TRAY_PATH=$(grep -o '"/.*/tray_app\.py"' "$PLIST" 2>/dev/null | tr -d '"')
    if [ -z "$OLD_TRAY_PATH" ]; then
        # Try without quotes (plist may use <string> tags)
        OLD_TRAY_PATH=$(grep -o '/.*/tray_app\.py' "$PLIST" 2>/dev/null | head -1)
    fi
    if [ -n "$OLD_TRAY_PATH" ]; then
        CAND="$(dirname "$OLD_TRAY_PATH")"
        if [ "$CAND" != "$INSTALL_DIR" ] && [ -f "$CAND/tray_app.py" ]; then
            OLD_INSTALL_DIR="$CAND"
        fi
    fi
fi

# --- Method 2: Crontab entry ---
if [ -z "$OLD_INSTALL_DIR" ]; then
    CRON_PATH=$(crontab -l 2>/dev/null | grep "tempo_automation.py" | head -1 | grep -o '/[^ ]*tempo_automation\.py' | head -1)
    if [ -n "$CRON_PATH" ]; then
        CAND="$(dirname "$CRON_PATH")"
        if [ "$CAND" != "$INSTALL_DIR" ] && [ -f "$CAND/tray_app.py" ]; then
            OLD_INSTALL_DIR="$CAND"
        fi
    fi
    # Also check for wrapper script paths
    if [ -z "$OLD_INSTALL_DIR" ]; then
        CRON_PATH=$(crontab -l 2>/dev/null | grep "run_daily.sh" | head -1 | grep -o '/[^ ]*run_daily\.sh' | head -1)
        if [ -n "$CRON_PATH" ]; then
            CAND="$(dirname "$CRON_PATH")"
            if [ "$CAND" != "$INSTALL_DIR" ] && [ -f "$CAND/tray_app.py" ]; then
                OLD_INSTALL_DIR="$CAND"
            fi
        fi
    fi
fi

# --- Method 3: Running process ---
if [ -z "$OLD_INSTALL_DIR" ]; then
    PROC_PATH=$(pgrep -af "tray_app.py" 2>/dev/null | grep -v grep | head -1 | grep -o '/[^ ]*tray_app\.py' | head -1)
    if [ -n "$PROC_PATH" ]; then
        CAND="$(dirname "$PROC_PATH")"
        if [ "$CAND" != "$INSTALL_DIR" ] && [ -f "$CAND/tray_app.py" ]; then
            OLD_INSTALL_DIR="$CAND"
        fi
    fi
fi

# --- Method 4: Well-known path scan ---
if [ -z "$OLD_INSTALL_DIR" ]; then
    for DIR in \
        "$HOME/Desktop/tempo-timesheet" \
        "$HOME/Documents/tempo-timesheet" \
        "$HOME/Downloads/tempo-timesheet"; do
        if [ -f "$DIR/tray_app.py" ] && [ -f "$DIR/tempo_automation.py" ]; then
            OLD_INSTALL_DIR="$DIR"
            break
        fi
    done
fi

# ============================================================================
# Phase A: Save config to temp before touching anything
# ============================================================================
if [ -n "$OLD_INSTALL_DIR" ]; then
    echo "[INFO] Found previous installation at: $OLD_INSTALL_DIR"
    if [ -f "$OLD_INSTALL_DIR/config.json" ]; then
        cp -f "$OLD_INSTALL_DIR/config.json" "/tmp/_tempo_migrated_config.json"
        echo "[OK] Previous config saved - credentials will be carried over"
    fi
    IS_UPGRADE=true
    echo ""
fi

# ============================================================================
# Phase B: Stop old tray (signal + wait + hard-kill fallback)
# ============================================================================
if [ "$IS_UPGRADE" = true ]; then
    echo "Stopping previous tray app instance..."
    echo "stop" > "$OLD_INSTALL_DIR/_tray_stop.signal"
    sleep 5

    # Hard-kill fallback: find python running tray_app.py from old dir
    KILLED=false
    for PID in $(pgrep -f "tray_app.py" 2>/dev/null); do
        CMDLINE=$(ps -p "$PID" -o args= 2>/dev/null)
        if echo "$CMDLINE" | grep -q "$OLD_INSTALL_DIR"; then
            kill "$PID" 2>/dev/null && KILLED=true
        fi
    done

    if [ "$KILLED" = true ]; then
        echo "[OK] Old tray process force-stopped"
    else
        echo "[OK] Old tray instance stopped gracefully"
    fi
    echo ""
fi

# ============================================================================
# Phase C: Remove all traces of previous installation
# ============================================================================
if [ "$IS_UPGRADE" = true ]; then
    echo "Removing previous installation traces..."

    # C1: Unload and remove LaunchAgent
    if [ -f "$PLIST" ]; then
        launchctl unload "$PLIST" 2>/dev/null
        rm -f "$PLIST"
        echo "  [OK] LaunchAgent removed"
    fi

    # C2: Remove old cron entries
    if crontab -l 2>/dev/null | grep -q "tempo_automation.py\|run_daily.sh\|run_weekly.sh\|run_monthly.sh"; then
        crontab -l 2>/dev/null | grep -v "tempo_automation.py\|run_daily.sh\|run_weekly.sh\|run_monthly.sh\|Tempo Automation" | crontab -
        echo "  [OK] Old cron entries removed"
    fi

    # C3: Remove artifact files from old dir
    rm -f "$OLD_INSTALL_DIR/_tray_stop.signal" 2>/dev/null
    rm -f "$OLD_INSTALL_DIR/.tray_app.lock" 2>/dev/null

    # C4: Remove config backup (prevent stale config bleeding into future installs)
    rm -f "$HOME/.config/TempoAutomation/config.json" 2>/dev/null
    echo "  [OK] Config backup cleared"

    # C5: Delete old folder -- only if it is NOT the new install destination
    if [ "$OLD_INSTALL_DIR" != "$INSTALL_DIR" ]; then
        rm -rf "$OLD_INSTALL_DIR" 2>/dev/null
        if [ $? -eq 0 ]; then
            echo "  [OK] Old installation folder removed: $OLD_INSTALL_DIR"
        else
            echo "  [!] Could not fully remove old folder (files still in use)"
            echo "      Please delete manually: $OLD_INSTALL_DIR"
        fi
    fi

    echo ""
fi

# ============================================================================
# Step 0: Copy files to fixed install location
# ============================================================================
if [ "$IS_UPGRADE" = true ]; then
    echo "Updating files at $INSTALL_DIR..."
else
    echo "Installing files to $INSTALL_DIR..."
fi
mkdir -p "$INSTALL_DIR/assets"

cp -f "$SCRIPT_DIR/tempo_automation.py"  "$INSTALL_DIR/"
cp -f "$SCRIPT_DIR/tray_app.py"          "$INSTALL_DIR/"
cp -f "$SCRIPT_DIR/confirm_and_run.py"   "$INSTALL_DIR/"
cp -f "$SCRIPT_DIR/config_template.json" "$INSTALL_DIR/"
cp -f "$SCRIPT_DIR/requirements.txt"     "$INSTALL_DIR/"
# Copy shell wrapper templates (used as base for generation)
[ -f "$SCRIPT_DIR/run_daily.sh" ]   && cp -f "$SCRIPT_DIR/run_daily.sh"   "$INSTALL_DIR/"
[ -f "$SCRIPT_DIR/run_weekly.sh" ]  && cp -f "$SCRIPT_DIR/run_weekly.sh"  "$INSTALL_DIR/"
[ -f "$SCRIPT_DIR/run_monthly.sh" ] && cp -f "$SCRIPT_DIR/run_monthly.sh" "$INSTALL_DIR/"
[ -f "$SCRIPT_DIR/assets/favicon.ico" ] && cp -f "$SCRIPT_DIR/assets/favicon.ico" "$INSTALL_DIR/assets/"
[ -d "$SCRIPT_DIR/lib" ] && cp -rf "$SCRIPT_DIR/lib" "$INSTALL_DIR/"

# Redefine SCRIPT_DIR to install location; all subsequent steps use this
SCRIPT_DIR="$INSTALL_DIR"
cd "$INSTALL_DIR"
echo "[OK] Files installed to $INSTALL_DIR"
echo ""

# ============================================================================
# [1/8] Detect Python
# ============================================================================

echo "[1/8] Detecting Python..."

PYTHON_PATH=""

# Check 1: python3 in PATH
if command -v python3 &> /dev/null; then
    PYTHON_PATH="$(command -v python3)"
fi

# Check 2: python in PATH (verify it is Python 3.x)
if [ -z "$PYTHON_PATH" ] && command -v python &> /dev/null; then
    PY_VER=$(python --version 2>&1)
    if echo "$PY_VER" | grep -q "Python 3"; then
        PYTHON_PATH="$(command -v python)"
    fi
fi

# Check 3: Homebrew paths (macOS)
if [ -z "$PYTHON_PATH" ] && [ "$(uname)" = "Darwin" ]; then
    for BREW_PY in /opt/homebrew/bin/python3 /usr/local/bin/python3; do
        if [ -x "$BREW_PY" ]; then
            PYTHON_PATH="$BREW_PY"
            break
        fi
    done
fi

if [ -z "$PYTHON_PATH" ]; then
    echo ""
    echo "ERROR: Python 3 is not installed or not in PATH"
    echo ""
    if [ "$(uname)" = "Darwin" ]; then
        echo "  Install via Homebrew: brew install python3"
        echo "  Or download from: https://www.python.org/downloads/"
    else
        echo "  Install: sudo apt-get install python3 python3-pip"
    fi
    echo ""
    exit 1
fi

echo "[OK] Found Python: $PYTHON_PATH"
"$PYTHON_PATH" --version
echo ""

# ============================================================================
# [2/8] Install dependencies (skip if pre-bundled)
# ============================================================================

echo "[2/8] Installing Python dependencies..."
echo ""

if [ -d "$SCRIPT_DIR/lib" ]; then
    echo "[OK] Pre-bundled lib/ directory found -- skipping pip install"
else
    "$PYTHON_PATH" -m pip install --upgrade pip --user 2>/dev/null || "$PYTHON_PATH" -m pip install --upgrade pip
    "$PYTHON_PATH" -m pip install -r requirements.txt --user 2>/dev/null || "$PYTHON_PATH" -m pip install -r requirements.txt

    if [ $? -ne 0 ]; then
        echo ""
        echo "ERROR: Failed to install dependencies"
        exit 1
    fi
    echo "[OK] Dependencies installed (requests, holidays, pystray, Pillow, keyring)"
fi
echo ""

# ============================================================================
# Phase D: Restore previous config into new install location
# Priority: 1) migrated from old folder  2) config backup
# ============================================================================
if [ ! -f "$SCRIPT_DIR/config.json" ]; then
    if [ -f "/tmp/_tempo_migrated_config.json" ]; then
        cp -f "/tmp/_tempo_migrated_config.json" "$SCRIPT_DIR/config.json"
        rm -f "/tmp/_tempo_migrated_config.json"
        echo "[OK] Previous config restored from old installation - wizard will skip credential prompts"
        echo ""
    elif [ "$IS_UPGRADE" = true ] && [ -f "$HOME/.config/TempoAutomation/config.json" ]; then
        cp -f "$HOME/.config/TempoAutomation/config.json" "$SCRIPT_DIR/config.json"
        echo "[OK] Previous config restored from backup - wizard will skip credential prompts"
        echo ""
    fi
fi

# Set secure permissions on config if it exists
[ -f "$SCRIPT_DIR/config.json" ] && chmod 600 "$SCRIPT_DIR/config.json"

# ============================================================================
# [3/8] Run setup wizard
# ============================================================================

echo "[3/8] Running setup wizard..."
echo ""

"$PYTHON_PATH" "$SCRIPT_DIR/tempo_automation.py" --setup

if [ $? -ne 0 ]; then
    echo ""
    echo "ERROR: Setup failed"
    exit 1
fi

echo ""
echo "[OK] Setup complete"
echo ""

# ============================================================================
# [4/8] Configure overhead stories (developers only)
# ============================================================================

echo "[4/8] Configuring overhead stories..."
echo ""
echo "Overhead stories are used for daily default hours (e.g., 2h/day),"
echo "PTO days, holidays, and days with no active tickets."
echo ""
read -p "Configure overhead stories now? (y/n, default: y): " SELECT_OH

if [ "$SELECT_OH" = "n" ] || [ "$SELECT_OH" = "N" ]; then
    echo "Skipped. You can configure later: $PYTHON_PATH tempo_automation.py --select-overhead"
else
    "$PYTHON_PATH" "$SCRIPT_DIR/tempo_automation.py" --select-overhead || {
        echo ""
        echo "[!] Overhead selection skipped or failed"
        echo "    You can configure later: $PYTHON_PATH tempo_automation.py --select-overhead"
    }
fi
echo ""

# ============================================================================
# [5/8] Generate wrapper scripts and schedule cron jobs
# ============================================================================

echo "[5/8] Setting up scheduled tasks..."
echo ""

# -- Generate _get_month.py helper: returns current YYYY-MM --
cat > "$SCRIPT_DIR/_get_month.py" << 'PYEOF'
from datetime import date
d = date.today()
print(f"{d.year}-{d.month:02d}")
PYEOF

# -- Generate run_daily.sh with detected Python path --
cat > "$SCRIPT_DIR/run_daily.sh" << SHEOF
#!/bin/bash
SCRIPT_DIR="$SCRIPT_DIR"
PYTHON_EXE="$PYTHON_PATH"

MONTH=\$("\$PYTHON_EXE" "\$SCRIPT_DIR/_get_month.py" 2>/dev/null || date +%Y-%m)
LOGFILE="\$SCRIPT_DIR/daily-timesheet-\${MONTH}.log"

echo "============================================" >> "\$LOGFILE"
echo "Run: \$(date '+%Y-%m-%d %H:%M:%S')" >> "\$LOGFILE"
echo "============================================" >> "\$LOGFILE"

"\$PYTHON_EXE" "\$SCRIPT_DIR/confirm_and_run.py"
SHEOF

# -- Generate run_weekly.sh with detected Python path --
cat > "$SCRIPT_DIR/run_weekly.sh" << SHEOF
#!/bin/bash
SCRIPT_DIR="$SCRIPT_DIR"
PYTHON_EXE="$PYTHON_PATH"

MONTH=\$("\$PYTHON_EXE" "\$SCRIPT_DIR/_get_month.py" 2>/dev/null || date +%Y-%m)
LOGFILE="\$SCRIPT_DIR/daily-timesheet-\${MONTH}.log"

echo "============================================" >> "\$LOGFILE"
echo "Weekly Verify Run: \$(date '+%Y-%m-%d %H:%M:%S')" >> "\$LOGFILE"
echo "============================================" >> "\$LOGFILE"

"\$PYTHON_EXE" "\$SCRIPT_DIR/tempo_automation.py" --verify-week --logfile "\$LOGFILE"
SHEOF

# -- Generate run_monthly.sh with detected Python path --
cat > "$SCRIPT_DIR/run_monthly.sh" << SHEOF
#!/bin/bash
SCRIPT_DIR="$SCRIPT_DIR"
PYTHON_EXE="$PYTHON_PATH"

MONTH=\$("\$PYTHON_EXE" "\$SCRIPT_DIR/_get_month.py" 2>/dev/null || date +%Y-%m)
LOGFILE="\$SCRIPT_DIR/daily-timesheet-\${MONTH}.log"

echo "============================================" >> "\$LOGFILE"
echo "Monthly Submit Run: \$(date '+%Y-%m-%d %H:%M:%S')" >> "\$LOGFILE"
echo "============================================" >> "\$LOGFILE"

"\$PYTHON_EXE" "\$SCRIPT_DIR/tempo_automation.py" --submit --logfile "\$LOGFILE"
SHEOF

chmod +x "$SCRIPT_DIR/run_daily.sh" "$SCRIPT_DIR/run_weekly.sh" "$SCRIPT_DIR/run_monthly.sh"
echo "[OK] Wrapper scripts generated with detected Python path"
echo ""

# Read sync time from config if it exists, otherwise default to 18:00
SYNC_TIME="18:00"
if [ -f "$SCRIPT_DIR/config.json" ]; then
    CFG_TIME=$("$PYTHON_PATH" -c "import json; c=json.load(open('$SCRIPT_DIR/config.json')); print(c.get('schedule',{}).get('daily_sync_time',''))" 2>/dev/null)
    if [ -n "$CFG_TIME" ]; then
        SYNC_TIME="$CFG_TIME"
    fi
fi
SYNC_HOUR=$(echo "$SYNC_TIME" | cut -d: -f1)
SYNC_MIN=$(echo "$SYNC_TIME" | cut -d: -f2)

# Initialize crontab if empty
if ! crontab -l &> /dev/null; then
    echo "# Tempo Automation Cron Jobs" | crontab -
fi

# Backup existing crontab
BACKUP_FILE="/tmp/tempo_crontab_backup_$$.txt"
crontab -l > "$BACKUP_FILE" 2>/dev/null || true
echo "[OK] Backed up existing crontab to $BACKUP_FILE"

# Remove any existing Tempo Automation entries, then add new ones
NEW_CRON="/tmp/tempo_crontab_new_$$.txt"
crontab -l 2>/dev/null | grep -v "tempo_automation.py\|run_daily.sh\|run_weekly.sh\|run_monthly.sh\|Tempo Automation" > "$NEW_CRON" || true

# Daily sync (Monday-Friday) using wrapper script
echo "" >> "$NEW_CRON"
echo "# Tempo Automation - Daily sync at $SYNC_TIME (Mon-Fri)" >> "$NEW_CRON"
echo "$SYNC_MIN $SYNC_HOUR * * 1-5 \"$SCRIPT_DIR/run_daily.sh\"" >> "$NEW_CRON"

# Weekly verification (Friday at 4:00 PM) using wrapper script
echo "" >> "$NEW_CRON"
echo "# Tempo Automation - Weekly verify (Fridays at 4:00 PM)" >> "$NEW_CRON"
echo "0 16 * * 5 \"$SCRIPT_DIR/run_weekly.sh\"" >> "$NEW_CRON"

# Monthly submission at 11:00 PM on last day of month using wrapper script
echo "" >> "$NEW_CRON"
echo "# Tempo Automation - Monthly submission (last day of month at 11:00 PM)" >> "$NEW_CRON"
if [ "$(uname)" = "Darwin" ]; then
    # macOS: BSD date syntax
    echo "0 23 28-31 * * [ \$(date -v+1d +\\%d) -eq 1 ] && \"$SCRIPT_DIR/run_monthly.sh\"" >> "$NEW_CRON"
else
    # Linux: GNU date syntax
    echo "0 23 28-31 * * [ \$(date -d tomorrow +\\%d) -eq 1 ] && \"$SCRIPT_DIR/run_monthly.sh\"" >> "$NEW_CRON"
fi

# Install new crontab
if crontab "$NEW_CRON"; then
    echo "[OK] Cron jobs created:"
    echo "     - Daily:   Mon-Fri at $SYNC_TIME (sync via wrapper)"
    echo "     - Weekly:  Fridays at 4:00 PM (verify hours, backfill gaps)"
    echo "     - Monthly: Last day at 11:00 PM (submit for approval)"
    rm -f "$NEW_CRON"
else
    echo "[FAIL] Failed to create cron jobs"
    echo "  Restoring backup..."
    crontab "$BACKUP_FILE"
    exit 1
fi

echo ""

# ============================================================================
# [6/8] Set up system tray app
# ============================================================================

echo "[6/8] Setting up System Tray App..."
echo ""
echo "The tray app lives in your menu bar, shows a notification at your"
echo "configured sync time, and lets you sync with one click."
echo "It will start automatically every time you log in."
echo ""

# Stop any existing tray app instance
"$PYTHON_PATH" "$SCRIPT_DIR/tray_app.py" --stop 2>/dev/null || true
sleep 2

# Register auto-start on login (LaunchAgent on Mac)
"$PYTHON_PATH" "$SCRIPT_DIR/tray_app.py" --register

# Start the tray app now (background, no console)
echo "Starting tray app..."
if [ "$IS_UPGRADE" = true ]; then
    nohup "$PYTHON_PATH" "$SCRIPT_DIR/tray_app.py" --upgraded > /dev/null 2>&1 &
else
    nohup "$PYTHON_PATH" "$SCRIPT_DIR/tray_app.py" > /dev/null 2>&1 &
fi
sleep 3
echo "[OK] Tray app is running in the menu bar"
echo ""
echo "NOTE: The tray app and cron jobs can coexist safely."
echo "      The sync is idempotent (re-running overwrites previous entries)."

echo ""

# ============================================================================
# [7/8] Test sync (optional)
# ============================================================================

echo "[7/8] Test sync (optional)"
echo ""
echo "Would you like to test the automation now?"
echo "This will sync today's timesheet to verify everything works."
echo ""
read -p "Run test? (y/n): " TEST_RUN

if [ "$TEST_RUN" = "y" ] || [ "$TEST_RUN" = "Y" ]; then
    echo ""
    echo "Running test sync..."
    echo ""
    "$PYTHON_PATH" "$SCRIPT_DIR/tempo_automation.py"
fi

echo ""

# ============================================================================
# [8/8] Post-install shortfall check
# ============================================================================

echo "[8/8] Checking for missing hours this month..."
echo ""
"$PYTHON_PATH" "$SCRIPT_DIR/tempo_automation.py" --post-install-check
echo ""

# ============================================================================
# Installation complete
# ============================================================================

echo ""
echo "============================================================"
echo "[OK] INSTALLATION COMPLETE!"
echo "============================================================"
echo ""
echo "Your automation is now set up and will run automatically:"
echo ""
echo "  Python: $PYTHON_PATH"
echo ""
echo "  Tray App:"
echo "    - Starts on login (menu bar icon)"
echo "    - Notifies at your configured sync time ($SYNC_TIME)"
echo "    - Right-click for menu: Sync Now, Add PTO, View Schedule, etc."
echo ""
echo "  Cron Jobs:"
echo "    - Daily:   Mon-Fri at $SYNC_TIME (sync via wrapper)"
echo "    - Weekly:  Fridays at 4:00 PM (verify hours, backfill gaps)"
echo "    - Monthly: Last day at 11:00 PM (submit for approval)"
echo ""
echo "Files:"
echo "  Config:  $SCRIPT_DIR/config.json"
echo "  Log:     $SCRIPT_DIR/daily-timesheet-YYYY-MM.log  (rotates monthly)"
echo "  Runtime: $SCRIPT_DIR/tempo_automation.log"
echo ""
echo "Manual commands:"
echo "  $PYTHON_PATH tempo_automation.py              (sync today)"
echo "  $PYTHON_PATH tempo_automation.py --date DATE  (sync specific date)"
echo "  $PYTHON_PATH tempo_automation.py --verify-week (verify this week)"
echo "  $PYTHON_PATH tempo_automation.py --submit     (submit monthly)"
echo "  $PYTHON_PATH tempo_automation.py --show-schedule (view calendar)"
echo "  $PYTHON_PATH tempo_automation.py --manage     (schedule menu)"
echo ""
echo "Uninstall:"
echo "  $PYTHON_PATH tray_app.py --unregister"
echo "  crontab -e  (remove lines containing 'Tempo Automation')"
echo "  Then delete this folder."
echo ""
echo "============================================================"
echo ""
