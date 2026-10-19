#!/usr/bin/env bash
# Cathode Translate installer
# https://github.com/Zachygd/Translate-plugin-Cathode
#
# Usage: ./install.sh [options]
#   --model NAME        vision model to use (default: qwen3-vl:4b-instruct)
#   --models-dir DIR    store Ollama models in DIR (e.g. a second drive)
#   --no-tuning         don't add the Ollama flash-attention speed settings
#   --no-ollama         don't install/configure Ollama (you manage it yourself)
#   -y, --yes           don't ask questions, accept defaults
#   -h, --help          show this help

set -euo pipefail

APP_ID="cathode-translate"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
BIN="$BIN_DIR/$APP_ID"
APPS_DIR="$HOME/.local/share/applications"
AUTOSTART_DIR="$HOME/.config/autostart"
CONFIG_DIR="$HOME/.config/$APP_ID"
CONFIG="$CONFIG_DIR/config.ini"
POLKIT_RULE="/etc/polkit-1/rules.d/50-$APP_ID.rules"
SERVICE="ollama"
DROPIN_DIR="/etc/systemd/system/$SERVICE.service.d"
API="http://localhost:11434"

MODEL=""
MODELS_DIR=""
TUNING=1
SETUP_OLLAMA=1
ASSUME_YES=0

bold=$'\e[1m'; green=$'\e[32m'; yellow=$'\e[33m'; red=$'\e[31m'; reset=$'\e[0m'
step() { printf '\n%s==> %s%s\n' "$bold" "$1" "$reset"; }
ok()   { printf '    %s✔%s %s\n' "$green" "$reset" "$1"; }
warn() { printf '    %s!%s %s\n' "$yellow" "$reset" "$1"; }
die()  { printf '%sError:%s %s\n' "$red" "$reset" "$1" >&2; exit 1; }
ask()  {  # ask "question" -> 0 for yes
    [ "$ASSUME_YES" = 1 ] && return 0
    read -r -p "    $1 [Y/n] " reply
    [[ -z "$reply" || "$reply" =~ ^[Yy] ]]
}

while [ $# -gt 0 ]; do
    case "$1" in
        --model)      MODEL="${2:?--model needs a name}"; shift 2 ;;
        --models-dir) MODELS_DIR="${2:?--models-dir needs a path}"; shift 2 ;;
        --no-tuning)  TUNING=0; shift ;;
        --no-ollama)  SETUP_OLLAMA=0; shift ;;
        -y|--yes)     ASSUME_YES=1; shift ;;
        -h|--help)    sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "Unknown option: $1 (see --help)" ;;
    esac
done

[ "$(id -u)" -eq 0 ] && die "Run this as your normal user, not with sudo. It will ask for sudo when needed."
command -v dnf >/dev/null || die "This installer is for Fedora (dnf). See README.md for manual steps on other distros."

# Use the model already in the config if the user didn't pass one
if [ -z "$MODEL" ] && [ -f "$CONFIG" ]; then
    MODEL="$(sed -n 's/^[[:space:]]*model[[:space:]]*=[[:space:]]*//p' "$CONFIG" | head -n1)"
fi
MODEL="${MODEL:-qwen3-vl:4b-instruct}"

printf '%sCathode Translate installer%s\n' "$bold" "$reset"
printf '    Model: %s\n' "$MODEL"
if [ -n "$MODELS_DIR" ]; then printf '    Models folder: %s\n' "$MODELS_DIR"; fi

