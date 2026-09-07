"""Constants for the Realme Smart Scale (RMH2011) integration.

Protocol reference: openScale ``RealmeSmartScaleHandler.kt`` (GPL-3.0),
https://github.com/oliexdev/openScale
"""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "realme_scale"

MANUFACTURER: Final = "realme"
MODEL: Final = "Smart Scale RMH2011"

# --- GATT ---
SVC_A602: Final = "0000a602-0000-1000-8000-00805f9b34fb"
CHR_A621: Final = "0000a621-0000-1000-8000-00805f9b34fb"  # live/history measurement notifications
CHR_A622: Final = "0000a622-0000-1000-8000-00805f9b34fb"  # keep-alive writes
CHR_A624: Final = "0000a624-0000-1000-8000-00805f9b34fb"  # handshake / configuration writes
CHR_A625: Final = "0000a625-0000-1000-8000-00805f9b34fb"  # notifications

KEEP_ALIVE_CMD: Final = bytes((0x00, 0x01, 0xD9))
KEEP_ALIVE_INITIAL_DELAY: Final = 0.5   # seconds, mirrors Kotlin Timer(500ms)
KEEP_ALIVE_INTERVAL: Final = 1.0        # seconds

# Notification stream header/length that marks a parseable measurement packet.
# (data[0] == 0x10 && data[1] == 0x11, payload length 17)
MEASUREMENT_HEADER: Final = 0x10
MEASUREMENT_LENGTH: Final = 0x11
MEASUREMENT_MIN_SIZE: Final = 19

# --- Config entry keys ---
CONF_ADDRESS: Final = "address"
CONF_NAME: Final = "name"
CONF_USER_NAME: Final = "user_name"

# User profile keys
CONF_SEX: Final = "sex"            # "male" | "female"
CONF_AGE: Final = "age"            # years (int)
CONF_HEIGHT: Final = "height"      # cm (float)
CONF_ACTIVITY_LEVEL: Final = "activity_level"  # openScale ActivityLevel name
CONF_INITIAL_WEIGHT: Final = "initial_weight"  # kg (float); 0 -> 0xFFFF sentinel

SEX_MALE: Final = "male"
SEX_FEMALE: Final = "female"

# openScale ActivityLevel: SEDENTARY, MILD, MODERATE, HEAVY, EXTREME
ACTIVITY_LEVELS: Final = (
    "sedentary",
    "mild",
    "moderate",
    "heavy",
    "extreme",
)
# HEAVY/EXTREME map to the "fitness body type" in YunmaiLib
FITNESS_ACTIVITY_LEVELS: Final = {"heavy", "extreme"}

DEFAULT_NAME: Final = "Realme Smart Scale"

# --- Coordinator ---
UPDATE_INTERVAL_SECONDS: Final = 30
CONNECT_TIMEOUT_SECONDS: Final = 20
CONNECT_RETRY_BASE_SECONDS: Final = 10
CONNECT_RETRY_MAX_SECONDS: Final = 120
DISCONNECTED_RETRY_SECONDS: Final = 60

# Measurement sanity bounds (kg), mirrored from the Kotlin handler
MIN_WEIGHT_KG: Final = 0.5
MAX_WEIGHT_KG: Final = 300.0

# --- Events ---
EVENT_MEASUREMENT: Final = "realme_scale_measurement"

# --- Services ---
SERVICE_RECONNECT: Final = "reconnect"

# --- Sensor helpers ---
ATTR_MEASUREMENT_TIME: Final = "measurement_time"
ATTR_IMPEDANCE: Final = "impedance"
