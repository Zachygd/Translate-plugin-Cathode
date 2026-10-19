#!/usr/bin/env bash
# Cathode Translate uninstaller
# Removes the app, its menu/autostart entries, the polkit rule and the speed
# settings. Ollama itself and your downloaded models are left alone.
#
# Usage: ./uninstall.sh [--purge]
#   --purge   also delete your settings and translation history

set -euo pipefail

APP_ID="cathode-translate"
PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1
[ "$(id -u)" -eq 0 ] && { echo "Run as your normal user, not with sudo."; exit 1; }

echo "Removing Cathode Translate..."
pkill -f "$HOME/.local/bin/$APP_ID( |$)" 2>/dev/null || true

rm -f "$HOME/.local/bin/$APP_ID" \
      "$HOME/.local/share/applications/$APP_ID.desktop" \
      "$HOME/.config/autostart/$APP_ID.desktop" \
      "${XDG_RUNTIME_DIR:-/tmp}/$APP_ID.sock" \
      "${XDG_RUNTIME_DIR:-/tmp}/$APP_ID-shot.png"
command -v kbuildsycoca6 >/dev/null && kbuildsycoca6 >/dev/null 2>&1 || true

NEED_RELOAD=0
if [ -e "/etc/polkit-1/rules.d/50-$APP_ID.rules" ]; then
    sudo rm -f "/etc/polkit-1/rules.d/50-$APP_ID.rules"
fi
if [ -e "/etc/systemd/system/ollama.service.d/20-$APP_ID-speed.conf" ]; then
    sudo rm -f "/etc/systemd/system/ollama.service.d/20-$APP_ID-speed.conf"
    NEED_RELOAD=1
fi
if [ "$NEED_RELOAD" = 1 ]; then
    sudo systemctl daemon-reload
    systemctl is-active --quiet ollama && sudo systemctl restart ollama || true
fi

if [ "$PURGE" = 1 ]; then
    rm -rf "$HOME/.config/$APP_ID" "$HOME/.local/share/$APP_ID"
    echo "Deleted settings and history."
else
    echo "Kept settings (~/.config/$APP_ID) and history (~/.local/share/$APP_ID)."
    echo "Run with --purge to delete them too."
fi

cat <<'EOF'

Done. Ollama and your models were not touched. To remove them as well:
  ollama rm qwen3-vl:4b-instruct          # delete a model
  sudo systemctl disable --now ollama     # stop the service
If you used --models-dir, the drop-in /etc/systemd/system/ollama.service.d/10-cathode-translate-models.conf
is also still there; delete it if you remove Ollama.
EOF
