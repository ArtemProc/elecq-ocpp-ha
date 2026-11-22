# Elecq charger dashboard — design

**Date:** 2026-11-22
**Status:** proposed, awaiting approval
**Size:** small change (one dashboard YAML file plus documentation)

## Intent

Give an Elecq charger owner a working dashboard they can paste into Home Assistant, with no extra frontend cards and no entity-ID hunting on a default install. The layout is the "control center" two-column option (B) chosen from the mockups.

## Why

The integration creates nine entities but ships no dashboard. The only example is a stale card snippet in `README.md` / `info.md` whose entity IDs no longer match what the current code generates. A dashboard is the visible payoff of installing the integration.

## Goals

- Ship a complete, ready-to-paste dashboard at `custom_components/elecq_ocpp/dashboards/elecq_ocpp.yaml`.
- Two columns on wide screens, stacked on phones, using the built-in `sections` view.
- Left: live power gauge, charging state, remote charging switch.
- Right: charger status, plugged-in, charging, session energy, total energy, smoothed power.
- Full-width 24-hour power history underneath.
- Use only built-in cards: `heading`, `gauge`, `tile`, `history-graph`.
- Replace the stale README / `info.md` example with installation steps and the correct default entity IDs.
- Work for AU101, AE104 and AE106: the entity set and default IDs do not depend on the charger model.

## Non-goals

- No custom Lovelace cards (no `auto-entities`, no graph cards, no frontend JS).
- No custom dashboard strategy, and no automatic dashboard creation.
- No integration code changes and no entity renaming; existing installs must keep their IDs.
- No multiple-charger dashboard; the integration supports one connected charge point.

## Anti-goals

- A dashboard that silently shows broken or unavailable entities.
- Requiring a second HACS component for the default experience.
- Hiding charger-specific values (gauge maximum) without telling the user to adjust them.

## Constraints

- The dashboard's `heading` cards require Home Assistant 2024.10 or newer (the sections view itself is 2024.3+, but heading cards were added in 2024.10). The integration's own minimum stays at 2024.1; the README states the dashboard requirement.
- Default entity IDs come from the hardcoded device name `Elecq AU101 Charger` plus each entity name. For a fresh install that gives:

| Entity | Default ID |
|---|---|
| Power | `sensor.elecq_au101_charger_power` |
| Smoothed power | `sensor.elecq_au101_charger_power_smoothed` |
| Total energy | `sensor.elecq_au101_charger_total_energy` |
| Session energy | `sensor.elecq_au101_charger_session_energy` |
| Charger status | `sensor.elecq_au101_charger_charger_status` |
| Charging state ("Info") | `sensor.elecq_au101_charger_info` |
| Plugged in | `binary_sensor.elecq_au101_charger_plugged_in` |
| Charging | `binary_sensor.elecq_au101_charger_charging` |
| Remote charging | `switch.elecq_au101_charger_remote_charging` |

- Renaming a device/entity, or assigning an area before the entities are created, can change these IDs. The README will include a one-step find/replace fallback.
- The gauge maximum of 22 kW matches the AE104. Owners of 7.4 kW or 11 kW chargers adjust one line; the README will say so.
- HACS installs the `custom_components/elecq_ocpp` folder, not repository-root files, so the dashboard must live inside that folder to arrive with a HACS install.

## Approaches considered

- **A — static YAML using default IDs. Chosen.** One file, only built-in cards, works on any supported HA version, and matches what HACS integration users expect. Trade-off: breaks if a user renamed entities; mitigated by documentation.
- **B — static YAML plus the `auto-entities` HACS card.** Survives renames, but adds a frontend dependency for a nine-entity integration. Rejected.
- **C — a dashboard strategy shipped and served by the integration.** Truly zero-setup and rename-proof, but needs frontend JavaScript, Home Assistant 2026.5+, and extra testing; disproportionate here. Rejected.

## Design

The dashboard is one `sections` view:

- `title: Elecq Charger`, one view titled `Charger`, icon `mdi:ev-station`, `max_columns: 2`.
- **Section 1 — Control** (`column_span: 1`): heading; gauge of `power` with `min: 0`, `max: 22`, `needle: true`; tile of the charging-state sensor; tile of the remote charging switch with the `toggle` feature.
- **Section 2 — Details** (`column_span: 1`): heading; six tiles (status, plugged in, charging, session energy, total energy, smoothed power). Tiles default to half-width, so they form a two-column grid.
- **Section 3 — History** (`column_span: 2`): heading; `history-graph` with raw and smoothed power, `hours_to_show: 24`, `min_y_axis: 0`.

Card names override the integration's entity names where they are unclear (`Info` becomes "Charging state", `Charger Status` becomes "Charger status").

## My calls

- Put the YAML inside `custom_components/elecq_ocpp/dashboards/` so HACS installs it; the README links to both the installed file and GitHub.
- Use `sections` rather than `grid` so the layout reacts to screen width without custom cards.
- Include all nine entities, even where two of them overlap with the switch state.
- Keep a 22 kW gauge maximum with a documented one-line adjustment.
- Do not bump `manifest.json`; this is a documentation/dashboard addition, not a code release.
- Update both `README.md` and `info.md`; leave the entity names in the integration alone to avoid breaking existing installs.

## Verification

- Parse the dashboard YAML with PyYAML; fail on syntax errors.
- Check every referenced entity ID is in the expected nine-ID set, and that card types are limited to the four built-in cards above.
- Paste the file into a Home Assistant 2024.10+ instance with the integration and confirm all cards render, the switch toggles, and the history graph populates.
- Confirm the README instructions work from a clean dashboard and that the old stale snippet is gone.

## Left to the builder

Exact icons, heading text, YAML ordering/formatting, and README prose.
