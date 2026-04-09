#!/bin/bash
# ============================================================================
# Tempo Automation - Mac Quick Setup (double-click to run)
# ============================================================================
# This file exists so Mac users can double-click it in Finder.
# .command files open in Terminal.app automatically.
# It clears the quarantine flag and launches install.sh.
# ============================================================================

cd "$(dirname "$0")"

echo ""
echo "============================================================"
echo "Tempo Automation - Mac Quick Setup"
echo "============================================================"
echo ""

# Remove quarantine from all files in this folder
if [ "$(uname)" = "Darwin" ]; then
    echo "Removing macOS quarantine flags..."
    xattr -dr com.apple.quarantine "$(pwd)" 2>/dev/null
    echo "[OK] Quarantine cleared"
    echo ""
fi

# Make scripts executable
chmod +x install.sh run_daily.sh run_weekly.sh run_monthly.sh 2>/dev/null

# Run the installer
bash install.sh
