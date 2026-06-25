#!/usr/bin/env bash
# AdProcess System Installer (Phase 1 → Phase 2)
# Copyright (c) 2025 James Eddy
# MIT License

set -euo pipefail

LOGFILE="$HOME/adprocess-install.log"
log() { echo "[$(date +'%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOGFILE"; }

#--------------------------------------------------
# Helper Functions
#
# Purpose:
#   Keep reusable installer support routines out of
#   the main installer flow.
#--------------------------------------------------

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

    if [[ -d "$RUNTIME_DIR" ]]; then
      log "Removing stale runtime directory: $RUNTIME_DIR"
      rm -rf "$RUNTIME_DIR" || true
    fi

    return 0
  fi

  log "Monitored components found. Requesting clean shutdown..."

  local mon_file
  local component

  # Example:
  #   /dev/shm/AdProcess/Flags/AdProcess.mon
  #       creates /dev/shm/AdProcess/Flags/quit-AdProcess
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

      if [[ -d "$RUNTIME_DIR" ]]; then
        log "Removing runtime directory: $RUNTIME_DIR"
        rm -rf "$RUNTIME_DIR" || true
      fi

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


repair_package_manager() {
  log "Checking package manager state..."

  export DEBIAN_FRONTEND=noninteractive

  log "Running: sudo dpkg --configure -a"
  if ! sudo dpkg --configure -a; then
    log "ERROR: dpkg repair failed. Package manager is not in a safe state."
    exit 1
  fi
  log "dpkg configure completed"

  log "Running: sudo apt-get -f install -y"
  if ! sudo apt-get -f install -y \
      -o Dpkg::Options::="--force-confdef" \
      -o Dpkg::Options::="--force-confold" \
      -o DPkg::Lock::Timeout=120; then
    log "ERROR: apt dependency repair failed. Package manager is not in a safe state."
    exit 1
  fi
  log "apt dependency repair completed"
}

apt_update_only() {
  log "Updating package lists only..."

  export DEBIAN_FRONTEND=noninteractive

  if ! sudo apt-get update -o DPkg::Lock::Timeout=120; then
    log "ERROR: apt update failed."
    exit 1
  fi
}

apt_install_required_packages() {
  log "Installing required packages only. General OS upgrade is intentionally skipped."

  export DEBIAN_FRONTEND=noninteractive

  if ! sudo apt-get install -y \
      -o Dpkg::Options::="--force-confdef" \
      -o Dpkg::Options::="--force-confold" \
      -o DPkg::Lock::Timeout=120 \
      samba samba-common-bin \
      smbclient \
      cifs-utils \
      git \
      python3 \
      python3-pip \
      feh \
      vlc \
      ffmpeg; then
    log "ERROR: required package install failed."
    exit 1
  fi
}

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
#
# Rule:
#   Wherever this installer starts from, copy THIS installer to $HOME
#   and continue using that same copy for Phase 2.
#
# Do NOT replace this installer with the copy from GitHub. The repository
# may contain an older installer than the bootstrap script.
#--------------------------------------------------
SCRIPT_PATH="$(readlink -f "$0")"
TARGET_PATH="$HOME/install_adprocess.sh"

# If we just re-exec’d, ORIG_SCRIPT_PATH is set: delete the original.
if [[ -n "${ORIG_SCRIPT_PATH:-}" && "$ORIG_SCRIPT_PATH" != "$TARGET_PATH" ]]; then
  rm -f -- "$ORIG_SCRIPT_PATH" 2>/dev/null || true
fi

if [[ "$SCRIPT_PATH" != "$TARGET_PATH" ]]; then
  cp "$SCRIPT_PATH" "$TARGET_PATH"
  chmod +x "$TARGET_PATH"
  exec env ORIG_SCRIPT_PATH="$SCRIPT_PATH" "$TARGET_PATH" "$@"
fi

