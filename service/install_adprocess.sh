#!/usr/bin/env bash
# AdProcess System Installer (Phase 1 → Phase 2)
# Copyright (c) 2025 James Eddy
# MIT License

set -euo pipefail

LOGFILE="$HOME/adprocess-install.log"
log() { echo "[$(date +'%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOGFILE"; }

#--------------------------------------------------
# Guardrails
#--------------------------------------------------
if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
  echo "Please run this script as your normal user (without sudo)." >&2
  exit 1
fi

#--------------------------------------------------
# Relocate to $HOME and re-exec if needed (Phase 1)
# Also delete the original script after we’ve re-exec’d from $HOME.
#--------------------------------------------------
SCRIPT_PATH="$(readlink -f "$0")"
TARGET_PATH="$HOME/install_adprocess.sh"

# If we just re-exec’d, ORIG_SCRIPT_PATH is set: delete the original
if [[ -n "${ORIG_SCRIPT_PATH:-}" && "$ORIG_SCRIPT_PATH" != "$TARGET_PATH" ]]; then
  rm -f -- "$ORIG_SCRIPT_PATH" 2>/dev/null || true
fi

if [[ "$SCRIPT_PATH" != "$TARGET_PATH" ]]; then
  cp "$SCRIPT_PATH" "$TARGET_PATH"
  chmod +x "$TARGET_PATH"
  # Re-exec from $HOME and remember where we came from so we can delete it
  exec env ORIG_SCRIPT_PATH="$SCRIPT_PATH" "$TARGET_PATH" "$@"
fi

