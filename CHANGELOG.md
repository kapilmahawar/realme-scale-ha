# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/kapilmahawar/realme-scale-ha/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/kapilmahawar/realme-scale-ha/compare/v0.1.1...v0.3.0
[0.1.1]: https://github.com/kapilmahawar/realme-scale-ha/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/kapilmahawar/realme-scale-ha/releases/tag/v0.1.0