#--------------------------------------------------
# Request Running Components to Stop
#
# Purpose:
#   Before cloning/updating repositories or replacing
#   services, ask any currently monitored AdProcess
#   ecosystem components to shut down cleanly.
#
# Mechanism:
#   Runtime Flags are RAM-backed under:
#
#       /dev/shm/AdProcess/Flags
#
#   or, if /dev/shm is unavailable:
#
#       /tmp/AdProcess/Flags
#
#   *.mon files identify monitored components.
#   For each heartbeat file found, this creates:
#
#       quit-<Component>
#
# Example:
#   /dev/shm/AdProcess/Flags/AdProcess.mon
#       creates /dev/shm/AdProcess/Flags/quit-AdProcess
#
# Notes:
#   Fresh installs normally have no runtime Flags
#   directory and no *.mon files, so this step exits
#   immediately.
#
#   Existing installs may have active processes. This
#   gives them up to 5 minutes to observe the quit file,
#   stop, and remove their .mon heartbeat file.
#
#   This is warning-only. The installer continues even
#   if a component does not stop within the timeout.
#--------------------------------------------------
request_components_stop

#--------------------------------------------------
# Determine Phase
#  - PHASE 1: collect inputs, clone repo if needed, then reinvoke self
#  - PHASE 2: system setup using known args, non-interactive
#
# Version behavior:
#   Enter  -> main branch, latest checked-in code
#   X      -> lite mode, no repo clone/update
#   1.86   -> specific tag v1.86 if present
#   v1.86  -> specific tag v1.86 if present
#   1.8X   -> latest tag matching v1.8*
#--------------------------------------------------
if [[ $# -eq 3 || $# -eq 4 || $# -eq 5 ]]; then
  ADPROCESS_VERSION="$1"  # e.g., main, v1.85, or X
  SMB_USERNAME="$2"
  SMB_PASSWORD="$3"
  TIMEZONE="${4:-America/Chicago}"
  [[ "$TIMEZONE" == "KEEP" ]] && TIMEZONE="America/Chicago"
  DEV_MODE="${5:-false}"
  log "Continuing Phase 2 with version=$ADPROCESS_VERSION, user=$SMB_USERNAME, timezone=$TIMEZONE, dev_mode=$DEV_MODE"
else
  #--------------------------------------------------
  # Development / Debug Mode Selection
  #
  # Purpose:
  #   Ask whether this Pi is being used as a development
  #   or debugging machine instead of a normal deployment Pi.
  #
  # Default behavior:
  #   Enter -> Normal deployment install
  #
  # Development mode:
  #   D     -> Preserve developer files such as .git,
  #            .gitignore, .vscode, and __pycache__.
  #
  # Notes:
  #   Ask this first because it affects later install behavior.
  #   If you are debugging on the Pi, pay attention here.
  #--------------------------------------------------
  while true; do
    echo
    read -rp "Development/debug Pi? [D=Yes, Enter=No]: " DEBUG_CHOICE

    case "${DEBUG_CHOICE^^}" in
      "")
        DEV_MODE=false
        break
        ;;
      D)
        DEV_MODE=true
        break
        ;;
      *)
        echo "Invalid selection."
        ;;
    esac
  done

  #--------------------------------------------------
  # AdProcess Version Selection
  #
  # Purpose:
  #   Choose which AdProcess source version to install.
  #
  # Options:
  #   Enter -> main branch, latest checked-in code
  #   main  -> main branch, latest checked-in code
  #   X     -> lite mode, no repo clone/update
  #   1.86  -> tag v1.86 if present
  #   v1.86 -> tag v1.86 if present
  #   1.8X  -> latest tag matching v1.8*
  #
  # Notes:
  #   Tags are read from GitHub when available.
  #   Lite mode leaves existing AdProcess files intact.
  #--------------------------------------------------
  get_tags() {
    git ls-remote --tags --refs \
      https://github.com/JamesMcFaddin/AdProcess.git \
      | awk -F/ '{print $NF}'
  }

  TAGS="$(get_tags || true)"

  while true; do
    read -rp "Enter AdProcess version (main=latest / 1.86 / 1.8X / Enter=main / X=lite): " INPUT_VERSION

    if [[ -z "$INPUT_VERSION" ]]; then
      ADPROCESS_VERSION="main"
      break

    elif [[ "$INPUT_VERSION" =~ ^[Mm][Aa][Ii][Nn]$ ]]; then
      ADPROCESS_VERSION="main"
      break

    elif [[ "$INPUT_VERSION" =~ ^[Xx]$ ]]; then
      ADPROCESS_VERSION="X"
      break

    elif [[ "$INPUT_VERSION" =~ ^[0-9]+\.[0-9]+X$ ]]; then
      if [[ -z "$TAGS" ]]; then
        echo "Could not fetch tags from GitHub. Try 'main', X, or a specific tag."
        continue
      fi

      MAJOR_MINOR="${INPUT_VERSION%X}"
      ADPROCESS_VERSION="$(
        printf '%s\n' "$TAGS" \
          | grep -E "^v?${MAJOR_MINOR}[0-9]+$" \
          | sort -V | tail -n1
      )"

      if [[ -n "$ADPROCESS_VERSION" ]]; then
        break
      else
        echo "No matching tags for $INPUT_VERSION"
      fi

    else
      if [[ -z "$TAGS" ]]; then
        echo "Could not fetch tags from GitHub. Try 'main', X, or a specific tag."
        continue
      fi

      if printf '%s\n' "$TAGS" | grep -Fxq "$INPUT_VERSION"; then
        ADPROCESS_VERSION="$INPUT_VERSION"
        break
      elif printf '%s\n' "$TAGS" | grep -Fxq "v$INPUT_VERSION"; then
        ADPROCESS_VERSION="v$INPUT_VERSION"
        break
      else
        echo "Tag '$INPUT_VERSION' not found. Try again."
      fi
    fi
  done

  #--------------------------------------------------
  # Timezone Selection
  #
  # Purpose:
  #   Set the Raspberry Pi timezone during installation.
  #
  # Default behavior:
  #   Enter -> Central (America/Chicago)
  #
  # Notes:
  #   Most deployments are expected to use Central time,
  #   so Enter follows the normal install path.
  #--------------------------------------------------
  while true; do
    echo
    echo "Timezone:"
    echo "  Enter) Central  (America/Chicago)"
    echo "  1) Eastern      (America/New_York)"
    echo "  2) Mountain     (America/Denver)"
    echo "  3) Pacific      (America/Los_Angeles)"
    echo "  4) Arizona      (America/Phoenix)"
    echo "  5) UTC"
    echo

    read -rp "Choice [Enter=Central]: " TZ_CHOICE

    case "$TZ_CHOICE" in
      "") TIMEZONE="America/Chicago"; break ;;
      1)  TIMEZONE="America/New_York"; break ;;
      2)  TIMEZONE="America/Denver"; break ;;
      3)  TIMEZONE="America/Los_Angeles"; break ;;
      4)  TIMEZONE="America/Phoenix"; break ;;
      5)  TIMEZONE="UTC"; break ;;
      *)  echo "Invalid selection." ;;
    esac
  done

  #--------------------------------------------------
  # Local Samba Share Credentials
  #
  # Purpose:
  #   Collect the username and password used for the Pi's
  #   local Samba share.
  #
  # Notes:
  #   These credentials are used later by smbpasswd.
  #   Password confirmation reduces fat-finger mistakes.
  #--------------------------------------------------
  while true; do
    read -rp "Enter Samba username: " SMB_USERNAME
    [[ -n "$SMB_USERNAME" ]] && break
  done

  while true; do
    read -rsp "Enter Samba password: " SMB_PASSWORD; echo
    read -rsp "Re-enter Samba password: " SMB_PASSWORD_CONFIRM; echo
    [[ "$SMB_PASSWORD" == "$SMB_PASSWORD_CONFIRM" ]] && break || echo "Passwords do not match. Try again."
  done

  #--------------------------------------------------
  # Repository Clone / Update
  #
  # Purpose:
  #   Fetch the selected AdProcess version from GitHub.
  #
  # Normal mode:
  #   Existing ~/AdProcess is removed and replaced by
  #   the selected branch or tag.
  #
  # Lite mode:
  #   Version X skips clone/update and leaves existing
  #   files in place.
  #
  # Important:
  #   The running installer is kept. It is not replaced
  #   by the copy from the repository.
  #--------------------------------------------------
  if [[ "$ADPROCESS_VERSION" != "X" ]]; then
    log "Cloning AdProcess repository (version ${ADPROCESS_VERSION})..."
    rm -rf "$HOME/AdProcess"
    git clone --branch "$ADPROCESS_VERSION" --depth 1 \
      https://github.com/JamesMcFaddin/AdProcess.git "$HOME/AdProcess"

    # IMPORTANT:
    # Do NOT replace the currently running installer with the copy from GitHub.
    # The repo/tag may contain an older installer than this bootstrap script.
    log "Keeping current installer for Phase 2"

    echo "$ADPROCESS_VERSION" > "$HOME/AdProcess/VERSION"
  else
    log "Lite mode 'X': skipping repository clone/update."
  fi

  #--------------------------------------------------
  # Re-exec Into Phase 2
  #
  # Purpose:
  #   Restart this same installer with all Phase 1 inputs
  #   passed as command-line arguments.
  #
  # Notes:
  #   Phase 2 is non-interactive.
  #--------------------------------------------------
  exec "$HOME/install_adprocess.sh" "$ADPROCESS_VERSION" "$SMB_USERNAME" "$SMB_PASSWORD" "$TIMEZONE" "$DEV_MODE"
