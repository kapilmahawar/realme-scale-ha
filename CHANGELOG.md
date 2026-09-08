# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.0] - 2026-09-08

### Architecture (identity model)
- **Separated concepts**: handshake profile (BLE), configured user (profile),
  detected user (owner of a measurement) are distinct; "Active user" UI is
  renamed *Scale handshake profile* everywhere and documented as not being
  the detected person.
- **Identity baseline is separate from latest measurement.** Baselines are
  rebuilt only from *identity-valid* stored records. With an expected weight
  configured, an out-of-range assignment (e.g. 52 kg handed to a 73 kg
  user) can never poison the identity range - the next ~52 kg reading still
  identifies the correct lighter user.
- Per-user identity fields: **Expected weight (kg)**, **Identification
  tolerance (kg)**, **Impedance tolerance (Ohm)**; 0 falls back to the
  global automatic-identification defaults.
- User devices show only **identity-valid** measurements as their "latest".

### Automatic identification (assignment.py rewrite, pure)
- Identity-range gating (expected +/- tolerance), impedance as secondary
  tie-breaker, no-match/ambiguous stay unassigned, single-user easy mode.
- Returns `method` (`automatic_weight`, `automatic_weight_impedance`,
  `none`) and `confidence`; both persist on records and are included in the
  measurement event payload.

### Fixes
- Canonical flow step name **`assign_pick`** everywhere; the stale
  `assign_unknown` menu key/step reference is gone (menu key, Python action,
  strings and translations now all use `assign_pick`).
- Manual assignment returns a friendly "measurement no longer available"
  message instead of a raw traceback for stale records.

## [0.5.1] - 2026-09-08

### Changed
- **Options Flow follows the current HA API**: `async_get_options_flow` is a
  `@staticmethod @callback` synchronous hook returning a no-argument
  `RealmeScaleOptionsFlow()`; the flow reads the entry from the parent
  `OptionsFlow.config_entry` property.
- **Bluetooth metadata per current guidance**: manifest depends on
  `bluetooth_adapters` (not `bluetooth`) and only advertises connectable
  discovery matchers (service UUID + scale-like names) — no `connectable:
  false` entries, since the RMH2011 needs an active GATT session.
- **Discovery rejects non-connectable sources** (`not_connectable` abort
  with a clear message/translation) instead of half-configuring a scale it
  cannot connect to.
- Menu wording: *Add User / Edit User / Remove User / Active User /
  Assignment Settings / Assign Unknown Measurement / Reassign Measurement /
  Save & Close*, first screen shows "Configured users: {count} / Unknown
  measurements: {pending}".
- README: "Adding additional users" section + Active User vs Assigned User
  explanation.

### Added
- Regression/structure tests for the options flow:
  `tests/test_user_management.py` (step & hook presence, translation
  coverage for every menu action/step/error/abort, and the full add → edit →
  remove → zero-users storage journey).

## [0.5.0] - 2026-09-08

### Changed
- **Multi-user management spec**: the scale stays a single config entry with
  users as profiles inside it (stable UUID ids, shared BLE coordinator,
  per-user devices under the scale). Add/edit/delete users from the entry's
  Options → Users & measurements.
- **Zero-user support**: the last user may now be deleted; the scale remains
  configured and measurements arrive as unassigned until a user is added.
- **Delete confirmation**: deleting a user now asks for explicit confirmation.
- **History preserved on delete**: a deleted user's stored measurements are
  kept and become unassigned records (re-attributable later), instead of
  being dropped.
- Wording aligned with the spec: *Users / Add user / Edit user / Delete
  user* throughout the options flow (no per-user "devices" language).

### Fixed
- `parse_user_options` no longer invents a fallback user when the stored
  `users` list is explicitly empty (previously blocked zero-user mode).

## [0.4.4] - 2026-09-08