# ---------------------------------------------------------------- packages
step "Installing dependencies"
PKGS=(python3 python3-pyqt6 spectacle curl)
MISSING=()
for p in "${PKGS[@]}"; do rpm -q "$p" >/dev/null 2>&1 || MISSING+=("$p"); done
if [ ${#MISSING[@]} -gt 0 ]; then
    sudo dnf install -y "${MISSING[@]}"
fi
python3 -c "import PyQt6.QtWidgets, PyQt6.QtNetwork" 2>/dev/null \
    || die "PyQt6 is installed but can't be imported. Try: sudo dnf reinstall python3-pyqt6"
ok "python3-pyqt6, spectacle, curl"

if command -v nvidia-smi >/dev/null && nvidia-smi >/dev/null 2>&1; then
    ok "NVIDIA GPU detected"
elif lspci 2>/dev/null | grep -qiE 'vga.*(amd|ati)|display.*(amd|ati)'; then
    ok "AMD GPU detected (Ollama uses ROCm; see README if it runs on CPU)"
else
    warn "No working NVIDIA driver found. Translation will work but may be slow on CPU."
    warn "NVIDIA users: install the RPM Fusion driver first (see README)."
fi

# ------------------------------------------------------------------ ollama
if [ "$SETUP_OLLAMA" = 1 ]; then
    step "Setting up Ollama"
    if ! command -v ollama >/dev/null; then
        if ask "Ollama isn't installed. Install it now with the official installer?"; then
            curl -fsSL https://ollama.com/install.sh | sh
        else
            die "Ollama is required. Install it, then run this again (or use --no-ollama)."
        fi
    fi
    ok "Ollama $(ollama --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1)"

    systemctl cat "$SERVICE" >/dev/null 2>&1 \
        || die "No '$SERVICE' systemd service found. See README > Manual Ollama setup."

    RESTART=0
    if [ -n "$MODELS_DIR" ]; then
        SVC_USER="$(systemctl show -p User --value "$SERVICE")"
        SVC_USER="${SVC_USER:-root}"
        id "$SVC_USER" >/dev/null 2>&1 \
            || die "The $SERVICE service runs as '$SVC_USER', but that user doesn't exist."
        sudo mkdir -p "$MODELS_DIR"
        sudo chown -R "$SVC_USER:" "$MODELS_DIR"
        sudo mkdir -p "$DROPIN_DIR"
        printf '[Service]\nEnvironment="OLLAMA_MODELS=%s"\n' "$MODELS_DIR" \
            | sudo tee "$DROPIN_DIR/10-$APP_ID-models.conf" >/dev/null
        ok "Models will be stored in $MODELS_DIR (owned by $SVC_USER)"
        RESTART=1
    fi
    if [ "$TUNING" = 1 ]; then
        sudo mkdir -p "$DROPIN_DIR"
        sudo install -m644 "$REPO_DIR/packaging/20-$APP_ID-speed.conf" \
            "$DROPIN_DIR/20-$APP_ID-speed.conf"
        ok "Speed settings added (flash attention, smaller KV cache)"
        RESTART=1
    fi
    sudo systemctl daemon-reload
    sudo systemctl enable "$SERVICE" >/dev/null 2>&1 || true
    if [ "$RESTART" = 1 ]; then
        sudo systemctl restart "$SERVICE"
    else
        sudo systemctl start "$SERVICE"
    fi

    for _ in $(seq 1 30); do
        curl -sf "$API/api/version" >/dev/null && break
        sleep 1
    done
    curl -sf "$API/api/version" >/dev/null \
        || die "Ollama didn't start. Check: journalctl -u $SERVICE -n 30"
    ok "Ollama service running"

    step "Downloading model $MODEL"
    if ollama list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qxF "$MODEL"; then
        ok "Already downloaded"
    else
        ollama pull "$MODEL" || die "Couldn't download '$MODEL'. Check the name at https://ollama.com/library"
        ok "Downloaded"
    fi

    step "Allowing start/stop from the tray menu"
    sed -e "s/@USER@/$USER/g" -e "s/@SERVICE@/$SERVICE/g" \
        "$REPO_DIR/packaging/50-$APP_ID.rules.in" \
        | sudo tee "$POLKIT_RULE" >/dev/null
    sudo chmod 644 "$POLKIT_RULE"
    ok "Polkit rule installed for user $USER"
fi

# --------------------------------------------------------------------- app
step "Installing Cathode Translate"
pkill -f "$BIN( |$)" 2>/dev/null || true
pkill -f "ollama-tray.py" 2>/dev/null || true      # pre-release version
sleep 0.5

install -Dm755 "$REPO_DIR/$APP_ID.py" "$BIN"
ok "App: $BIN"

mkdir -p "$CONFIG_DIR"
if [ -f "$CONFIG" ]; then
    sed -i "s|^[[:space:]]*model[[:space:]]*=.*|model = $MODEL|" "$CONFIG"
    ok "Kept your settings: $CONFIG"
else
    sed "s|^model = .*|model = $MODEL|" "$REPO_DIR/config/config.ini.example" > "$CONFIG"
    ok "Settings: $CONFIG"
fi

mkdir -p "$APPS_DIR" "$AUTOSTART_DIR"
sed "s|@BIN@|$BIN|g" "$REPO_DIR/packaging/$APP_ID.desktop.in" > "$APPS_DIR/$APP_ID.desktop"
sed "s|@BIN@|$BIN|g" "$REPO_DIR/packaging/$APP_ID-autostart.desktop.in" > "$AUTOSTART_DIR/$APP_ID.desktop"
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
command -v kbuildsycoca6 >/dev/null && kbuildsycoca6 >/dev/null 2>&1 || true
ok "App menu entry + start at login"

# Leftovers from the manual (pre-release) setup
for old in "$HOME/bin/ollama-tray.py" "$HOME/bin/ocr-translate.sh" "$HOME/bin/ollama-status.sh"; do
    if [ -e "$old" ]; then warn "Old file from the manual setup can be deleted: $old"; fi
done
OLD_ENTRIES="$(grep -lsE 'ocr-translate\.sh|ollama-tray\.py' "$APPS_DIR"/*.desktop "$AUTOSTART_DIR"/*.desktop 2>/dev/null || true)"
for f in $OLD_ENTRIES; do
    warn "Old shortcut/autostart entry from the manual setup: $f (remove it in System Settings or delete the file)"
done

# ------------------------------------------------------------------- start
step "Starting"
setsid -f "$BIN" >/dev/null 2>&1
ok "Cathode Translate is running in your system tray"

cat <<EOF

${bold}Done!${reset}

  Hotkey:  ${bold}Meta+Shift+T${reset} (Windows key + Shift + T) to translate a screen area.
           If it doesn't work, set it in System Settings > Keyboard > Shortcuts:
           Add New > Application > Cathode Translate > "Translate Screen Region".

  Tray:    left-click to translate, right-click for options, hover for status.
  Config:  $CONFIG

EOF
