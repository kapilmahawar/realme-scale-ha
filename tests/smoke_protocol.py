import sys
import types
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

# Prevent Python from executing custom_components/realme_scale/__init__.py
# (it imports Home Assistant); tests only need the pure protocol modules.
_parent = types.ModuleType("custom_components")
_parent.__path__ = [str(root / "custom_components")]
sys.modules["custom_components"] = _parent
_realme = types.ModuleType("custom_components.realme_scale")
_realme.__path__ = [str(root / "custom_components" / "realme_scale")]
_realme.__package__ = "custom_components.realme_scale"
sys.modules["custom_components.realme_scale"] = _realme

from custom_components.realme_scale.scale_controller import (
    ScaleUser,
    build_handshake,
    is_measurement_packet,
    mac_string_to_bytes,
    obfuscate,
    parse_measurement,
)
from custom_components.realme_scale.bia import YunmaiBia
from datetime import UTC, datetime

MAC = "AA:BB:CC:DD:EE:FF"
MAC_BYTES = bytes((0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF))

assert mac_string_to_bytes(MAC) == MAC_BYTES

payload = bytes(range(40))
assert obfuscate(obfuscate(payload, MAC_BYTES), MAC_BYTES) == payload

user = ScaleUser(name="Alice", sex="male", age=35, height_cm=175.0,
                 activity_level="moderate", initial_weight=75.0)
cmds = build_handshake(user, MAC_BYTES, now=1_800_000_000, tz_offset_min=330)
assert len(cmds) == 6
for cmd in cmds:
    assert cmd[0] == 0x10 and cmd[1] == len(cmd) - 2

def make(weight_raw, scale_time, impedance):
    p = bytearray(17)
    p[8] = (weight_raw >> 8) & 0xFF; p[9] = weight_raw & 0xFF
    p[10] = (scale_time >> 24) & 0xFF; p[11] = (scale_time >> 16) & 0xFF
    p[12] = (scale_time >> 8) & 0xFF; p[13] = scale_time & 0xFF
    p[14] = (impedance >> 8) & 0xFF; p[15] = impedance & 0xFF
    return bytes((0x10, 0x11)) + obfuscate(bytes(p), MAC_BYTES)

packet = make(7500, 1_800_000_000, 520)
assert is_measurement_packet(packet)
m = parse_measurement(packet, MAC_BYTES, user)
assert m is not None and abs(m.weight_kg - 75.0) < 1e-9, m
assert m.impedance == 520
assert m.measured_at == datetime.fromtimestamp(1_800_000_000, tz=UTC)
assert m.body_fat is not None and 5.0 <= m.body_fat <= 45.0, m.body_fat
assert m.water is not None and 0 < m.water < 100
assert m.muscle is not None
assert 0 < m.bone_kg < 20
assert m.lean_body_mass_kg is not None
assert m.visceral_fat is not None

# sanity checks
assert parse_measurement(make(10, 0, 0), MAC_BYTES, user) is None
assert parse_measurement(make(50000, 0, 0), MAC_BYTES, user) is None

calc = YunmaiBia(sex=1, height_cm=175.0, activity_level="moderate")
fat = calc.get_fat(age=35, weight=75.0, resistance=520)
assert 5.0 <= fat <= 45.0, fat

print("smoke OK: handshake", len(cmds), "cmds; weight", m.weight_kg,
      "fat", round(m.body_fat, 2), "muscle", round(m.muscle, 2),
      "water", round(m.water, 2), "bone", round(m.bone_kg, 2),
      "lbm", round(m.lean_body_mass_kg, 2), "visceral", m.visceral_fat)