fi

#--------------------------------------------------
# PHASE 2 — System Prep
#  Normal mode: full setup
#  Lite mode (version == X): minimal, non-disruptive changes
#--------------------------------------------------
NORMAL_MODE=true
[[ "${ADPROCESS_VERSION}" == "X" ]] && NORMAL_MODE=false

log "Phase 2 starting (mode: $([[ "$NORMAL_MODE" == true ]] && echo normal || echo lite), dev_mode=$DEV_MODE)..."

#--------------------------------------------------
# System Update and Required Packages
#
# Purpose:
#   Repair interrupted package operations, refresh package
#   lists, and install only the packages required by
#   AdProcess and its support tooling.
#
# Installed packages:
#   samba, samba-common-bin, smbclient, cifs-utils,
#   git, python3, python3-pip, feh, vlc, ffmpeg.
#
# Notes:
#   Normal mode intentionally does NOT perform a general
#   apt upgrade/full-upgrade. Field installs should not
#   pull unrelated package updates while deploying AdProcess.
#
#   Lite mode skips package changes.
#--------------------------------------------------
if [[ "$NORMAL_MODE" == true ]]; then
  repair_package_manager
  apt_update_only
  apt_install_required_packages
  sudo apt-get autoclean -y || true
else
  log "Lite mode: skipping apt update and package installs."
