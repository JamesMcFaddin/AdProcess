#!/usr/bin/env bash
# AdProcess System
# Copyright (c) 2025 James Eddy (James McFaddin)
#
# This software is licensed under the MIT License.
# See the LICENSE file or https://opensource.org/licenses/MIT for details.

set -euo pipefail

#---------------------#
#  Relocate if needed #
#---------------------#
SCRIPT_PATH="$(readlink -f "$0")"
TARGET_PATH="$HOME/install_adprocess.sh"

if [[ "$SCRIPT_PATH" != "$TARGET_PATH" ]]; then
    cp "$SCRIPT_PATH" "$TARGET_PATH"
    chmod +x "$TARGET_PATH"
    exec "$TARGET_PATH" "$@"
fi

LOGFILE="$HOME/adprocess-install.log"
log() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOGFILE"
}

#---------------------------#
#  Determine Phase (Prompt or Use Args)  #
#---------------------------#
if [[ $# -eq 3 ]]; then
    ADPROCESS_VERSION="$1"
    SMB_USERNAME="$2"
    SMB_PASSWORD="$3"
    log "Continuing Phase 2 with version=$ADPROCESS_VERSION, user=$SMB_USERNAME"
else
    while true; do
        read -rp "Enter the AdProcess version to install (e.g., 1.82 or 1.8X, or press Enter for latest): " INPUT_VERSION
        if [[ -z "$INPUT_VERSION" ]]; then
            log "Fetching latest version tag from GitHub..."
            LATEST_TAG=$(curl -s "https://api.github.com/repos/JamesMcFaddin/AdProcess/tags" \
                | grep -oP '"name":\s*"v[0-9.]+"' \
                | sed 's/"name":\s*"v//' | sed 's/"//' \
                | sort -Vr | head -n 1)
            ADPROCESS_VERSION="v$LATEST_TAG"
            break
        elif [[ "$INPUT_VERSION" =~ ^[0-9]+\.[0-9]+X$ ]]; then
            MAJOR_MINOR="${INPUT_VERSION%X}"
            log "Searching GitHub for highest version matching $MAJOR_MINOR..."
            HIGHEST_TAG=$(curl -s "https://api.github.com/repos/JamesMcFaddin/AdProcess/tags" \
                | grep -oP "\"name\":\s*\"v?$MAJOR_MINOR[0-9]+\"" \
                | sed 's/"name":\s*"v\?//' | sed 's/"//' \
                | sort -Vr | head -n 1)
            if [[ -n "$HIGHEST_TAG" ]]; then
                ADPROCESS_VERSION="$HIGHEST_TAG"
                break
            else
                echo "Error: No matching tags found for $INPUT_VERSION"
            fi
        else
            ADPROCESS_VERSION="v$INPUT_VERSION"
            break
        fi
    done

    while true; do
        read -rp "Enter Samba username: " SMB_USERNAME
        [[ -n "$SMB_USERNAME" ]] && break
    done

    while true; do
        read -rsp "Enter Samba password: " SMB_PASSWORD
        echo
        read -rsp "Re-enter Samba password: " SMB_PASSWORD_CONFIRM
        echo
        if [[ "$SMB_PASSWORD" == "$SMB_PASSWORD_CONFIRM" ]]; then
            break
        else
            echo "Passwords do not match. Try again."
        fi
    done

    #---------------------------#
    #  Clone AdProcess repo     #
    #---------------------------#
    log "Cloning AdProcess repository (version $ADPROCESS_VERSION)..."
    mkdir -p "$HOME/AdProcess"
    rm -rf "$HOME/AdProcess"/*
    git clone --branch "$ADPROCESS_VERSION" --depth 1 https://github.com/JamesMcFaddin/AdProcess.git "$HOME/AdProcess"

    # Immediately copy over the new install script so it can re-execute itself if needed
    cp "$HOME/AdProcess/service/install_adprocess.sh" "$HOME/install_adprocess.sh"
    chmod +x "$HOME/install_adprocess.sh"
    log "Updated local install_adprocess.sh from repository"

    # Track installed version
    echo "$ADPROCESS_VERSION" > "$HOME/AdProcess/VERSION"

    #---------------------------#
    #  PHASE_2 — System Prep    #
    #---------------------------#
    exec "$HOME/install_adprocess.sh" "$ADPROCESS_VERSION" "$SMB_USERNAME" "$SMB_PASSWORD"
fi

#---------------------------#
#  PHASE_2 — System Prep    #
#---------------------------#
log "Updating system..."
sudo apt update && sudo apt full-upgrade -y
sudo apt autoremove --purge -y
sudo apt autoclean

log "Installing required packages..."

echo "Enabling NTP time synchronization..."

# Make sure timedatectl exists (some very minimal OS images might lack it)
if command -v timedatectl &>/dev/null; then
    sudo timedatectl set-ntp true
    echo "NTP time sync enabled via timedatectl."
else
    echo "timedatectl not found. Installing NTP manually..."
    sudo apt-get update
    sudo apt-get install -y ntp
    sudo systemctl enable ntp
    sudo systemctl start ntp
    echo "NTP installed and service started."
fi

sudo apt install -y \
    samba samba-common-bin \
    cifs-utils \
    git \
    python3 \
    python3-pip

#---------------------------#
#  Cleanup Dev Files        #
#---------------------------#
log "Cleaning up dev-only files..."
rm -rf "$HOME/AdProcess/.git" \
       "$HOME/AdProcess/.gitignore" \
       "$HOME/AdProcess/.vscode"
find "$HOME/AdProcess" -type d -name '__pycache__' -exec rm -rf {} +

#---------------------------#
#  Ensure required dirs     #
#---------------------------#
log "Creating base directories..."
mkdir -p "$HOME/Cloud"
mkdir -p "$HOME/AdProcess/config"
rm -rf "$HOME/AdProcess/config"/*

#---------------------------#
#  Configure Samba          #
#---------------------------#
SMB_CONF="/etc/samba/smb.conf"
SHARE_NAME="AStepUp"

log "Configuring Samba share..."
if ! grep -q "^\[$SHARE_NAME\]" "$SMB_CONF"; then
    sudo tee -a "$SMB_CONF" > /dev/null <<EOF

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
    (echo "$SMB_PASSWORD"; echo "$SMB_PASSWORD") | sudo smbpasswd -a "$SMB_USERNAME" > /dev/null
fi

sudo systemctl enable --now smbd nmbd
sudo systemctl restart smbd nmbd

#---------------------------#
#  Mount /Cloud via CIFS    #
#---------------------------#
log "Setting up CIFS mount..."
uid=$(id -u)
gid=$(id -g)
entry="//192.168.1.245/ADsCloud  $HOME/Cloud  cifs  password=,vers=3.0,uid=$uid,gid=$gid,file_mode=0664,dir_mode=0775,cache=none,_netdev,x-systemd.automount,x-systemd.idle-timeout=30s 0 0"

if ! grep -Fxq "$entry" /etc/fstab; then
    echo "$entry" | sudo tee -a /etc/fstab > /dev/null
    sudo systemctl daemon-reexec
fi

if ! mountpoint -q "$HOME/Cloud"; then
    sudo mount -a
fi

#---------------------------#
#  Enable VNC               #
#---------------------------#
log "Enabling VNC..."
if ! raspi-config nonint get_vnc | grep -q "0"; then
    sudo raspi-config nonint do_vnc 0
fi

#---------------------------#
#  Setup autostart          #
#---------------------------#
AUTOSTART_FILE="$HOME/.config/labwc/autostart"
mkdir -p "$(dirname "$AUTOSTART_FILE")"

if ! grep -q "AdProcess.py" "$AUTOSTART_FILE" 2>/dev/null; then
    echo "exec /usr/bin/python3 $HOME/AdProcess/AdProcess.py &" >> "$AUTOSTART_FILE"
    chmod +x "$AUTOSTART_FILE"
fi

#---------------------------#
#  All done, reboot         #
#---------------------------#
log "Install complete for version $ADPROCESS_VERSION. Rebooting in 5 seconds..."
sleep 5
sudo reboot
