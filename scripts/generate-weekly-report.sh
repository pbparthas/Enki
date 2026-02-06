#!/bin/bash
# Enki Weekly Report Generator
# Run via cron: 0 9 * * 1 ~/.enki/scripts/generate-weekly-report.sh
#
# This script generates the weekly Ereshkigal pattern review report
# and optionally sends a desktop notification.

set -euo pipefail

# Configuration
ENKI_DIR="${ENKI_DIR:-$HOME/.enki}"
REVIEWS_DIR="${ENKI_DIR}/reviews"
DATE=$(date +%Y-%m-%d)
REPORT_FILE="${REVIEWS_DIR}/weekly-${DATE}.md"

# Ensure directories exist
mkdir -p "${REVIEWS_DIR}"

# Check if enki CLI is available
if ! command -v enki &> /dev/null; then
    echo "Error: enki CLI not found in PATH" >&2
    exit 1
fi

# Generate the report
echo "Generating weekly report for ${DATE}..."
enki report weekly --output "${REPORT_FILE}"

if [[ -f "${REPORT_FILE}" ]]; then
    echo "Report saved to: ${REPORT_FILE}"

    # Get summary for notification
    SUMMARY=$(enki report weekly --summary 2>/dev/null || echo "Weekly report ready")

    # Send desktop notification if notify-send is available
    if command -v notify-send &> /dev/null; then
        notify-send "Enki Weekly Review Ready" "${SUMMARY}" --icon=dialog-information
    fi

    # On macOS, use osascript (P1-18: pass via heredoc to prevent injection)
    if command -v osascript &> /dev/null; then
        osascript - "${SUMMARY}" <<'APPLESCRIPT'
on run argv
    display notification (item 1 of argv) with title "Enki Weekly Review Ready"
end run
APPLESCRIPT
    fi

    echo "Done."
else
    echo "Error: Failed to generate report" >&2
    exit 1
fi

# Cleanup old reports (keep last 12 weeks)
echo "Cleaning up old reports..."
find "${REVIEWS_DIR}" -name "weekly-*.md" -type f -mtime +90 -delete 2>/dev/null || true

echo "Weekly report generation complete."
