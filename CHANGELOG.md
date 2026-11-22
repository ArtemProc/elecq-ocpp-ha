# Changelog – Elecq AU101 OCPP Integration

## v1.0.6 – Dependency fix
- Fixed config flow failing on current Home Assistant by updating `ocpp` to 2.1.0
- `ocpp` 0.16.0 imported `jsonschema._validators`, which newer jsonschema versions removed

## v1.0.5 – Branding
- Removed BashTheDog branding from the integration badge image

## v1.0.4 – Dashboard
- Added a ready-to-paste dashboard using only built-in Home Assistant cards
- Corrected the documented entity IDs
- Documentation now points to the Gitea repository

## v1.0.0 – Initial Release
- Full OCPP 2.0.1 WebSocket server embedded in Home Assistant
- Real-time charger status updates
- EV plugged-in detection
- Accurate charging state (`Charging`, `EVConnected`, `SuspendedEV`, `Idle`)
- Power & energy monitoring (instant + smoothed)
- Remote start/stop charging via OCPP commands
- Safe switch behavior (grey-out while awaiting charger)
- Session energy tracking
- Status, binary sensor, and switch entities created
- Fully local processing (no cloud)
- Added README, info.md, badges, and integration icons
