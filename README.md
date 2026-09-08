<p align="center">
  <img src="icon.png" alt="realme logo" width="96" />
</p>

# Realme Smart Scale — Home Assistant

Home Assistant custom integration for the **realme Smart Scale RMH2011**.
It connects to the scale over Bluetooth and exposes live weight and
impedance readings, locally derived body-composition estimates, per-user
sensors, measurement history and automatic multi-user attribution.

## Features

- Bluetooth LE / GATT connectivity (active connection session)
- Live weight and impedance measurements
- Local body-composition estimates (body fat, muscle, water, bone, lean
  body mass, visceral fat, BMI, BMR and more)
- Multiple scale users, each with their own Home Assistant device
- Automatic measurement attribution using weight + impedance matching
- Manual assignment and reassignment of measurements
- Optional linking of users to Home Assistant People
- Persistent measurement history
- User removal with complete HA entity/device/storage cleanup
- HACS update-safe persistent configuration
- Options flow for managing users and settings
- Diagnostic entities and scale-level status entities
- Measurement events, assignment and reconnect services
- Confirmation blueprint for unassigned measurements

## Supported scale

| Model | Connection |
| --- | --- |
| realme Smart Scale RMH2011 | Bluetooth LE / GATT |

This integration targets the **RMH2011** model. It is not tested against
other realme scale models and does not claim support for them.

## Requirements

- Home Assistant with Bluetooth support
- A realme Smart Scale RMH2011
- A Bluetooth adapter that can maintain an **active GATT connection**

The RMH2011 requires an active GATT connection for its handshake and live
measurement stream:

- BLE *advertisements* can be discovered through supported Home Assistant
  Bluetooth sources.
- The measurement session needs an actively connectable GATT client.
- A passive, advertisement-only Bluetooth proxy is **not** sufficient for
  the active session.

## Installation

### HACS

1. Open HACS.
2. Open **Integrations**.
3. Search for **Realme Smart Scale** and install it.
4. Restart Home Assistant.
5. Go to **Settings → Devices & services** and add **Realme Smart Scale**.

If the repository is not listed in the HACS default repository set, add it
as a **custom repository** (category *Integration*):

```
https://github.com/kapilmahawar/realme-scale-ha
```

### Manual

