#!/bin/sh
# Installs hermes-vision-inline on Linux/macOS.
# Copies the plugin into ~/.hermes/plugins/hermes-vision-inline and enables it.
set -e

SRC="$(cd "$(dirname "$0")" && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
DEST="$HERMES_HOME/plugins/hermes-vision-inline"

mkdir -p "$DEST"
cp "$SRC/__init__.py" "$SRC/plugin.yaml" "$DEST/"
if [ -f "$SRC/README.md" ]; then cp "$SRC/README.md" "$DEST/"; fi
if [ -d "$SRC/docs" ]; then cp -r "$SRC/docs" "$DEST/"; fi
echo "copied plugin to $DEST"

if ! command -v hermes >/dev/null 2>&1; then
    echo "hermes is not on PATH. Run this once it is:"
    echo "  hermes plugins enable hermes-vision-inline"
    exit 0
fi

if hermes plugins enable hermes-vision-inline; then
    echo "enabled. Restart the desktop backend to pick it up:"
    echo "  systemctl --user restart hermes-dashboard.service"
else
    echo "enable failed. Add 'hermes-vision-inline' to plugins.enabled in config.yaml by hand."
fi
