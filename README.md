# Remote System Monitor (RSM) - Home Assistant Integration

[![HACS Validation](https://github.com/r3ferrei/Remote-System-Monitor-HACS-Addon/actions/workflows/hacs.yml/badge.svg)](https://github.com/r3ferrei/Remote-System-Monitor-HACS-Addon/actions/workflows/hacs.yml)
[![Hassfest Validation](https://github.com/r3ferrei/Remote-System-Monitor-HACS-Addon/actions/workflows/hassfest.yml/badge.svg)](https://github.com/r3ferrei/Remote-System-Monitor-HACS-Addon/actions/workflows/hassfest.yml)
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/default)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A native Home Assistant Custom Integration for **TRIGONE Remote System Monitor Server** (v3.98, protocol 311).

It communicates **directly** with the Windows Remote System Monitor server over TLS (default port `19150`) using its native binary protocol. **No MQTT broker or external services are needed** — telemetry is pulled directly into Home Assistant's Entity and Device registries with minimum latency.

---

## ✨ Key Features

- **Direct Socket Connection**: Connects straight to the RSM Server over TLS 1.2 using secure two-stage SHA-512 challenge-response authentication.
- **Hierarchical Device Organization**:
  - Automatically creates a root device for your **Computer Host**.
  - Creates dedicated child devices for **CPU**, **GPU** (NVIDIA, AMD, Intel), **Storage Disks**, and **Motherboard** via Home Assistant's `via_device` registry.
- **Rich Telemetry & Entity Types**:
  - 🌡️ **Temperatures** (CPU Cores, GPU, Motherboard, Hard Drives)
  - ⚡ **Voltages & Power** (CPU VCore, GPU Power Draw, Motherboard rails)
  - 🔄 **Fans & Pumps** (RPM speed, control percentages)
  - 📊 **Usage & Clocks** (CPU load %, GPU core/memory load, RAM usage)
  - 💾 **Storage & Disk I/O** (Data usage in GB/MB, read/write rates, S.M.A.R.T. health)
  - 🌐 **Network Throughput** (Live download and upload rates in KB/s)
- **Dynamic Sensor Discovery**: Automatically detects and registers newly appearing hardware, disks, or processes on-the-fly without requiring a Home Assistant restart.
- **Config Flow & Options Flow**: Configure IP, port, server password, and polling rate (1s to 60s) directly through the Home Assistant user interface.

---

## 📦 Installation

### Method 1: Via HACS (Recommended)

#### Option A: As a Custom Repository
1. In Home Assistant, open **HACS** > **Integrations**.
2. Click the three dots icon in the top right corner and select **Custom repositories**.
3. In the **Repository** field, enter your GitHub repository URL:
   ```
   https://github.com/r3ferrei/Remote-System-Monitor-HACS-Addon
   ```
4. In the **Type** dropdown, select **Integration**.
5. Click **Add**, locate **Remote System Monitor**, and click **Download**.
6. Restart Home Assistant.

#### Option B: Default HACS Store
This repository is configured to comply with all [HACS Publishing Guidelines](https://www.hacs.xyz/docs/publish/). To publish to the official HACS directory:
1. Ensure GitHub Actions (`HACS Action` and `Hassfest`) pass on your repository.
2. Create a tagged GitHub Release (e.g. `v1.0.0`).
3. Submit a pull request adding your repository to the `integration` list in [hacs/default](https://github.com/hacs/default).

---

### Method 2: Manual Installation

1. Download the latest release `.zip` or clone this repository.
2. Copy the `custom_components/remote_system_monitor` folder into your Home Assistant configuration directory:
   ```
   <config_dir>/custom_components/remote_system_monitor/
   ```
3. Restart Home Assistant.

---

## ⚙️ Configuration

1. In Home Assistant, navigate to **Settings** > **Devices & Services**.
2. Click **Add Integration** in the bottom right corner.
3. Search for **Remote System Monitor**.
4. Fill in the connection form:
   - **Host / IP Address**: The IP address or hostname of your Windows PC running Remote System Monitor (e.g., `192.168.1.100`).
   - **Port**: Remote System Monitor port (default: `19150`).
   - **Password**: The password configured in the Remote System Monitor Server Control Panel.
   - **Polling Interval (seconds)**: Desired update frequency (default: `5` seconds; range: `1`–`60`s).
5. Click **Submit**. Home Assistant will authenticate directly with the server and automatically discover all hardware components and sensors!

### Adjusting Polling Rate Later
To change the refresh interval without re-adding the integration:
- Go to **Settings** > **Devices & Services** > **Remote System Monitor**.
- Click **Configure** to adjust the polling interval in seconds.

---

## 🖥️ Standalone Python Client (Testing / Debugging)

For debugging or inspecting the raw telemetry stream outside of Home Assistant, a standalone test client is included in the `tests/` directory:

```bash
# Prompt for password interactively:
python tests/test_client.py --host 192.168.1.100

# Or supply credentials directly:
python tests/test_client.py --host 192.168.1.100 --password "YOUR_PASSWORD"

# Output raw telemetry snapshot JSON:
python tests/test_client.py --host 192.168.1.100 --password "YOUR_PASSWORD" --json
```

---

## 📋 HACS Publishing Checklist

- [x] Repository includes `hacs.json` in the root.
- [x] Integration files reside under `custom_components/remote_system_monitor/`.
- [x] `manifest.json` includes `domain`, `name`, `codeowners`, `config_flow`, `iot_class`, `version`, and documentation links.
- [x] Brand assets (`icon.png` and `logo.png`) provided in `custom_components/remote_system_monitor/brand/`.
- [x] All configuration is managed via Config Flow and Options Flow (no deprecated YAML configuration).
- [x] Uses non-blocking socket execution via `hass.async_add_executor_job`.
- [x] GitHub Action workflows included for `.github/workflows/hacs.yml` and `.github/workflows/hassfest.yml`.
- [x] Open source license included (`LICENSE`).

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
