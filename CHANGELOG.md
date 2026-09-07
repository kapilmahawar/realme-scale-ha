# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- CI: GitHub Actions workflow runs the pure-python test suite on push/PR.

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

[Unreleased]: https://github.com/kapilmahawar/realme-scale-ha/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/kapilmahawar/realme-scale-ha/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/kapilmahawar/realme-scale-ha/releases/tag/v0.1.0
