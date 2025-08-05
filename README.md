# AdProcess

AdProcess is a Python-based automated media display system designed for Raspberry Pi devices. It plays scheduled videos in a continuous loop, syncing content from a shared network folder and selecting ads based on time of day, day of week, and custom scheduling logic.

This system is built for signage-style deployments like bars, lounges, or retail, with each Pi set up for unattended operation, content updates, and video playback.

---

## 1. Flash Pi OS to SD Card

Use [Raspberry Pi Imager](https://www.raspberrypi.com/software/) to flash Raspberry Pi OS.

While flashing:

- ⚙️ Click the **gear icon** to pre-configure:
  - **Username:** Use your company’s short name — e.g., `astepup`, `bking`, `wab`
    - `pi` is **allowed**, but not recommended.
    - None of the scripts require `astepup` specifically.
  - **Password:** Your choice
  - **Wi-Fi SSID and Password**
  - **Hostname:** Give the Pi a *meaningful* name like:
    - `BarTV`
    - `StageTV`
    - This way your terminal prompt looks like:
      ```
      astepup@BarTV:~ $
      astepup@StageTV:~ $
      ```

- 📄 **Before flashing**, copy `install_adprocess.sh` into the SD card’s `/boot` partition.

  You can download the latest `install_adprocess.sh` script here:
  https://raw.githubusercontent.com/JamesMcFaddin/AdProcess/main/install_adprocess.sh
  

