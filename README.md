# Elecq AU101/AE106 OCPP – Home Assistant Custom Integration

<p>
  <img src="https://raw.githubusercontent.com/BashTheDog/elecq-ocpp-ha/main/assets/elecq_badge_square.png" width="160" alt="Elecq Logo">
</p>

[![GitHub Release](https://img.shields.io/github/v/release/BashTheDog/elecq-ocpp-ha?style=for-the-badge)](https://github.com/BashTheDog/elecq-ocpp-ha/releases)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-blue.svg?style=for-the-badge)](https://hacs.xyz/)
[![GitHub Stars](https://img.shields.io/github/stars/BashTheDog/elecq-ocpp-ha?style=for-the-badge)](https://github.com/BashTheDog/elecq-ocpp-ha/stargazers)

---

# ⚡ Elecq AU101/AE106 OCPP Integration

A Home Assistant integration for the **Elecq AU101/AE106** EV Charger using an embedded **OCPP 2.0.1 WebSocket server**.  This project was developed for AU101, but a user report indicates it also works on AE106.

This integration provides:

- Real-time **charger status**
- EV **plugged-in** detection
- Detailed **charging state** (`Charging`, `SuspendedEV`, `Idle`, etc.)
- **Start/Stop charging** with true OCPP commands
- Accurate power and energy tracking
- Greyed-out switch while waiting for charger responses
- No cloud required — fully local

---

# 🖼 Integration Logo (Smaller)

<p>
  <img src="https://raw.githubusercontent.com/BashTheDog/elecq-ocpp-ha/main/assets/elecq_badge_square.png" width="160" alt="Elecq Logo">
</p>

---

# 🚀 Features

### 🟢 Charger Status
- Available / Occupied / Faulted / Unavailable

### 🔌 EV Plug Detection
- Instant plug/unplug recognition  
- Uses `stoppedReason: EVDisconnected` for accuracy

### 🔋 Charging State
- Real Charging
- EVConnected
- SuspendedEV → **Possibly fully charged**
- Idle / Finished

### ⚡ Energy Metrics
- Real-time Power (kW)
- Smoothed Power
- Total Energy (kWh)
- Session Energy

### 🆘 Smart Charging Switch
- Reflects *actual* charger state  
- Grey-out while awaiting OCPP response  
- Safe rejection when EV is full or unplugged  

---

# 📦 Installation

## Option A — HACS (Custom Repository)

1. Open **HACS → Integrations**
2. Add custom repository:

```
https://github.com/BashTheDog/elecq-ocpp-ha
```

3. Select category: **Integration**
4. Install & restart Home Assistant

---

## Option B — Manual Installation

Copy:

```
custom_components/elecq_ocpp
```

into:

```
<config>/custom_components/elecq_ocpp/
```

Restart Home Assistant.

---

# 🔧 Configuration

Open **Settings → Devices & Services → Add Integration → Elecq OCPP**

| Field | Meaning |
|-------|---------|
| Port | WebSocket port (default `9006`) |
| ID Token | Used in RequestStartTransaction |
| EVSE ID | Typically `1` |
| Connector ID | Typically `1` |

---

# 🔗 Elecq Charger OCPP Setup

In the Elecq mobile app, set:

```
ws://<home-assistant-ip>:9006/AU101B2G00127D
```

OCPP Version: **2.0.1**

---

# 🧩 Entities

The integration always creates the same nine entities. Entity IDs use the device name
`Elecq AU101 Charger`, so each ID is prefixed with `elecq_au101_charger_`.

### Sensors
- `sensor.elecq_au101_charger_power`
- `sensor.elecq_au101_charger_power_smoothed`
- `sensor.elecq_au101_charger_total_energy`
- `sensor.elecq_au101_charger_session_energy`
- `sensor.elecq_au101_charger_charger_status` (registry name "Charger Status", hence `charger_charger`)
- `sensor.elecq_au101_charger_info` (charging state; registry name "Info")

### Binary Sensors
- `binary_sensor.elecq_au101_charger_plugged_in`
- `binary_sensor.elecq_au101_charger_charging`

### Switch
- `switch.elecq_au101_charger_remote_charging`

---

# 🖥 Dashboard

A ready-to-paste dashboard ships with the integration. It uses only built-in Home
Assistant cards and the nine default entity IDs above.

## Install (UI)

1. Go to **Settings → Dashboards → Add dashboard → New dashboard from scratch**.
2. Open the new dashboard, select **Edit dashboard**, and take control if prompted.
3. Open the three-dot menu and choose **Raw configuration editor**.
4. Replace everything with the contents of
   `custom_components/elecq_ocpp/dashboards/elecq_ocpp.yaml`.
5. Select **Save**.

The file is installed with the integration. You can also copy it from GitHub:
[elecq_ocpp.yaml](https://github.com/BashTheDog/elecq-ocpp-ha/blob/main/custom_components/elecq_ocpp/dashboards/elecq_ocpp.yaml).

## Install (YAML mode)

If you manage dashboards in YAML, copy the file to `dashboards/elecq_ocpp.yaml` in
your Home Assistant config folder, then add:

```yaml
lovelace:
  dashboards:
    elecq:
      mode: yaml
      title: Elecq
      icon: mdi:ev-station
      show_in_sidebar: true
      filename: dashboards/elecq_ocpp.yaml
```

## Notes

- The dashboard uses a **sections** view, which requires Home Assistant **2024.3** or newer.
- The gauge maximum is `22` kW, matching the AE104. If your charger is 7.4 kW or 11 kW,
  change `max: 22` in the gauge card to `7.4` or `11` so the gauge reads usefully.
- If you renamed the device or any entity, update the nine IDs in the YAML (or use your
  own entity IDs). The defaults are listed in the Entities section above.

---

# 🛠 Development

```bash
git clone https://github.com/BashTheDog/elecq-ocpp-ha
```

---

# 🏷 Versioning

- Follows Semantic Versioning  
- Version must match `manifest.json`  
- Releases should follow `vX.Y.Z`

---

# 📄 License

MIT License © 2025 **BashTheDog**