### Fixed
- **Opening entry Options returned "500 / Config flow could not be
  loaded"**: `async_get_options_flow` was declared `async def`, but Home
  Assistant invokes it synchronously and expects the flow instance; an
  un-awaited coroutine caused the 500. The hook is now a plain
  (non-async) static method, so the Users & measurements menu opens.

## [0.4.3] - 2026-09-08

### Fixed
- **Entry "Options" menu (Users & measurements) was missing entirely**:
  `RealmeScaleConfigFlow` never registered `async_get_options_flow`, so Home
  Assistant did not expose the entry's Options at all. The hook is now wired
  and `RealmeScaleOptionsFlow` accepts the config entry it is constructed
  with. Add / edit / remove users now works from the entry card.

## [0.4.2] - 2026-09-08

### Fixed
- **"Add Integration → Realme Smart Scale" reported `not_implemented`**:
  a refactor had left `async_step_user` / `async_step_profile` defined
  *outside* the `RealmeScaleConfigFlow` class, so Home Assistant could not
  find the starting step. The steps are proper class methods again.
- The address step now always uses a plain, universally serializable MAC
  text field (discovered scales are shown as a hint instead of a dropdown
  that some Home Assistant versions cannot render).

## [0.4.1] - 2026-09-07

### Changed
- Repository branding: realme logo added as repo-root `icon.png`/`logo.png`
  (HACS) and as component brand assets
  `custom_components/realme_scale/brand/{icon,logo}{,@2x}.png` for
  Home Assistant 2026.3+.
- README: AI-use disclosure and contributor credits (DeepSeek).

### Fixed
- Integration **Help** link now points to this repository's documentation
  (was the upstream openScale repo).
- Options-flow **Back** navigation no longer raises `not_implemented`
  (missing `async_step_menu` handler for the menu step).

## [0.4.0] - 2026-09-07

### Added
- **Derived metric sensors** per user (computed on the fly): BMI, body fat
  mass (kg), BMR (Schofield/WHO), ideal weight (Devine), protein % (Wang).
- **Interpretation text sensors** per user: BMI category (WHO), body-fat
  category (Gallagher age/sex bands), body-water level, visceral-fat
  rating and a simple body-type heuristic.
- `realme_scale_measurement` events for unassigned readings now also carry
  `candidate_user_ids` (parallel to `candidate_users`).
- **Interactive confirm blueprint** (`blueprints/
  realme_scale_confirm_measurement.yaml`): push notification with one quick
  action per likely user; tapping assigns the measurement.
- README: derived-metrics reference table, medical disclaimer and formula
  sources.
- Unit tests for the metrics module (`tests/test_metrics.py`).

## [0.3.0] - 2026-09-07

### Added
- CI: GitHub Actions workflow runs the pure-python test suite on push/PR.
- **HA People integration**: user setup/edit forms offer a dropdown of
  existing `person.*` entities; picking one auto-fills the profile name and
  links the profile (every sensor of that user then exposes `person` /
  `person_entity_id` attributes).
- **Discovery filtering**: only devices advertising the scale service UUID
  (`0000a602`) or a scale-like name token are offered by the integration —
  other realme BLE devices (e.g. "realme Buds Air7") are no longer detected
  as a scale.
- **Multi-user support**: every scale entry now holds a *list* of user
  profiles; users can be added / edited / removed after install from a
  menu-driven options flow.
- **One HA device per user**: each user's latest attributed measurement is
  exposed as its own device with the full sensor set.
- **Active-user select entity** plus options action to pick which profile
  is written into the scale handshake (reconnects to apply).
- **Two-signal auto-attribution** (weight + impedance fingerprints, both
  with configurable tolerances): measurements are matched to the user whose
  last reading is consistent with the new one.
- **Confirm-before-assign semantics**: readings that match nobody - or match
  several users - are *not* silently attributed; they stay unassigned and
  the event reports `candidate_users` (nearest first) for a confirm prompt.
- **Persistent measurement store** (per entry): every packet is recorded
  with a `measurement_id`; the bounded unassigned queue survives restarts.
