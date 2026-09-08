<p align="center">
  <img src="icon.png" alt="realme logo" width="96" />
</p>

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
4. Fill in the **first user profile** — this is written into the scale
   handshake and drives the local body-composition math:
   - sex, age, height (cm), activity level (openScale's five levels —
     `heavy`/`extreme` select the "fitness" formula branch), and an optional
     initial weight in kg (leave `0` for "new user", which sends the
     `0xFFFF` sentinel like openScale does).
5. More users can be added at any time via the entry's **Options** (see
   *Multiple users* below), which also updates the active profile.

### HACS

The repository ships with `hacs.json`, so it can be added as a custom
repository in HACS (Settings → HACS → ⋮ → Custom repositories →
`https://github.com/kapilmahawar/realme-scale-ha`, category *Integration*)
and installed from there. Manual installation (above) works identically.

## Entities

The integration registers **one HA device per configured user** (sensors
show that user's latest attributed measurement) plus one *scale* device:

| Entity (on each user's device) | Unit | Notes |
|---|---|---|
| `sensor.<name>_weight` | kg | latest attributed weight |
| `sensor.<name>_body_fat` | % | locally computed from impedance |
| `sensor.<name>_muscle` | % | see quirk note below |
| `sensor.<name>_water` | % | locally computed |
| `sensor.<name>_bone_mass` | kg | locally computed |
| `sensor.<name>_lean_body_mass` | kg | locally computed |
| `sensor.<name>_visceral_fat` | – | unitless index |
| `sensor.<name>_impedance` | Ω | diagnostic (disabled by default) |
| `sensor.<name>_last_measured` | timestamp | measurement time from the scale |

The device is named `Realme Smart Scale - <user name>` (entity id e.g.
`sensor.realme_smart_scale_alice_weight`), and every sensor carries the
attributes `user`, `user_id`, `measured_at` — plus `person` /
`person_entity_id` when the profile is linked to an HA person.

Each user's device also exposes **derived metric sensors** (computed on the
fly from the latest reading + profile):

| Entity (on each user's device) | Unit | Notes |
|---|---|---|
| `sensor.<name>_bmi` | – | weight / height² |
| `sensor.<name>_body_fat_mass` | kg | weight × body-fat % |
| `sensor.<name>_bmr` | kcal/day | Schofield (WHO) resting metabolism |
| `sensor.<name>_ideal_weight` | kg | Devine formula |
| `sensor.<name>_protein` | % | Wang constant on lean mass |
| `sensor.<name>_bmi_category` | text | WHO bands |
| `sensor.<name>_body_fat_category` | text | age/sex bands (Gallagher) |
| `sensor.<name>_body_type` | text | heuristic from BMI + fat band |
| `sensor.<name>_body_water_category` | text | Low / Normal / High |
| `sensor.<name>_visceral_fat_category` | text | Healthy / Elevated / High |

Sensors that need impedance (body fat & friends) stay unavailable on
weight-only packets. Sources are listed under *Derived metrics &
interpretations* below.

On the *scale* device:

| Entity | Notes |
|---|---|
| `binary_sensor.<name>_connected` | live GATT link state |
| `sensor.<name>_unassigned_count` | measurements waiting to be assigned |
| `select.<name>_active_user` | switch which profile goes into the handshake |

> **Upgrading from 0.1?** The old version placed sensors directly on the
> scale device, so your entity registry may still contain stale entities
> whose ids contain the MAC (e.g.
> `sensor.realme_smart_scale_d8_0b_cb_14_a4_4f_weight`). The new per-user
> sensors are separate entities — disable/delete the MAC-based ones under
> **Settings → Devices & Services → Entities** (search the MAC) so
> dashboards only show the per-user devices.

An event `realme_scale_measurement` is fired for every parsed packet so
automations can react without polling entities. The event carries
`address`, `measurement_id`, `status` (`assigned` | `unknown`), `weight`,
`measured_at` and — when attributed — `user`/`user_id` plus the locally
derived metrics. When the reading is ambiguous (`unknown`) but a few users
plausibly match, `candidate_users` and `candidate_user_ids` list them
nearest-first. Keep the `measurement_id`: it is what you use to
assign/`reassign` a measurement.

## Multiple users & measurement attribution

The RMH2011 protocol never says *who* is standing on the scale, so the
integration keeps **profiles in Home Assistant** and attributes every packet
using the two signals the scale *does* send — weight and impedance:

1. **Who measures is a (weight + impedance) match.** A measurement is
   automatically given to a user whose last reading is within the *weight
   tolerance* (kg) and, when both readings carry impedance, within the
   *impedance tolerance* (Ω). Impedance lets the integration tell apart two
   people who weigh the same (their body-fat/impedance differs). Single-user
   scales always attribute to that user. Tunables: Options → Auto-assignment
   (0 on an axis disables it).
2. **Not sure = do not guess.** A reading matching *nobody* or matching
   *several* people is kept **unassigned** and published with
   `status: unknown`. When several people plausibly match, the event also
   carries `candidate_users` (nearest first) so a prompt can offer quick
   actions — nothing is silently attributed to the wrong profile.
3. **Confirm from a menu later.** Options → *Assign unassigned
   measurements* lists every waiting measurement (time + weight) with a user
   dropdown. Wrong auto-assignment? Options → *Reassign a recent
   measurement* moves an already-assigned record to the right user; body
   composition is recomputed under the chosen profile either way. Both are
   available to automations via the `realme_scale.assign_measurement`
   service (field `user` accepts the name or the stable id).
4. **Active user = handshake profile.** The active user's profile (sex /
   age / height / initial weight) is written into the scale on every
   connection and is the profile used the moment a brand-new user is
   confirmed for the first time. Change it with the `select` entity or via
   Options; the scale reconnects to apply it.

Options flow actions: *Add user*, *Edit user*, *Delete user* (with a
confirmation step), *Choose active user*, *Auto-assignment settings*,
*Assign unassigned measurements*, *Reassign a recent measurement*, *Save and
close*. Structural changes reload the entry so each user's device
appears/disappears automatically. Deleting a user keeps their stored
measurements (they become unassigned and can be re-attributed later); even
the **last user may be deleted** — the scale stays configured and readings
arrive as unassigned until a user is added. Changing a user's name edits the
existing profile (its stable id never changes).

**Tip:** after adding users, each person's *first* weigh-in has no baseline
yet, so it lands in *Unassigned measurements* — assign it once and from then
on that user is recognised automatically (as long as weights stay separated
or the impedance fingerprint discriminates).

## Adding additional users

1. Open **Settings → Devices & services**.
2. Open the **Realme Smart Scale** integration (the configured entry, e.g.
   `Realme Smart Scale (AA:BB:CC:DD:EE:FF)`).
3. Select **Configure** (the entry's Options — ⋮ / gear).
4. Select **Add User**.
5. Enter the user's profile: name (or pick their HA Person to auto-fill),
   sex, age, height, activity level, optional initial weight.
6. **Repeat** for every additional user (all edits are kept in a working
   copy while the menu is open).
7. Select **Save & Close** — the entry reloads, a device/sensor set appears
   per user, and every user immediately joins automatic assignment.

No reinstall, re-discovery, YAML or restart is required to add users.

### Active User vs. Assigned User

- **Active User** (handshake profile): the *one* profile (sex/age/height/
  activity/initial weight) written into the scale's BLE handshake when it
  connects. Change it via the menu or the *Active user* select entity.
- **Assigned User**: the HA user a measurement belongs to after automatic
  attribution.

They are deliberately independent: with *Active User = Alice*, a weighing
from your carol can still be attributed to her profile (unique weight +
impedance match) — the handshake profile does not own measurements. Only the
active profile is sent to the scale; HA never asks the scale to know about
every configured user.

## Per-user dashboards

Nothing extra is needed for dashboards: each user is a **device** with its
own sensors, and Home Assistant records each sensor's history over time.
Entity ids are derived from the device name:

```text
device:            Realme Smart Scale - Alice          (via "Add user")
entities:          sensor.realme_smart_scale_alice_weight
                   sensor.realme_smart_scale_alice_body_fat
                   sensor.realme_smart_scale_alice_muscle
                   sensor.realme_smart_scale_alice_water
                   sensor.realme_smart_scale_alice_bone_mass
                   sensor.realme_smart_scale_alice_lean_body_mass
                   sensor.realme_smart_scale_alice_visceral_fat
                   sensor.realme_smart_scale_alice_last_measured
```

To build one page per person, create a view per user and filter by their
device:

```yaml
# dashboard YAML - one card per metric, or use the history graph card
type: entities
entities:
  - sensor.realme_smart_scale_alice_weight
  - sensor.realme_smart_scale_alice_body_fat
  - sensor.realme_smart_scale_alice_water
title: Alice - today

type: history-graph
entities:
  - sensor.realme_smart_scale_alice_weight
  - sensor.realme_smart_scale_alice_body_fat
hours_to_show: 24
```

Template helpers if you prefer cards fed by templates:

```jinja
{{ states('sensor.realme_smart_scale_alice_weight') }} kg
```

### Linking to Home Assistant People

When you add/edit a user you get a **dropdown of your existing People**
(Settings → People → `person.*`). Picking one fills in the user name and
links the profile, so a profile never drifts from the person it belongs to.
Each of that user's sensors then carries:

- `user` / `user_id` — the scale profile name and stable id
- `person` / `person_entity_id` — the linked HA person (when set)

Useful for templates and automations:

```jinja
{# every sensor carrying this person's id is theirs #}
{{ state_attr('sensor.realme_smart_scale_alice_weight', 'person') }}

{# list entities for a person via the person attribute #}
{% for e in states.sensor %}
  {% if e.entity_id.startswith('sensor.realme_smart_scale_') and
        state_attr(e.entity_id, 'person_entity_id') == 'person.alice' %}
    {{ e.entity_id }}: {{ e.state }}
  {% endif %}
{% endfor %}
```

### Interactive "who was that?" confirmation (blueprint)

A ready-made blueprint sends a push notification with one quick action per
likely user when a measurement can't be attributed automatically; tapping a
name assigns it via `realme_scale.assign_measurement`:

- File: `blueprints/realme_scale_confirm_measurement.yaml`
- Copy it to `config/blueprints/automation/` (or import the raw file via
  **Settings → Automations → Blueprints → Import**), then create an
  automation from it and set your `notify.*` service.
- Requires the **mobile_app** integration for notification actions. If a
  platform doesn't support actions, the fallback is the message text +
  the unassigned-measurements list in the integration options.

## Derived metrics & interpretations

The derived sensors (BMI, BMR, ideal weight, body-fat mass, protein and the
text categories) use the following standard references:

| Metric | Formula / reference |
|---|---|
| BMI | weight / height² |
| BMI category | WHO classification bands |
| BMR | Schofield equation, FAO/WHO/UNU 1985, age-stratified (kcal/day) |
| Ideal weight | Devine (1974): 50/45.5 + 0.91 × (height cm − 152.4) |
| Body fat mass | weight × body-fat % |
| Protein | Wang et al.: ~19.5 % of fat-free mass |
| Body-fat category | Gallagher et al. (2000) sex/age percentage bands |
| Body-water level | commonly cited hydration ranges (M ~50–65 %, F ~45–60 %) |
| Visceral-fat rating | Healthy ≤ 9, Elevated 10–14, High ≥ 15 |

These are the same class of estimators used by apps like
[bodymiscale](https://github.com/dckiller51/bodymiscale)/openScale; the
weight/impedance core stays the faithful YunmaiLib port.

> ⚠️ **Disclaimer** — This integration is **not a medical device**. All
> derived metrics (BMI, BMR, body fat, water, protein, visceral fat, body
> type) are estimations from generic formulas and bioelectrical impedance
> analysis, provided for personal tracking only. Do not use them to
> diagnose, treat, or prevent any condition, and don't make significant
> health decisions based on them.

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
- **`realme_scale.assign_measurement`** — (re)assign a stored measurement
  to a user: `measurement_id` (from the event) and `user` (name or stable
  id). Works for unassigned *and* already-assigned measurements (the latter
  fixes a wrong auto-assignment). Equivalent to *Options → Assign / Reassign
  measurements*; useful from automations, e.g. a notification with quick
  actions after an unmatched weighing.

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

A GitHub Actions workflow (`.github/workflows/ci.yml`) runs the same suite on
every push/PR against Python 3.11 and 3.12.

Suggested on-device bring-up checklist:
1. Confirm the scale shows as *connectable* in HA Bluetooth settings while
   awake.
2. Add the integration; watch logs for `connected` and any packet parse
   warnings (`logger: homeassistant.components.realme_scale`, debug on).
3. Step on the scale; expect the `weight` sensor (and event) to tick.
4. Compare `body_fat`/`water` with the realme Link / openScale app. If BIA
   figures are off, sanity-check the profile (especially height and
   activity level) — the math mirrors openScale exactly.

## AI assistance & contributors

> 🤖 **AI-use disclosure:** This project is developed with the assistance of
> AI tools — including **DeepSeek** — which contribute to code, unit tests
> and documentation. All AI-generated work is reviewed, tested and accepted
> by the repository owner before it is merged; the owner remains responsible
> for the content of this project.

- **Alice Mahawar** — project owner and maintainer.
- **DeepSeek (AI assistant)** — development, testing and documentation
  support (see disclosure above).

> ℹ️ GitHub's contributor graph reflects commit *authorship* (which runs
> under the owner's account), so the list above is the authoritative credit.

## License / attribution

This integration is a derivative port of GPL-3.0 code from
[oliexdev/openScale](https://github.com/oliexdev/openScale)
(`RealmeSmartScaleHandler.kt`, `YunmaiLib.kt`) and is therefore distributed
under the **GNU GPL v3**. See `LICENSE`.