fi

#--------------------------------------------------
# Timezone and NTP Configuration
#
# Purpose:
#   Apply the timezone selected in Phase 1 and enable
#   network time synchronization.
#
# Default:
#   Enter in Phase 1 selects Central time:
#       America/Chicago
#
# Notes:
#   Re-running the installer safely re-applies the
#   selected timezone.
#--------------------------------------------------
log "Configuring timezone and NTP..."

if command -v timedatectl >/dev/null 2>&1; then
  log "Setting timezone to $TIMEZONE"
  sudo timedatectl set-timezone "$TIMEZONE" || \
    log "Warning: failed to set timezone"

  log "Enabling NTP time synchronization..."
  sudo timedatectl set-ntp true || \
    log "Warning: failed to enable NTP"
else
  if [[ "$NORMAL_MODE" == true ]]; then
    sudo apt-get install -y ntp
    sudo systemctl enable ntp || true
    sudo systemctl start ntp || true

    log "Warning: timedatectl not present; could not automatically set timezone to $TIMEZONE."
  else
    log "Lite mode: timedatectl not present; skipping ntp package install."
  fi
fi

#--------------------------------------------------
# systemd Journal Retention Limits
#
# Purpose:
#   Prevent log files from consuming excessive disk
#   space on Raspberry Pi SD cards.
#
# Configuration:
#   SystemMaxUse      = 100 MB
#   SystemMaxFileSize = 10 MB
#   MaxRetentionSec   = 7 days
#
# Notes:
#   AdProcess maintains its own application logs.
#   These settings apply only to systemd journal
#   entries such as service output, kernel messages,
#   boot events, and system diagnostics.
#
#   Safe to re-run. Existing settings are replaced
#   with the current AdProcess standard.
#--------------------------------------------------
log "Configuring systemd journal limits..."
sudo mkdir -p /etc/systemd/journald.conf.d
sudo tee /etc/systemd/journald.conf.d/adprocess.conf >/dev/null <<EOF
[Journal]
SystemMaxUse=100M
SystemMaxFileSize=10M
MaxRetentionSec=7day
EOF
sudo systemctl restart systemd-journald || true