- **Assign unassigned measurements to a user later** from *Options →
  Assign unassigned measurements* or via the new
  `realme_scale.assign_measurement` service; BIA figures are recomputed
  under the assigned user's profile.
- **Reassign already-assigned records** (Options → Reassign a recent
  measurement, or the same service) to fix wrong auto-assignments; the
  affected users' "latest" sensors are refreshed from the store.
- `realme_scale_measurement` events now carry `measurement_id` and
  `status` (`assigned` | `unknown`), plus `candidate_users` for ambiguous
  readings.
- README: per-user dashboard guide with example YAML and entity ids.
- Unit tests for attribution, record serialization and user-options
  parsing/migration (`tests/test_assignment.py`, `test_records.py`,
  `test_options.py`).

### Fixed
- **Config flow 500 on current Home Assistant**: the manual MAC field no
  longer uses `cv.matches_regex` (newer schema serializers reject it); the
  MAC is validated in code instead.
- **Discovery** no longer matches unrelated realme BLE devices
  (see "Discovery filtering" above).
- Import of the measurement-id constant in `store.py` tidied up.

### Changed
- Old single-profile entries migrate transparently: the flat profile
  becomes a one-user registry with the legacy user id.
- Sensor unique ids are now user-scoped
  (`<unique>_user_<user_id>_<metric>`). After upgrading, previously
  configured sensors appear as separate per-user entities; orphaned
  v0.1 entities may need to be removed from the entity registry.

## [0.1.1] - 2026-02-09

### Added
- `hacs.json` (HACS-ready metadata for the custom component).
- `CHANGELOG.md`.
- GitHub Actions CI (`pytest` for the protocol/BIA unit tests).

### Changed
- GATT write type now mirrors openScale's `GattScaleAdapter`: write-with-response
  is used unless the characteristic only advertises
  `PROPERTY_WRITE_NO_RESPONSE`.

## [0.1.0] - 2026-02-09

### Added
- Initial release.
- Home Assistant custom integration for the realme Smart Scale RMH2011.
- Byte-faithful port of the openScale `RealmeSmartScaleHandler` protocol:
  - service `0000a602`, characteristics `a621` (notify), `a622` (keep-alive),
    `a624` (handshake), `a625` (notify);
  - 6-step handshake to `a624` with MAC-keyed XOR (`MAC[i % 6]`) body cipher;
  - keep-alive `00 01 D9` to `a622` every 1 s;
  - live measurement stream on `a621` (weight `u16BE/100`, scale time
    `u32BE`, impedance `u16BE`).
- Local body-composition engine ported from openScale `YunmaiLib`
  (body fat, water, muscle, bone mass, lean body mass, visceral fat).
- Config flow with BLE discovery and a user profile step
  (sex / age / height / activity level / initial weight), plus options flow.
- Nine sensors and a connectivity binary sensor.
- `realme_scale_measurement` HA event and `realme_scale.reconnect` service.
- Unit tests + hardware-free protocol smoke test (`tests/`).

[Unreleased]: https://github.com/kapilmahawar/realme-scale-ha/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/kapilmahawar/realme-scale-ha/compare/v0.5.1...v0.6.0
[0.5.1]: https://github.com/kapilmahawar/realme-scale-ha/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/kapilmahawar/realme-scale-ha/compare/v0.4.4...v0.5.0
[0.4.4]: https://github.com/kapilmahawar/realme-scale-ha/compare/v0.4.3...v0.4.4
[0.4.3]: https://github.com/kapilmahawar/realme-scale-ha/compare/v0.4.2...v0.4.3
[0.4.2]: https://github.com/kapilmahawar/realme-scale-ha/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/kapilmahawar/realme-scale-ha/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/kapilmahawar/realme-scale-ha/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/kapilmahawar/realme-scale-ha/compare/v0.1.1...v0.3.0
[0.1.1]: https://github.com/kapilmahawar/realme-scale-ha/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/kapilmahawar/realme-scale-ha/releases/tag/v0.1.0
