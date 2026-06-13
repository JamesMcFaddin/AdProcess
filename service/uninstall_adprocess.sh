#!/usr/bin/env bash
# uninstall_adprocess.sh - AdProcess System Uninstaller
# Copyright (c) 2026 James Eddy
# MIT License
#
# Purpose:
#   Remove AdProcess application/runtime/service artifacts so the Pi
#   looks close to freshly flashed, while keeping installed packages
#   and VNC configuration intact.
#
# Keeps:
#   - apt packages
#   - VNC setting
#   - OS updates
#
# Removes:
#   - AdProcess / PiNotify / PiWatchdog application folders
#   - LabWC AdProcess autostart
#   - AdProcess component post-install service
#   - PiWatchdog service/timer
#   - CIFS ADsCloud fstab entry and active ~/Cloud mount
#   - RAM-backed runtime tree
#
# Optional:
#   - ~/Videos
#   - ~/Archive

set -euo pipefail

LOGFILE="$HOME/adprocess-uninstall.log"
log() { echo "[$(date +'%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOGFILE"; }

confirm() {
  local prompt="$1"
  local answer

  read -rp "$prompt [y/N]: " answer
  case "${answer^^}" in
    Y|YES) return 0 ;;
    *)     return 1 ;;
  esac
}

get_ram_base() {
  if [[ -d /dev/shm ]]; then
    echo "/dev/shm"
  else
    echo "/tmp"
  fi
}