#--------------------------------------------------
# Determine Phase
#  - PHASE 1: collect inputs, (optionally) clone repo, reinvoke self
#  - PHASE 2: system setup (non-interactive)
#--------------------------------------------------
if [[ $# -eq 3 ]]; then
  ADPROCESS_VERSION="$1"  # e.g., v1.86 or X (lite mode)
  SMB_USERNAME="$2"
  SMB_PASSWORD="$3"
  log "Continuing Phase 2 with version=$ADPROCESS_VERSION, user=$SMB_USERNAME"
else
  # --- Version selection (robust & simple) ---
  get_tags() {
    curl -fsSL "https://api.github.com/repos/JamesMcFaddin/AdProcess/tags" \
      | grep -oP '"name":\s*"\K[^"]+'   # e.g., v1.86
  }

  TAGS="$(get_tags)"
  if [[ -z "$TAGS" ]]; then
    echo "Error: could not fetch tags from GitHub." >&2
    exit 2
  fi

  while true; do
    read -rp "Enter AdProcess version (1.86 / 1.8X / Enter=latest / X=lite): " INPUT_VERSION

    if [[ -z "$INPUT_VERSION" ]]; then
      ADPROCESS_VERSION="$(printf '%s\n' "$TAGS" | sort -V | tail -n1)"
      break

    elif [[ "$INPUT_VERSION" =~ ^[Xx]$ ]]; then
      ADPROCESS_VERSION="X"   # lite mode (no code download; minimal changes)
      break

    elif [[ "$INPUT_VERSION" =~ ^[0-9]+\.[0-9]+X$ ]]; then
      MAJOR_MINOR="${INPUT_VERSION%X}"
      ADPROCESS_VERSION="$(
        printf '%s\n' "$TAGS" \
          | grep -E "^v?${MAJOR_MINOR}[0-9]+$" \
          | sort -V | tail -n1
      )"
      if [[ -n "$ADPROCESS_VERSION" ]]; then break; else echo "No matching tags for $INPUT_VERSION"; fi

    else
      if printf '%s\n' "$TAGS" | grep -Fxq "$INPUT_VERSION"; then
        ADPROCESS_VERSION="$INPUT_VERSION"; break
      elif printf '%s\n' "$TAGS" | grep -Fxq "v$INPUT_VERSION"; then
        ADPROCESS_VERSION="v$INPUT_VERSION"; break
      else
        echo "Tag '$INPUT_VERSION' not found. Try again."
      fi
    fi
  done

  # --- SMB credentials (asked in both normal and lite modes) ---
  while true; do
    read -rp "Enter Samba username: " SMB_USERNAME
    [[ -n "$SMB_USERNAME" ]] && break
  done
  while true; do
    read -rsp "Enter Samba password: " SMB_PASSWORD; echo
    read -rsp "Re-enter Samba password: " SMB_PASSWORD_CONFIRM; echo
    [[ "$SMB_PASSWORD" == "$SMB_PASSWORD_CONFIRM" ]] && break || echo "Passwords do not match. Try again."
  done

  # --- Clone repo at chosen tag (SKIP in lite mode X) ---
  if [[ "$ADPROCESS_VERSION" != "X" ]]; then
    log "Cloning AdProcess repository (version ${ADPROCESS_VERSION})..."
    rm -rf "$HOME/AdProcess"
    git clone --branch "$ADPROCESS_VERSION" --depth 1 \
      https://github.com/JamesMcFaddin/AdProcess.git "$HOME/AdProcess"

    # Update local installer from repo (future-proof)
    if [[ -f "$HOME/AdProcess/service/install_adprocess.sh" ]]; then
      cp "$HOME/AdProcess/service/install_adprocess.sh" "$HOME/install_adprocess.sh"
      chmod +x "$HOME/install_adprocess.sh"
      log "Updated local install_adprocess.sh from repository"
    fi

    echo "$ADPROCESS_VERSION" > "$HOME/AdProcess/VERSION"
  else
    log "Lite mode 'X': skipping repository clone/update."
  fi

  # --- Re-exec Phase 2 with args ---
  exec "$HOME/install_adprocess.sh" "$ADPROCESS_VERSION" "$SMB_USERNAME" "$SMB_PASSWORD"
fi

#--------------------------------------------------
# PHASE 2 — System Prep
#  Normal mode: full setup
#  Lite mode (version == X): minimal, non-disruptive changes
#--------------------------------------------------
NORMAL_MODE=true
[[ "${ADPROCESS_VERSION}" == "X" ]] && NORMAL_MODE=false

log "Phase 2 starting (mode: $([[ "$NORMAL_MODE" == true ]] && echo normal || echo lite))..."

if [[ "$NORMAL_MODE" == true ]]; then
  log "Updating system..."
  sudo apt update
  sudo apt full-upgrade -y
  sudo apt autoremove --purge -y
  sudo apt autoclean -y || true

  log "Installing required packages (samba, cifs-utils, git, python3, feh, vlc)..."
  sudo apt install -y \
    samba samba-common-bin \
    cifs-utils \
    git \
    python3 \
    python3-pip \
    feh \
    vlc
else
  log "Lite mode: skipping apt update/upgrade and package installs."
fi

log "Enabling NTP time synchronization..."
if command -v timedatectl >/dev/null 2>&1; then
  sudo timedatectl set-ntp true || true
else
  if [[ "$NORMAL_MODE" == true ]]; then
    sudo apt-get install -y ntp
    sudo systemctl enable ntp
    sudo systemctl start ntp
  else
    log "Lite mode: timedatectl not present; skipping ntp package install."
  fi
fi

log "Creating base directories..."
mkdir -p "$HOME/Cloud"
mkdir -p "$HOME/AdProcess/config" || true

if [[ "$NORMAL_MODE" == true ]]; then
  log "Cleaning up repo dev-only files..."
  rm -rf "$HOME/AdProcess/.git" "$HOME/AdProcess/.gitignore" "$HOME/AdProcess/.vscode" || true
  find "$HOME/AdProcess" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true

  # Sanity checks (non-fatal)
  [[ -f "$HOME/AdProcess/config/config.default.json" ]] || echo "Warning: config.default.json not found."
  [[ -f "$HOME/AdProcess/config/PlayList.json"     ]] || echo "Warning: PlayList.json not found."
else
  log "Lite mode: leaving existing AdProcess files intact."
fi

#--------------------------------------------------
# Samba share for $HOME (server on the Pi)
#  - Normal mode: create/modify share + password
#  - Lite mode: leave server config untouched
#--------------------------------------------------
if [[ "$NORMAL_MODE" == true ]]; then
  log "Configuring Samba share..."
  SMB_CONF="/etc/samba/smb.conf"
  SHARE_NAME="AStepUp"
  if ! grep -q "^\[$SHARE_NAME\]" "$SMB_CONF"; then
    sudo tee -a "$SMB_CONF" >/dev/null <<EOF

[$SHARE_NAME]
  path = $HOME
  browseable = yes
  read only = no
  guest ok = no
  valid users = $SMB_USERNAME
  force create mode = 0664
  force directory mode = 2775
EOF
  fi

  log "Setting Samba password for $SMB_USERNAME..."
  if ! sudo pdbedit -L | grep -q "^$SMB_USERNAME:"; then
    (echo "$SMB_PASSWORD"; echo "$SMB_PASSWORD") | sudo smbpasswd -a "$SMB_USERNAME" >/dev/null
  else
    (echo "$SMB_PASSWORD"; echo "$SMB_PASSWORD") | sudo smbpasswd "$SMB_USERNAME" >/dev/null || true
  fi

  sudo systemctl enable --now smbd nmbd
  sudo systemctl restart smbd nmbd
else
  log "Lite mode: skipping Samba server configuration and password changes."
fi

#--------------------------------------------------
# CIFS mount for ~/Cloud (Win11: anonymous via blank password, persistent)
#   - No automount / idle-timeout (avoids remount races)
#   - No credentials file
#   - Boots even if NAS is offline (nofail)
#--------------------------------------------------
log "Setting up CIFS mount for ~/Cloud (password=, persistent)..."

uid=$(id -u); gid=$(id -g)
MOUNTPOINT="$HOME/Cloud"
SHARE="//OfficeDesktop/ADsCloud"
# Keep your proven 'password=' approach; no username supplied.
OPTS="password=,vers=3.0,uid=$uid,gid=$gid,file_mode=0664,dir_mode=0775,cache=none,_netdev,nofail"
ENTRY="$SHARE  $MOUNTPOINT  cifs  $OPTS  0  0"

sudo mkdir -p "$MOUNTPOINT"

# Replace any existing fstab line for this share+mount (regardless of old options), else append
if sudo grep -Eq "^[^#]*//192\.168\.1\.245/ADsCloud[[:space:]]+$MOUNTPOINT[[:space:]]+cifs" /etc/fstab; then
  sudo sed -i "s|^[^#]*//192\.168\.1\.245/ADsCloud[[:space:]]\+$MOUNTPOINT[[:space:]]\+cifs[[:space:]].*|$ENTRY|" /etc/fstab
else
  echo "$ENTRY" | sudo tee -a /etc/fstab >/dev/null
fi

sudo systemctl daemon-reexec

# Remount to apply new options
if mountpoint -q "$MOUNTPOINT"; then
  sudo umount "$MOUNTPOINT" || true
fi
sudo mount "$MOUNTPOINT" || sudo mount -a || true

#--------------------------------------------------
# Enable VNC (if supported) — normal mode only
#--------------------------------------------------
if [[ "$NORMAL_MODE" == true ]]; then
  log "Enabling VNC (if supported)..."
  if command -v raspi-config >/dev/null 2>&1; then
    if ! raspi-config nonint get_vnc | grep -q "0"; then
      sudo raspi-config nonint do_vnc 0 || true
    fi
  fi
else
  log "Lite mode: skipping VNC changes."
fi

#--------------------------------------------------
# Autostart AdProcess on login (labwc) — normal mode only
#--------------------------------------------------
if [[ "$NORMAL_MODE" == true ]]; then
  AUTOSTART_FILE="$HOME/.config/labwc/autostart"
  mkdir -p "$(dirname "$AUTOSTART_FILE")"
  if ! grep -q "AdProcess.py" "$AUTOSTART_FILE" 2>/dev/null; then
    echo "exec /usr/bin/python3 $HOME/AdProcess/AdProcess.py &" >> "$AUTOSTART_FILE"
    chmod +x "$AUTOSTART_FILE" || true
  fi
else
  log "Lite mode: leaving autostart unchanged."
fi

if [[ "$NORMAL_MODE" == true ]]; then
  log "Install complete for version $ADPROCESS_VERSION. Rebooting in 5 seconds..."
  sleep 5
  sudo reboot
else
  log "Lite mode complete (no reboot)."
fi
