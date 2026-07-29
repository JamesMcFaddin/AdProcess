<<<<<<< HEAD
#!/bin/bash

=======
>>>>>>> 5ab0ebb4e7f87cd0e59dc61d1fac66af38913b2b
# install_components.sh - AdProcess System
# Copyright (c) 2025 James Eddy (James McFaddin)
#
# This software is licensed under the MIT License.
# See the LICENSE file or https://opensource.org/licenses/MIT for details.

# AdProcess Component Installer
# Installs post-reboot AdProcess ecosystem components.
# Copyright (c) 2025 James Eddy
# MIT License

set -euo pipefail

LOGFILE="$HOME/adprocess-install.log"

log() {
  echo "[$(date +'%Y-%m-%d %H:%M:%S')] [Components] $1" |
    tee -a "$LOGFILE"
}

#--------------------------------------------------
# Guardrails
#--------------------------------------------------

if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
  echo "Please run this script as your normal user (without sudo)." >&2
  exit 1
fi

#--------------------------------------------------
# Component Installer Settings
#
# Purpose:
#   Define repository locations and standard directory
#   paths for post-reboot AdProcess components.
#
# Notes:
#   Repositories are expected to be private GitHub repos
#   accessible from this Pi using the same GitHub access
#   method used by the main AdProcess installer.
#--------------------------------------------------

HOME_DIR="$HOME"
FLAGS_DIR="$HOME_DIR/Flags"
ARCHIVE_DIR="$HOME_DIR/Archive"

PINOTIFY_REPO_URL="https://github.com/JamesMcFaddin/PiNotify.git"

PINOTIFY_DIR="$HOME_DIR/PiNotify"

#--------------------------------------------------
# Helper: Install or Refresh Repository
#
# Purpose:
#   Fetch the current main-branch version of a component
#   into its standard runtime directory.
#
# Behavior:
#   Existing target directory is removed first.
#   This matches the main AdProcess installer pattern:
#   a clean deployment copy is preferred over in-place pull.
#
# Arguments:
#   $1 -> repository URL
#   $2 -> target directory
#   $3 -> component display name
#--------------------------------------------------

install_repo() {
  local REPO_URL="$1"
  local TARGET_DIR="$2"
  local COMPONENT_NAME="$3"

  log "Installing $COMPONENT_NAME from $REPO_URL..."

  rm -rf "$TARGET_DIR"

  git clone \
    --branch main \
    --depth 1 \
    "$REPO_URL" \
    "$TARGET_DIR"

  log "$COMPONENT_NAME repository installed at $TARGET_DIR"
}

#--------------------------------------------------
# Shared Persistent Directories
#
# Purpose:
#   Ensure directories shared across components exist.
#
# Directories:
#   ~/Flags
#       Runtime flags, heartbeat files, debug toggles,
#       quit requests, and component state files.
#
#   ~/Archive
#       Persistent archive location for component output,
#       saved mailboxes, logs, and future retained data.
#
# Notes:
#   ~/Flags should normally already exist from the main
#   AdProcess installer, but mkdir -p makes this safe.
#--------------------------------------------------

log "Creating shared persistent directories..."

mkdir -p "$FLAGS_DIR"
mkdir -p "$ARCHIVE_DIR"

#--------------------------------------------------
# Install PiNotify
#
# Purpose:
#   Install the notification component and create its
#   mailbox directory structure.
#
# Directory Layout:
#   ~/PiNotify
#       Component code.
#
#   ~/PiNotify/Inbox
#       Inbound mailbox directory.
#
#   ~/PiNotify/Outbox
#       Outbound notification queue.
#
#   ~/PiNotify/Working
#       Temporary in-process mailbox work area.
#--------------------------------------------------

install_repo \
  "$PINOTIFY_REPO_URL" \
  "$PINOTIFY_DIR" \
  "PiNotify"

log "Creating PiNotify mailbox directories..."

mkdir -p "$PINOTIFY_DIR/Inbox"
mkdir -p "$PINOTIFY_DIR/Outbox"
mkdir -p "$PINOTIFY_DIR/Working"

#--------------------------------------------------
<<<<<<< HEAD
=======
# Install PiWatchdog
#
# Purpose:
#   Install or update the PiWatchdog repository,
#   create required directories, and install the
#
# Directories:
#   ~/PiWatchdog
#       PiWatchdog application files.
#
#   ~/Flags
#       Shared control, status, heartbeat, and
#       debug files used by AdProcess, PiNotify,
#       PiWatchdog, and administrative tools.
#
#   ~/Archive
#       Persistent storage for archived logs,
#       diagnostics, and future support files.
#
# Notes:
#   Installation details are owned by the
#   PiWatchdog project itself and are performed by:
#
#       ~/PiWatchdog/install_pi-watchdog.sh
#
#   Safe to re-run.
#--------------------------------------------------

install_repo \
    "https://github.com/JamesMcFaddin/PiWatchdog.git" \
    "$HOME/PiWatchdog" \
    "PiWatchdog"

#--------------------------------------------------
>>>>>>> 5ab0ebb4e7f87cd0e59dc61d1fac66af38913b2b
# Component Installer Completion
#
# Purpose:
#
# Notes:
#
#--------------------------------------------------
<<<<<<< HEAD

log "Cleaning up post-reboot component installer service if present..."

sudo systemctl disable \
  adprocess-install-components.service \
  >/dev/null 2>&1 || true

sudo rm -f \
  /etc/systemd/system/adprocess-install-components.service

sudo systemctl daemon-reload || true
=======
>>>>>>> 5ab0ebb4e7f87cd0e59dc61d1fac66af38913b2b

log "Component install complete."