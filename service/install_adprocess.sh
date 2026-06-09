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

  #--------------------------------------------------
  # IMPORTANT
  #
  # Do NOT replace the currently running installer with
  # the copy from the repository.
  #
  # The repository version may be older than the bootstrap
  # script that was launched from /boot.
  #
  # Rule:
  #   1. Copy THIS installer to $HOME.
  #   2. Re-execute the $HOME copy.
  #   3. Continue using that copy for Phase 2.
  #
  # This guarantees the same installer runs for the entire
  # install process.
  #--------------------------------------------------
  log "Keeping current installer for Phase 2"

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

  log "Installing required packages (samba, cifs-utils, git, python3, feh, vlc, ffmpeg)..."
  sudo apt install -y \
    samba samba-common-bin \
    cifs-utils \
    git \
    python3 \
    python3-pip \
    feh \
    vlc \
    ffmpeg
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

log "Configuring systemd journal limits..."
sudo mkdir -p /etc/systemd/journald.conf.d
sudo tee /etc/systemd/journald.conf.d/adprocess.conf >/dev/null <<EOF
[Journal]
SystemMaxUse=100M
SystemMaxFileSize=10M
MaxRetentionSec=7day
EOF
sudo systemctl restart systemd-journald || true

log "Creating base directories..."
mkdir -p "$HOME/Cloud"
mkdir -p "$HOME/Flags"
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
#--------------------------------------------------
# Samba share for $HOME (server on the Pi)
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
  create mask = 0664
  directory mask = 0775
EOF

  fi

  log "Setting Samba password for $SMB_USERNAME..."

  (
    echo "$SMB_PASSWORD"
    echo "$SMB_PASSWORD"
  ) | sudo smbpasswd -s -a "$SMB_USERNAME" || true

  log "Enabling Samba services..."

  sudo systemctl enable smbd || true
  sudo systemctl enable nmbd || true

  sudo systemctl restart smbd || true
  sudo systemctl restart nmbd || true

  log "Verifying Samba services..."

  if systemctl is-active --quiet smbd; then
      log "SMBD running"
  else
      log "Warning: SMBD failed to start"
  fi

  if systemctl is-active --quiet nmbd; then
      log "NMBD running"
  else
      log "Warning: NMBD failed to start"
  fi

else

  log "Lite mode: skipping Samba configuration."

fi

#--------------------------------------------------
# CIFS mount for ~/Cloud
#   - Read-only mount from OfficeDesktop/ADsCloud
#   - Uses production-proven pireader account
#   - systemd automount avoids boot/mount timing races
#   - If unavailable, AdProcess still runs from local videos
#--------------------------------------------------
log "Setting up CIFS mount for ~/Cloud (best effort)..."

uid=$(id -u); gid=$(id -g)
MOUNTPOINT="$HOME/Cloud"
SHARE="//OfficeDesktop/ADsCloud"

# Proven working options from BackTv.
OPTS="username=pireader,password=kokopella,vers=3.0,sec=ntlmssp,uid=$uid,gid=$gid,file_mode=0444,dir_mode=0555,cache=loose,actimeo=60,iocharset=utf8,_netdev,x-systemd.automount,x-systemd.idle-timeout=30s,nofail"
ENTRY="$SHARE  $MOUNTPOINT  cifs  $OPTS  0  0"

sudo mkdir -p "$MOUNTPOINT"

# Replace any existing fstab line for ADsCloud mounted at this mountpoint.
if sudo grep -Eq "^[^#]*//([^[:space:]]+)/ADsCloud[[:space:]]+$MOUNTPOINT[[:space:]]+cifs" /etc/fstab; then
  sudo sed -i "s|^[^#]*//[^[:space:]]*/ADsCloud[[:space:]]\+$MOUNTPOINT[[:space:]]\+cifs[[:space:]].*|$ENTRY|" /etc/fstab
else
  echo "$ENTRY" | sudo tee -a /etc/fstab >/dev/null
fi

sudo systemctl daemon-reload
sudo systemctl daemon-reexec

#--------------------------------------------------
# Apply CIFS mount (best effort)
# AdProcess can run from local videos even if Cloud sync is unavailable.
#--------------------------------------------------
log "Applying CIFS mount (best effort)..."

if mountpoint -q "$MOUNTPOINT"; then
  sudo umount "$MOUNTPOINT" || \
    log "Warning: failed to unmount existing $MOUNTPOINT"
fi

if sudo mount "$MOUNTPOINT"; then
  log "CIFS mount succeeded: $MOUNTPOINT"
else
  log "Warning: CIFS mount failed. AdProcess will continue using local files."
fi

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
