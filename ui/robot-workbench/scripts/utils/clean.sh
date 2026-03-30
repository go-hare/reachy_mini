#!/bin/bash

# Project cleanup script
# Removes all build files and artifacts

set -e

echo "🧹 Cleaning project..."

# Build directories to remove
DIRS_TO_CLEAN=(
  "dist"                          # Frontend Vite build
  "src-tauri/target"              # Rust Tauri build
  "src-tauri/gen"                 # Tauri generated files
  "src-tauri/binaries"            # Sidecar (Python venv, uv, cpython)
  "uv-wrapper/target"            # Rust uv-wrapper build
  "scripts/__pycache__"          # Python cache
  "test-updates"                  # Test update files
)

# macOS Application Support data (daemon apps, venvs, metadata)
APP_SUPPORT_DIR="$HOME/Library/Application Support/com.pollen-robotics.reachy-mini"

# Temporary files to remove
FILES_TO_CLEAN=(
  "*.log"                         # Log files
  "daemon-develop-test.log"       # Specific log file
)

# Remove directories
for dir in "${DIRS_TO_CLEAN[@]}"; do
  if [ -d "$dir" ] || [ -f "$dir" ]; then
    echo "  ❌ Removing $dir"
    rm -rf "$dir"
  else
    echo "  ⏭️  $dir does not exist (already clean)"
  fi
done

# Remove files
for pattern in "${FILES_TO_CLEAN[@]}"; do
  if ls $pattern 1> /dev/null 2>&1; then
    echo "  ❌ Removing $pattern"
    rm -f $pattern
  fi
done

# Remove Application Support data (installed apps, venvs, metadata)
if [ -d "$APP_SUPPORT_DIR" ]; then
  echo "  ❌ Removing Application Support data: $APP_SUPPORT_DIR"
  rm -rf "$APP_SUPPORT_DIR"
else
  echo "  ⏭️  Application Support data does not exist (already clean)"
fi

echo "✅ Cleanup complete!"
echo ""
echo "💡 To reinstall dependencies: yarn install"
echo "💡 To rebuild: yarn build"

