<p align="center">
  <img src="icon.png" alt="Realme Smart Scale" width="96" />
</p>

# Realme Smart Scale

Home Assistant integration for the **realme Smart Scale RMH2011**, providing
Bluetooth measurements, multi-user support, body-composition metrics,
persistent history, and Home Assistant automations.

[![GitHub Release](https://img.shields.io/github/v/release/kapilmahawar/realme-scale-ha)](https://github.com/kapilmahawar/realme-scale-ha/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/kapilmahawar/realme-scale-ha)
[![License: GPL v3](https://img.shields.io/github/license/kapilmahawar/realme-scale-ha)](LICENSE)
[![CI](https://github.com/kapilmahawar/realme-scale-ha/actions/workflows/ci.yml/badge.svg)](https://github.com/kapilmahawar/realme-scale-ha/actions/workflows/ci.yml)

The integration talks to the scale over Bluetooth LE / GATT, decodes live
measurements locally, computes body-composition estimates on the Home
Assistant side, and keeps every reading in persistent per-user history —
all without cloud services.

---

## Features

### Measurements

- **Weight** and **impedance** live readings over Bluetooth
- **Local body-composition estimates** computed from weight + impedance
- **Persistent measurement history** per user
- **Offline last-known-value behavior**: once a valid measurement exists,
  sensors keep their last known value and stay available while the scale
  sleeps or disconnects; the `Connected` binary sensor independently reports
  link state

### Multi-user

- Multiple scale users, each with their **own Home Assistant device**
- **Automatic measurement attribution** (weight matching, plus impedance
  when both readings carry it)
- **Manual assignment** and **reassignment** of measurements
- Optional linking of users to existing Home Assistant **People**
- Ambiguous or unrecognized readings stay **unassigned** — nothing is
  silently guessed
- **Date of birth based profiles**: the current age is derived from the
  stored date of birth, so age never needs a manual yearly update

### Body composition

All computed locally from weight, impedance and the active user profile:

| Group | Metrics |
| --- | --- |
| Core | Body fat, muscle, body water, bone mass, lean body mass, visceral fat |
| Derived | BMI, body-fat mass, BMR, ideal weight, protein |
| Interpretation | BMI category, body-fat category, body-type, body-water level, visceral-fat rating |

### Home Assistant

- **Events** (`realme_scale_measurement`) and **services** (reconnect,
  assign/reassign) for automations
- Included **automation blueprint** to confirm unassigned measurements
- Per-user **devices** under the physical scale device, with diagnostics
- **Persistent configuration** that survives HACS updates and restarts

---

## Supported hardware

### realme Smart Scale RMH2011

| Property | Details |
| --- | --- |
| Model | realme Smart Scale RMH2011 |
| Connection | Bluetooth LE / GATT (active connection) |
| Measurements | Weight, impedance |
| Body composition | Calculated locally |
| Multi-user | Supported |

Only the RMH2011 is supported. Other realme scale models are **not** tested
and are not claimed to work.

---

## Requirements

- Home Assistant with Bluetooth support
- HACS or manual installation
- A Bluetooth adapter able to maintain an **active GATT connection**
- A realme Smart Scale RMH2011

The RMH2011 requires an *active* Bluetooth GATT session for its handshake
and live measurement stream. BLE *advertisements* can be picked up from
supported Home Assistant Bluetooth sources, but a passive
advertisement-only relay is **not sufficient** — the integration must be
able to connect to the scale. If the scale is unreachable through a
passive proxy, use a locally attached Bluetooth adapter or a proxy that
supports active connections.

---

## Installation

### HACS — Recommended

1. Open HACS.
2. Open **Integrations**.
3. Search for **Realme Smart Scale**.
4. Download.
5. Restart Home Assistant.
6. Go to **Settings → Devices & services**.
7. Add the **Realme Smart Scale** integration.

If the repository is not listed in the HACS default set yet, add it as a
**custom repository** (category *Integration*):

```
https://github.com/kapilmahawar/realme-scale-ha
```

### Manual

1. Download the latest release from the
   [releases page](https://github.com/kapilmahawar/realme-scale-ha/releases).
2. Copy the `custom_components/realme_scale` folder into
   `config/custom_components/`.
3. Restart Home Assistant, then add the integration.

---

## Configuration

1. Add the integration and pick the discovered scale, or enter its Bluetooth
   address (`AA:BB:CC:DD:EE:FF`).
2. Create the **first user** — a profile with sex, date of birth, height and
   activity level, plus an optional expected weight and identification
   tolerances. You may link it to an existing Home Assistant **Person**.
   Your age is calculated automatically from your date of birth, so you do
   not need to update it every year.
3. Add further users later under
   **Settings → Devices & services → Realme Smart Scale → Configure**.

From the options screen you can add users, edit profiles, link or change
People, remove users, set the handshake profile, adjust automatic
identification defaults, assign or reassign measurements, and **Save &
Close** to apply everything.

**Existing users:** profiles created before date-of-birth support keep using
their stored age until you enter a date of birth once under
*Options → Edit user*. The profile keeps working normally in the meantime
— nothing is guessed or overwritten.

> Three different roles should not be confused:
>
> - a **configured user** is a profile you created;
> - the **handshake profile** is the profile whose sex/date of birth/height
>   is written into the scale during the Bluetooth handshake (age is
>   calculated from the date of birth);
> - the **attributed user** is who automatic identification decides was on
>   the scale for a given measurement.
>
> The handshake profile is *not* necessarily the person standing on the
> scale — attribution decides that independently.

---

## Multiple users

Each user is a profile with a stable internal id, stored in the config
entry. Home Assistant shows one device per user under the physical scale:

```text
Realme Smart Scale
├── Alice
├── Bob
└── Charlie
```

Automatic attribution matches every measurement against the configured
users using weight (identity ranges) and, when the reading carries
impedance, the impedance tolerance as well:

- a reading matching exactly one user is assigned to them;
- a reading matching nobody, or matching several users, stays **unassigned**
  — it is never silently attributed;
- unassigned measurements can be **assigned manually**, and a wrong
  assignment can be fixed by **reassigning** the measurement to the right
  user (its body-composition values are recalculated for that user).

---

## Removing a user

Removing a user is permanent and deletes:

- their stored measurement records
- their Home Assistant entities
- their Home Assistant device
- their runtime state

The physical scale device and the other users are **not** affected. Removal
only happens from **Options → Remove user → Save & Close** — never during
setup, updates or restarts — and **cannot be undone**.

---

## Data persistence

- Users live in the Home Assistant **config entry** (survive HACS updates
  and restarts).
- Measurement records live in Home Assistant's persistent `.storage`
  (via `Store`) — never inside the `custom_components` directory.
- The integration creates and manages only its own entities/devices; linked
  Home Assistant People are existing entities that stay under your control.

---

## Devices & entities

Entities are grouped on per-user devices under the physical **Realme Smart
Scale** device:

```text
Realme Smart Scale
├── Realme Smart Scale          ← scale device
│   ├── sensor.*_weight
│   └── ...                     ← measurement / derived / interpretation
├── Alice                       ← per-user device
├── Bob
└── Charlie
```

Per user, the integration provides:

- **Measurements**: weight, body fat, muscle, body water, bone mass, lean
  body mass, visceral fat, impedance, and a *Last Measured* diagnostic
- **Derived metrics**: BMI, body-fat mass, BMR, ideal weight, protein
- **Interpretations**: BMI category, body-fat category, body type, body
  water level, visceral-fat rating

Scale-level entities: a `Connected` binary sensor (actual BLE link state), an
*Unassigned Measurements* count, and the handshake-profile select.

### Offline behavior

Sensors retain their **last known value** while the scale is disconnected —
history stays continuous and entities remain `available` once a valid value
exists. They become `unavailable` only when no valid value has ever been
recorded for them (for example, weight-only packets never produce the
impedance-derived metrics).

---

## Automations

### Event

Every measurement fires a `realme_scale_measurement` event with fields such
as `measurement_id`, `status`, `weight`, `measured_at`, the assigned `user`
/ `user_id`, `candidate_users` / `candidate_user_ids` for
unassigned/ambiguous readings, `assignment_method`, `confidence` and the
computed metrics where applicable.

### Services

- `realme_scale.reconnect` — close and re-establish the Bluetooth session.
- `realme_scale.assign_measurement` — assign or reassign a stored
  measurement to a user (`measurement_id`, plus `user` name or id).

### Blueprint

[`blueprints/realme_scale_confirm_measurement.yaml`](blueprints/realme_scale_confirm_measurement.yaml)
sends a notification when a measurement could not be attributed
automatically, with one quick action per likely user. Import it under
**Settings → Automations → Blueprints** and create an automation from it.
Quick actions require a mobile notification service (`notify.<service>`);
without actions, the message still points to where unassigned measurements
can be reviewed.

---

## Troubleshooting

### Scale not discovered

- Wake the scale (step on it once).
- Check that Home Assistant has Bluetooth available and that the scale
  appears as a *connectable* source.
- Look at Home Assistant's Bluetooth diagnostics for the device.

### Scale discovered, but no measurements arrive

- Confirm an **active GATT connection** is possible (passive advertisement
  relays are not enough).
- Wake / step on the scale.
- Make sure no other app or device holds an exclusive connection.
- Check the Home Assistant log.

### A measurement stays unassigned

- Review the user profile (expected weight and tolerances).
- Use **Options → Assign measurement**.
- Consider impedance matching to separate users of similar weight.

### Body-composition sensors unavailable

Weight-only packets cannot produce impedance-derived metrics. Step on the
scale with bare feet on the electrodes and make sure the impedance sensors
are not disabled. Entities only become unavailable when no valid value
exists.

---

## Debug logging

Add to `configuration.yaml` and restart Home Assistant:

```yaml
logger:
  default: warning
  logs:
    custom_components.realme_scale: debug
```

---

## Medical disclaimer

> This integration is **not a medical device**. Body-composition values are
> estimates derived from bioelectrical impedance measurements and generic
> formulas. They are intended for personal tracking only and must not be
> used to diagnose, treat or prevent any medical condition.

---

## Technical summary

<details>
<summary>BLE / GATT and protocol notes</summary>

- Service: `0000a602-0000-1000-8000-00805f9b34fb`
- Characteristics:
  - `a621` — notifications (live measurements)
  - `a622` — keep-alive writes
  - `a624` — handshake / configuration writes
  - `a625` — notifications
- An active handshake (6 commands) is written after connecting, with a
  periodic keep-alive while connected.
- Live packets carry weight, scale timestamp and impedance.
- Body composition is computed locally from weight + impedance and the
  active user profile.

</details>

---

## Development

The repository ships unit tests for the protocol decoder, body-composition
math, identification/assignment engine, records storage and UI metadata:

```
python -m pytest tests/ -v
```

---

## License & attribution

This project contains protocol and body-composition code derived from
[openScale](https://github.com/oliexdev/openScale)
(`RealmeSmartScaleHandler`, `YunmaiLib`), which is distributed under the
**GNU GPL v3**. This project is therefore licensed under the
**GNU GPL v3** — see [LICENSE](LICENSE).

This is an independent community project. It is not affiliated with,
endorsed by, or sponsored by realme, openScale or Home Assistant.