#--------------------------------------------------
# Base Directory Structure
#
# Purpose:
#   Create the standard AdProcess directory layout
#   used by AdProcess, PiWatchdog, PiNotify, and
#   related support utilities.
#
# Directories:
#   ~/Cloud
#       CIFS-mounted ADsCloud share from OfficeDesktop.
#
#   ~/Archive
#       Persistent storage for archived logs,
#       diagnostics, and support files.
#
#   ~/PFlags
#       Persistent flags that survive reboot, such as:
#           debug-all
#           debug-AdProcess
#           debug-PiWatchdog
#
#   ~/AdProcess/config
#       Local AdProcess configuration files.
#
# Notes:
#   mkdir -p is used so the installer may be run
#   repeatedly without error.
#
#   Existing files and directories are preserved.
#--------------------------------------------------
log "Creating base directories..."
mkdir -p "$HOME/Cloud"
mkdir -p "$HOME/Archive"
mkdir -p "$HOME/PFlags"
mkdir -p "$HOME/AdProcess/config" || true
log "Persistent flags directory ready: $HOME/PFlags"

#--------------------------------------------------
# Deployment Copy Cleanup and Sanity Checks
#
# Purpose:
#   Convert a freshly cloned AdProcess repository into
#   a clean runtime deployment copy.
#
# Removed in normal deployment mode:
#   .git
#       Git repository metadata. Deployment Pis do not
#       need to track branches, commits, or perform pulls.
#
#   .gitignore
#       Development-only Git control file.
#
#   .vscode
#       VS Code workspace settings. Deployment Pis are not
#       edited with VS Code.
#
#   __pycache__
#       Python bytecode cache directories. These are safe
#       to remove and will be regenerated if needed.
#
# Notes:
#   Development/debug mode preserves these files.
#   Lite mode leaves the existing AdProcess directory
#   untouched.
#--------------------------------------------------
if [[ "$NORMAL_MODE" == true ]]; then
  if [[ "$DEV_MODE" == true ]]; then
    log "Development/debug mode: keeping development files intact."
  else
    log "Cleaning up deployment copy..."
    rm -rf "$HOME/AdProcess/.git" "$HOME/AdProcess/.gitignore" "$HOME/AdProcess/.vscode" || true
    find "$HOME/AdProcess" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
  fi

  # Sanity checks (non-fatal)
  [[ -f "$HOME/AdProcess/config/config.default.json" ]] || echo "Warning: config.default.json not found."
  [[ -f "$HOME/AdProcess/config/PlayList.json"     ]] || echo "Warning: PlayList.json not found."
else
  log "Lite mode: leaving existing AdProcess files intact."
fi

#--------------------------------------------------
# Samba Share Configuration
#
# Purpose:
#   Expose the Pi's home directory as a Samba share
#   for maintenance access from other machines.
#
# Share:
#   AStepUp -> $HOME
#
# Notes:
#   Best effort only.
#   AdProcess can run even if Samba fails.
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
#
# TODO:
# Move CIFS credentials out of source control.
#
# Current design intentionally embeds credentials to allow
# fully unattended installs on freshly flashed Pi systems.
#
# Future options:
#   - Protected credentials file
#   - Encrypted credential store
#   - Provisioning from OfficeDesktop
#
# Deferred until deployment workflow stabilizes.
#--------------------------------------------------
log "Setting up CIFS mount for ~/Cloud (best effort)..."

uid=$(id -u)
gid=$(id -g)

MOUNTPOINT="$HOME/Cloud"
SHARE="//OfficeDesktop/ADsCloud"

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

log "Applying CIFS mount (best effort)..."

if mountpoint -q "$MOUNTPOINT"; then
  log "CIFS mount already active: $MOUNTPOINT"
else
  if sudo mount "$MOUNTPOINT"; then
    log "CIFS mount succeeded: $MOUNTPOINT"
  else
    log "Warning: CIFS mount failed. AdProcess will continue using local files."
  fi
fi

