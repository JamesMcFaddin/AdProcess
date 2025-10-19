# 🧩 Install and Configure Samba on the Pi

This guide explains how to install, configure, and test a Samba file share on your Raspberry Pi. Samba lets you access files from Windows, macOS, or Linux systems using the familiar `\\raspberrypi` network path.

---

## Step 1 — Update and Install Samba

Update your Raspberry Pi and install Samba along with its common utilities:

```bash
sudo apt update
sudo apt install samba samba-common-bin -y
```

You can confirm Samba installed correctly with:

```bash
smbd --version
```

---

## Step 2 — Create a Shared Folder

Choose or create a directory to share. For example, create a new folder called “Shared” in your home directory:

```bash
mkdir -p ~/Shared
chmod 777 ~/Shared
```

> **Note:** The `777` permission makes the folder world-readable and writable for convenience. For a production environment, you should restrict access to trusted users only.

---

## Step 3 — Edit the Samba Configuration File

Open the main Samba configuration file in a text editor:

```bash
sudo nano /etc/samba/smb.conf
```

Scroll to the bottom and add the following share definition:

```ini
[AStepUp]
  path = /home/astepup
  browseable = yes
  read only = no
  guest ok = no
  valid users = astepup
  force create mode = 0664
  force directory mode = 2775

```

Save your changes (`Ctrl + O`, `Enter`, then `Ctrl + X`).

---

## Step 4 — Add a Samba User

You must add your Pi’s user to Samba so it can log in to the share. Replace `pi` with your actual username if different:

```bash
sudo smbpasswd -a username
```

Enable the account:

```bash
sudo smbpasswd -e username
```

Restart and enable Samba:

```bash
sudo systemctl restart smbd
sudo systemctl enable smbd
```

---

## Step 5 — Connect from Another Device

### On Windows
1. Press **Win + R** and enter:
   ```
   \\raspberrypi
   ```
   or use its IP address:
   ```
   \\192.168.1.100
   ```
2. Enter your Raspberry Pi username and Samba password when prompted.

### On macOS or Linux
1. Open **Finder → Go → Connect to Server...**
2. Enter:
   ```
   smb://raspberrypi.local/Shared
   ```
3. Authenticate with your Pi username and password.

---

## Step 6 — (Optional) Guest Access Without Password

If you want to create an open share that does not require login credentials, add another section to `/etc/samba/smb.conf`:

```ini
[Public]
   path = /home/pi/Public
   browseable = yes
   writeable = yes
   guest ok = yes
   guest only = yes
   create mask = 0777
   directory mask = 0777
```

Restart Samba to apply changes:

```bash
sudo systemctl restart smbd
```

You can now access it via:

```
\\raspberrypi\Public
```

or

```
smb://raspberrypi.local/Public
```

> ⚠️ Guest access is convenient but **not secure**. Anyone on your local network can read and write to the folder.

---

## Step 7 — Verify Samba Service

Check that Samba is running:

```bash
sudo systemctl status smbd
```

If everything is working, you should see “active (running)” in green.

---

## Step 8 — Troubleshooting

| Issue | Likely Fix |
|-------|-------------|
| 🔌 “Host not found” | Use the Pi’s IP address instead of `raspberrypi.local`. |
| 🔑 “Access denied” | Double-check `smb.conf` user settings and file permissions. |
| 🔄 “Old credentials cached” | On Windows, open Command Prompt and run `net use * /delete`. |
| 🔥 Firewall issues | Allow TCP ports **137–139** and **445** through your firewall if enabled. |

---

## Step 9 — Auto-Mounting the Share (Optional)

To automatically mount your Pi’s share when booting a Linux client, edit `/etc/fstab` on that system and add a line like this:

```bash
//192.168.1.100/Shared /mnt/pi_shared cifs username=pi,password=YourPassword,iocharset=utf8,uid=1000,gid=1000 0 0
```

Replace IP address, username, and password as needed.

---

## Step 10 — Summary

You’ve now set up Samba on your Raspberry Pi!  
- **`/home/pi/Shared`** — password-protected share  
- **`/home/pi/Public`** — optional guest share  
- Accessible from Windows, macOS, or Linux.

Your Raspberry Pi is now a mini file server on your network. 🎉

---

**Author:** James Eddy  
**License:** MIT  
**Tested on:** Raspberry Pi OS (32-bit and 64-bit, Bookworm and Bullseye)
