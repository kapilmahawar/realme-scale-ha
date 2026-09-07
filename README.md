# Realme Smart Scale (RMH2011) — Home Assistant integration

A Home Assistant **custom integration** that reads the **Realme Smart Scale
RMH2011** over Bluetooth and exposes live measurements (weight + locally
derived body composition) as sensors.

The Bluetooth protocol is a byte-for-byte port of the
[openScale](https://github.com/oliexdev/openScale) `RealmeSmartScaleHandler`
(GPL-3.0), so behaviour matches what the openScale Android app does with the
same scale.

> ⚠️ This is a protocol port written against the reference openScale handler,
> not a "blessed" integration. It has **not yet been validated against a real
> scale** — treat the first run as a bring-up exercise (see *Testing* below).

---

## What the scale actually does (vs. what the blueprint assumed)

| OpenScale handler reality | Consequence |
|---|---|
| 6-step **active GATT handshake** written to `0xA624` right after connecting | HA must *connect* to the scale — passive advertisement sniffing is **not** enough |
| Continuous keep-alive `00 01 D9` on `0xA622` every 1 s | The link must be held open by the integration |
| Live measurements stream on `0xA621` (`0x10 0x11` header packets) | Weight/timestamp/impedance update while you stand on the scale |
| Scale only transmits **weight + impedance + timestamp** | Body fat / muscle / water / bone / LBM / visceral fat are **computed locally** (ported `YunmaiLib`) |
| No separate "fetch history" command in the handler | The blueprint's `sync_history` / `calibrate` services cannot be faithfully implemented — omitted deliberately |

## Connectivity requirements

The RMH2011 **does not broadcast** measurements like Xiaomi scales do. It only
talks to an actively connected GATT client, so:

1. **Discovery** can come from any HA Bluetooth source (local adapter **or**
   ESPHome BLE proxy) — the scale advertises as `realme Smart Scale` with
   service UUID `0000a602-...`.
2. **The data path (GATT) requires a *connectable* source.** A plain ESPHome
   proxy only relays advertisements; it cannot carry the handshake /
   keep-alive / notification session used here. If your ESP32-C3 runs an
   ESPHome BLE proxy, you also need the scale within range of the HA host's
   own Bluetooth adapter (or a proxy version that supports active
   connections). The config flow and setup will tell you when the scale is
   not currently connectable.

## Installation

1. Copy the `custom_components/realme_scale` folder into your HA
   `config/custom_components/` directory.
2. Restart Home Assistant.
3. **Settings → Devices & Services → Add Integration → Realme Smart Scale.**
   - The scale should appear in the discovery list. If it does not, wake the
     scale (step on it once) and retry, or add its MAC address manually.
4. Fill in the **user profile** — this is written into the scale handshake
   and drives the local body-composition math:
   - sex, age, height (cm), activity level (openScale's five levels —
     `heavy`/`extreme` select the "fitness" formula branch), and an optional
     initial weight in kg (leave `0` for "new user", which sends the
     `0xFFFF` sentinel like openScale does).
5. Change the profile later via the entry's **Options** (applies to the next
   connection / measurement).

## Entities

Sensors (all on one device):

| Entity | Unit | Notes |
|---|---|---|
| `sensor.<name>_weight` | kg | live weight from the scale |
| `sensor.<name>_body_fat` | % | locally computed from impedance |
| `sensor.<name>_muscle` | % | see quirk note below |
| `sensor.<name>_water` | % | locally computed |
| `sensor.<name>_bone_mass` | kg | locally computed |
| `sensor.<name>_lean_body_mass` | kg | locally computed |
| `sensor.<name>_visceral_fat` | – | unitless index |
| `sensor.<name>_impedance` | Ω | diagnostic (disabled by default) |
| `sensor.<name>_last_measured` | timestamp | measurement time from the scale |
| `binary_sensor.<name>_connected` | – | live GATT link state |

An event `realme_scale_measurement` is fired for every parsed packet so
automations can react without polling entities. Attributes of each sensor
carry `measured_at` and the configured `user`.

### Known quirk (faithful port)

The openScale Realme handler computes muscle as
`getMuscle(fat) / weight * 100` and feeds the *stored* muscle value back into
the bone-mass formula (unlike openScale's Yunmai handler, which stores
`getMuscle(fat)` directly). This port mirrors the Realme handler **exactly**
so HA shows the same numbers openScale shows for this device. If you ever
want the Yunmai convention instead, change one line in
`scale_controller.py` (`muscle_pct = calc.get_muscle(fat_pct)`).

## Services

- **`realme_scale.reconnect`** — drops the BLE session and immediately
  re-runs discovery + handshake. Useful after changing the user profile or
  when the scale stops answering. Target a specific device or call with no
  target to reconnect all configured scales.

The other services in the original blueprint (`sync_history`, `identify_user`,
`calibrate`, `clear_history`) are **not** registered because the reference
openScale handler defines no such protocol operations — registering them
would just be theatre.

## Service / protocol reference

- UUIDs: service `0000a602-0000-1000-8000-00805f9b34fb`; characteristics
  `a621` (notifications), `a622` (keep-alive), `a624` (handshake),
  `a625` (notifications).
- Handshake: 6 commands, each wrapped as `0x10 <len> XOR(payload, MAC)`
  where the cipher key is the scale MAC (`mac[i % 6]`): register, set time
  (+ timezone), start measure, user info (sex/age/height/initial weight),
  formula, unit=KG.
- Live packet: `0x10 0x11` + 17 bytes encrypted with the same repeating XOR;
  after decryption: weight `u16be@8 / 100`, scale time `u32be@10`,
  impedance `u16be@14`.

## Testing without a scale

Pure-python unit tests live in `tests/` and exercise the protocol module
without any Home Assistant / Bluetooth hardware:

```bash
python -m pytest tests/ -v
```

Suggested on-device bring-up checklist:
1. Confirm the scale shows as *connectable* in HA Bluetooth settings while
   awake.
2. Add the integration; watch logs for `connected` and any packet parse
   warnings (`logger: homeassistant.components.realme_scale`, debug on).
3. Step on the scale; expect the `weight` sensor (and event) to tick.
4. Compare `body_fat`/`water` with the realme Link / openScale app. If BIA
   figures are off, sanity-check the profile (especially height and
   activity level) — the math mirrors openScale exactly.

## License / attribution

This integration is a derivative port of GPL-3.0 code from
[oliexdev/openScale](https://github.com/oliexdev/openScale)
(`RealmeSmartScaleHandler.kt`, `YunmaiLib.kt`) and is therefore distributed
under the **GNU GPL v3**. See `LICENSE`.