#--------------------------------------------------
# VNC Enablement
#
# Purpose:
#   Enable Raspberry Pi VNC access when raspi-config
#   supports the non-interactive VNC command.
#
# Notes:
#   Normal mode only.
#   Best effort. Unsupported systems are skipped.
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
# LabWC Autostart Configuration
#
# Purpose:
#   Start the AdProcess desktop-session components
#   when the Pi graphical desktop session starts.
#
# Components started:
#   AdLauncher
#       Runs inside the labwc desktop session and handles
#       runtime *.launch requests from:
#
#           /dev/shm/AdProcess/Flags
#
#       This allows AdWatchdog/PiWatchdog to request
#       AdProcess restarts without launching GUI processes
#       from a systemd timer/session.
#
#   AdProcess
#       Main signage application.
#
# Architecture:
#   Normal boot:
#       labwc -> AdLauncher
#       labwc -> AdProcess
#
#   Runtime recovery:
#       PiWatchdog writes AdProcess.launch
#       AdLauncher launches AdProcess inside the desktop session.
#
# Target:
#   ~/.config/labwc/autostart
#
# Notes:
#   Normal mode only.
#   Duplicate/stale launcher entries are removed before
#   the current AdProcess system autostart block is added.
#--------------------------------------------------
if [[ "$NORMAL_MODE" == true ]]; then
  log "Configuring LabWC autostart for AdProcess system..."

  AUTOSTART_FILE="$HOME/.config/labwc/autostart"
  AUTOSTART_MARKER="# AdProcess system autostart"

  ADLAUNCHER_CMD="exec /usr/bin/python3 $HOME/AdProcess/AdLauncher/AdLauncher.py &"
  ADPROCESS_CMD="exec /usr/bin/python3 $HOME/AdProcess/AdProcess.py &"

  mkdir -p "$(dirname "$AUTOSTART_FILE")"
  touch "$AUTOSTART_FILE"

  # Remove previous AdProcess system autostart entries,
  # including older direct-only AdProcess starts and any
  # earlier AdLauncher attempts.
  sed -i '/AdProcess\.py/d' "$AUTOSTART_FILE"
  sed -i '/AdLauncher\.py/d' "$AUTOSTART_FILE"
  sed -i '/# AdProcess autostart/d' "$AUTOSTART_FILE"
  sed -i '/# AdLauncher autostart/d' "$AUTOSTART_FILE"
  sed -i '/# AdProcess system autostart/d' "$AUTOSTART_FILE"

  {
    echo ""
    echo "$AUTOSTART_MARKER"
    echo "$ADLAUNCHER_CMD"
    echo "$ADPROCESS_CMD"
  } >> "$AUTOSTART_FILE"

  chmod +x "$AUTOSTART_FILE" || true

  log "LabWC autostart configured:"
  log "  $ADLAUNCHER_CMD"
  log "  $ADPROCESS_CMD"
else
  log "Lite mode: leaving autostart unchanged."
fi

#--------------------------------------------------
# Post-Reboot Component Installer
#
# Purpose:
#   Register a one-shot systemd service that runs
#   after reboot to install/update AdProcess support
#   components such as PiNotify and PiWatchdog.
#
# Script:
#   ~/AdProcess/service/install_components.sh
#
# Notes:
#   install_components.sh is expected to disable
#   and remove this service when it completes.
#--------------------------------------------------
if [[ "$NORMAL_MODE" == true ]]; then
  log "Registering post-reboot component installer..."

  COMPONENT_INSTALLER="$HOME/AdProcess/service/install_components.sh"
  POSTINSTALL_SERVICE="/etc/systemd/system/adprocess-components-install.service"

  if [[ -f "$COMPONENT_INSTALLER" ]]; then
    chmod +x "$COMPONENT_INSTALLER" || true

    sudo tee "$POSTINSTALL_SERVICE" >/dev/null <<EOF
[Unit]
Description=AdProcess Post-Reboot Component Installer
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$USER
WorkingDirectory=$HOME
ExecStart=/bin/bash $COMPONENT_INSTALLER

[Install]
WantedBy=multi-user.target
EOF

    sudo systemctl daemon-reload
    sudo systemctl enable adprocess-components-install.service

    log "Post-reboot component installer registered."
  else
    log "Warning: component installer not found: $COMPONENT_INSTALLER"
    log "Skipping post-reboot component installer registration."
  fi
else
  log "Lite mode: skipping post-reboot component installer registration."
fi

#--------------------------------------------------
# Final Completion / Reboot
#
# Purpose:
#   Finish the installation and reboot normal
#   deployment installs so system services, mounts,
#   desktop autostart, and package updates begin from
#   a clean boot.
#
# Notes:
#   Lite mode does not reboot.
#--------------------------------------------------
if [[ "$NORMAL_MODE" == true ]]; then
  log "Install complete for version $ADPROCESS_VERSION. Rebooting in 5 seconds..."
  sleep 5
  sudo reboot
else
  log "Lite mode complete (no reboot)."
fi