1. Download the latest release from the
   [releases page](https://github.com/kapilmahawar/realme-scale-ha/releases).
2. Copy the `custom_components/realme_scale` folder into your Home
   Assistant `config/custom_components/` directory.
3. Restart Home Assistant and add the integration.

## Configuration

1. Add the integration and either pick the discovered scale or enter its
   Bluetooth address (`AA:BB:CC:DD:EE:FF` format).
2. Create the **first user** and configure:
   - sex, age, height, activity level
   - optional initial/expected weight and identification tolerances
3. Save the integration.

Additional users and settings are managed afterwards:

```
Settings → Devices & services → Realme Smart Scale → Configure
```

## Multiple users

- Several scale users can be configured. Each user is a profile with a
  stable internal id and receives their own Home Assistant device under the
  physical scale.
- Users can optionally be linked to an existing Home Assistant **Person**.
- Automatic attribution matches each measurement against configured users
  using **weight** (identity ranges) and, when available, **impedance**.
- Ambiguous measurements (several plausible users, or none) stay
  **unassigned** — they are never silently attributed.
- Unassigned measurements can be assigned manually, and wrong assignments
  can be corrected by reassigning them to the right user.

### Active profile / handshake profile

The **handshake profile** is the profile whose sex / age / height etc. is
written to the scale during the Bluetooth handshake. It is **not**
necessarily the person standing on the scale: attribution decides who owns
a measurement, independently of the handshake profile.

## Removing a user

Removing a user permanently deletes:

- that user's stored measurement records
- that user's Home Assistant entities
- that user's Home Assistant device
- that user's runtime state

The physical scale device and other users are **not** affected.

Removal is performed through:

```
Options → Remove User → Save & Close
```

**This operation cannot be undone.**

## Data persistence

- User configuration is stored in the Home Assistant config entry.
- Measurement records are stored using Home Assistant's persistent storage.
- No data is stored inside the installed `custom_components` directory.

The integration is designed so that normal HACS updates and Home Assistant
restarts do not reset configured users or stored measurement history.

## Entities

Entities are grouped per user device, plus a physical scale device.

### User measurements

Weight · Body Fat · Muscle · Water · Bone Mass · Lean Body Mass ·
Visceral Fat · Impedance · Last Measured

### Derived metrics

BMI · Body Fat Mass · BMR · Ideal Weight · Protein

### Interpretations

BMI Category · Body Fat Category · Body Type · Body Water Category ·
Visceral Fat Category

### Scale-level entities

Connection status · Unassigned Measurements · Handshake profile (active
user)

Device structure:

```
Realme Smart Scale
├── <user 1>
├── <user 2>
└── ...
```

Home Assistant creates per-user entities automatically and preserves
existing entity registry ids across UI metadata changes. Generic examples:

```
sensor.<user>_weight
sensor.<user>_body_fat
```

## Home Assistant People

Users can optionally be linked to existing Home Assistant Person entities.
This associates scale profiles with your People without the integration
creating or managing People itself.

## Automations, events and services

An event `realme_scale_measurement` is fired for each measurement and
includes fields such as:

- `measurement_id`, `status`, `weight`, `measured_at`
- `user`, `user_id` when assigned
- `candidate_users`, `candidate_user_ids` for unassigned/ambiguous readings
- `assignment_method`, `confidence`, and derived metrics when applicable

Services:

- `realme_scale.reconnect` — close and re-establish the Bluetooth session.
- `realme_scale.assign_measurement` — assign or reassign a stored
  measurement to a user (`measurement_id`, `user` name or id).

## Blueprint

A confirmation blueprint is included:

```
blueprints/realme_scale_confirm_measurement.yaml
```

When a measurement cannot be attributed automatically it sends a
notification with quick actions per likely user; tapping one assigns the
measurement. Import the blueprint under
**Settings → Automations → Blueprints**, then create an automation from
it. Action buttons require Home Assistant mobile notifications
(`notify.<notification_service>`); without actions the message still tells
you where to review unassigned measurements.

## Derived metrics

| Metric | Method |
| --- | --- |
| BMI | Weight / height² |
| BMR | Schofield (WHO) |
| Ideal weight | Devine |
| Body fat mass | Weight × body-fat % |
| Protein | Lean-mass based estimate |
| Body-fat category | Age/sex reference ranges |
| Water category | Reference hydration ranges |
| Visceral-fat category | Threshold-based interpretation |

## Medical disclaimer

> This integration is **not a medical device**. Body-composition values are
> estimates derived from bioelectrical impedance and generic formulas.
> They are intended for personal tracking only and should not be used to
> diagnose, treat, or prevent medical conditions.

## Technical details

<details>
<summary>BLE/GATT and protocol summary</summary>

- Service: `0000a602-0000-1000-8000-00805f9b34fb`
- Characteristics:
  - `a621` — notifications (live measurements)
  - `a622` — keep-alive writes
  - `a624` — handshake / configuration writes
  - `a625` — notifications
- Active handshake with 6 commands, written after connection.
- Periodic keep-alive while connected.
- Live measurement packets carry weight, timestamp and impedance.
- Body composition is calculated locally from weight + impedance and the
  active user profile.

</details>

## Troubleshooting

### Scale not discovered

- Wake the scale (step on it once).
- Verify Bluetooth is available in Home Assistant.
- Verify the device is reachable as a *connectable* source.
- Check Home Assistant's Bluetooth diagnostics.

### Scale discovered but measurements do not arrive

- Confirm active GATT connectivity (a passive advertisement relay is not
  enough).
- Wake / step on the scale.
- Make sure no other application or device holds an exclusive connection.
- Check the Home Assistant logs.

### Measurement stays unassigned

- Review the user's profile (expected weight and tolerances).
- Use **Options → Assign unassigned measurements**.
- Consider enabling impedance matching to separate users of similar weight.

### Body composition unavailable

Weight-only packets cannot produce impedance-derived metrics. Confirm the
scale reports impedance (bare feet on the electrodes) and that impedance
sensors are not hidden/disabled.

## Debug logging

Add to `configuration.yaml` and restart Home Assistant:

```yaml
logger:
  default: warning
  logs:
    custom_components.realme_scale: debug
```

## Development / testing

The repository includes automated tests for the protocol, body-composition
math and flow/structure:

```
python -m pytest tests/ -v
```

## License and attribution

This project contains protocol and body-composition code derived from
[openScale](https://github.com/oliexdev/openScale)
(`RealmeSmartScaleHandler`, `YunmaiLib`), which is distributed under the
**GNU GPL v3**. This project is therefore licensed under the
**GNU GPL v3** — see [LICENSE](LICENSE).

This is an independent community project. It is not affiliated with,
endorsed by, or sponsored by realme, openScale, or Home Assistant.