request_components_stop() {
  local RAM_BASE
  local RUNTIME_DIR
  local FLAGS_DIR
  local STOP_TIMEOUT_SECONDS=300
  local STOP_POLL_SECONDS=5

  RAM_BASE="$(get_ram_base)"
  RUNTIME_DIR="$RAM_BASE/AdProcess"
  FLAGS_DIR="$RUNTIME_DIR/Flags"

  if [[ ! -d "$FLAGS_DIR" ]]; then
    log "No runtime Flags directory found. No running components to stop."
    return 0
  fi

  shopt -s nullglob
  local mon_files=( "$FLAGS_DIR"/*.mon )
  shopt -u nullglob

  if [[ ${#mon_files[@]} -eq 0 ]]; then
    log "No monitored components found."
    return 0
  fi

  log "Monitored components found. Requesting clean shutdown..."

  local mon_file
  local component

  for mon_file in "${mon_files[@]}"; do
    component="$(basename "$mon_file" .mon)"
    log "Stopping: $component"
    touch "$FLAGS_DIR/quit-${component}"
  done

  log "Waiting up to $STOP_TIMEOUT_SECONDS seconds for components to shut down..."

  local elapsed=0

  while (( elapsed < STOP_TIMEOUT_SECONDS )); do
    shopt -s nullglob
    mon_files=( "$FLAGS_DIR"/*.mon )
    shopt -u nullglob

    if [[ ${#mon_files[@]} -eq 0 ]]; then
      echo
      log "All monitored components stopped."
      return 0
    fi

    printf "."
    sleep "$STOP_POLL_SECONDS"

    elapsed=$((elapsed + STOP_POLL_SECONDS))

    if (( elapsed % 60 == 0 )); then
      echo
      log "Waiting... ${elapsed}/${STOP_TIMEOUT_SECONDS} seconds"
    fi
  done

  echo
  log "Warning: timeout waiting for monitored components to stop."

  for mon_file in "${mon_files[@]}"; do
    component="$(basename "$mon_file" .mon)"
    log "Still present: $component"
  done

  return 0
}

if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
  echo "Please run this script as your normal user (without sudo)." >&2
  exit 1
fi

echo
echo "AdProcess uninstall will remove application/service/runtime artifacts."
echo "It will keep installed packages and VNC."
echo

if ! confirm "Continue with uninstall?"; then
  echo "Uninstall cancelled."
  exit 0
fi

log "===== AdProcess uninstall starting ====="

#--------------------------------------------------
# Stop AdProcess ecosystem components
#--------------------------------------------------
request_components_stop

# Also stop any VLC/cvlc player left behind.
log "Stopping VLC/cvlc processes owned by this user..."
pkill -u "$USER" -f "/usr/bin/cvlc" 2>/dev/null || true
pkill -u "$USER" -f "vlc" 2>/dev/null || true

#--------------------------------------------------
# Disable/remove post-reboot component installer
#--------------------------------------------------
log "Removing post-reboot component installer service..."
sudo systemctl disable adprocess-components-install.service 2>/dev/null || true
sudo systemctl stop adprocess-components-install.service 2>/dev/null || true
sudo rm -f /etc/systemd/system/adprocess-components-install.service

#--------------------------------------------------
# Disable/remove PiWatchdog service and timer
#--------------------------------------------------
log "Removing PiWatchdog systemd timer/service..."
sudo systemctl stop pi-watchdog.timer 2>/dev/null || true
sudo systemctl stop pi-watchdog.service 2>/dev/null || true
sudo systemctl disable pi-watchdog.timer 2>/dev/null || true
sudo rm -f /etc/systemd/system/pi-watchdog.timer
sudo rm -f /etc/systemd/system/pi-watchdog.service

sudo systemctl daemon-reload || true

#--------------------------------------------------
# Remove LabWC autostart entry
#--------------------------------------------------
AUTOSTART_FILE="$HOME/.config/labwc/autostart"

if [[ -f "$AUTOSTART_FILE" ]]; then
  log "Removing AdProcess from LabWC autostart..."
  sed -i '/AdProcess\.py/d' "$AUTOSTART_FILE"
  sed -i '/# AdProcess autostart/d' "$AUTOSTART_FILE"
else
  log "LabWC autostart file not found; skipping."
fi

#--------------------------------------------------
# Remove CIFS ADsCloud fstab entry and unmount ~/Cloud
#--------------------------------------------------
MOUNTPOINT="$HOME/Cloud"

log "Unmounting CIFS Cloud mount if active..."
if mountpoint -q "$MOUNTPOINT"; then
  sudo umount "$MOUNTPOINT" 2>/dev/null || sudo umount -l "$MOUNTPOINT" 2>/dev/null || true
fi

log "Removing ADsCloud CIFS entry from /etc/fstab..."
sudo sed -i "\|^[^#]*//[^[:space:]]*/ADsCloud[[:space:]]\+$MOUNTPOINT[[:space:]]\+cifs[[:space:]].*|d" /etc/fstab || true

sudo systemctl daemon-reload || true

#--------------------------------------------------
# Remove RAM-backed runtime tree
#--------------------------------------------------
RAM_BASE="$(get_ram_base)"
RUNTIME_DIR="$RAM_BASE/AdProcess"

if [[ -d "$RUNTIME_DIR" ]]; then
  log "Removing runtime directory: $RUNTIME_DIR"
  rm -rf "$RUNTIME_DIR" || true
else
  log "Runtime directory not found: $RUNTIME_DIR"
fi

#--------------------------------------------------
# Remove application directories
#--------------------------------------------------
log "Removing application directories..."
rm -rf "$HOME/AdProcess" || true
rm -rf "$HOME/PiNotify" || true
rm -rf "$HOME/PiWatchdog" || true

#--------------------------------------------------
# Remove installer/log leftovers
#--------------------------------------------------
log "Removing installer leftovers..."
rm -f "$HOME/install_adprocess.sh" || true
rm -f "$HOME/install_components.sh" || true
rm -f "$HOME/adprocess-install.log" || true

# Keep this uninstall log until the end; optionally remove it below.
if confirm "Remove uninstall log too?"; then
  rm -f "$LOGFILE" || true
fi

#--------------------------------------------------
# Optional durable data cleanup
#--------------------------------------------------
if confirm "Remove local video files in ~/Videos?"; then
  log "Removing ~/Videos..."
  rm -rf "$HOME/Videos" || true
else
  log "Keeping ~/Videos."
fi

if confirm "Remove archive files in ~/Archive?"; then
  log "Removing ~/Archive..."
  rm -rf "$HOME/Archive" || true
else
  log "Keeping ~/Archive."
fi

if confirm "Remove empty ~/Cloud directory?"; then
  log "Removing ~/Cloud..."
  rmdir "$HOME/Cloud" 2>/dev/null || rm -rf "$HOME/Cloud" || true
else
  log "Keeping ~/Cloud directory."
fi

log "===== AdProcess uninstall complete ====="
echo
echo "Uninstall complete."
echo "Packages and VNC were left intact."
echo "A reboot is recommended."
echo
