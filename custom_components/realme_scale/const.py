# Copyright (C) 2026 Kapil Mahawar
#
# This file is part of the realme-scale-ha Home Assistant integration.
# Protocol facts referenced here originate from openScale
# (https://github.com/oliexdev/openScale), GPL-3.0.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""Constants for the Realme Smart Scale (RMH2011) integration.

Protocol reference: openScale ``RealmeSmartScaleHandler.kt`` (GPL-3.0),
https://github.com/oliexdev/openScale
"""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "realme_scale"

MANUFACTURER: Final = "realme"
MODEL: Final = "RMH2011"

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

# Multi-user options keys.  Each user is a dict of the CONF_* profile keys
# below plus a stable CONF_USER_ID.  ``users`` is a list of those dicts.
CONF_USERS: Final = "users"
CONF_USER_ID: Final = "user_id"
CONF_ACTIVE_USER_ID: Final = "active_user_id"
# Auto-assignment weight tolerance in kg; 0 disables auto-assignment.
CONF_AUTO_ASSIGN_KG: Final = "auto_assign_kg"
DEFAULT_AUTO_ASSIGN_KG: Final = 3.0
# Impedance tolerance in Ohm used to tell same-weight users apart;
# 0 disables the impedance gate (weight-only matching).
CONF_IMPEDANCE_TOL_OHM: Final = "impedance_tol_ohm"
DEFAULT_IMPEDANCE_TOL_OHM: Final = 60.0
LEGACY_USER_ID: Final = "default"  # id used when migrating single-profile entries

# User profile keys
CONF_USER_NAME: Final = "user_name"
CONF_PERSON_ENTITY: Final = "person_entity_id"  # optional HA person.* link
CONF_SEX: Final = "sex"            # "male" | "female"
CONF_AGE: Final = "age"            # legacy years (int); kept for migration
# Date of birth, ISO "YYYY-MM-DD". Empty/absent = not configured yet; the
# current age is derived from it (CONF_AGE remains the fallback for legacy
# profiles that predate date-of-birth support).
CONF_DATE_OF_BIRTH: Final = "date_of_birth"
CONF_HEIGHT: Final = "height"      # cm (float)
CONF_ACTIVITY_LEVEL: Final = "activity_level"  # openScale ActivityLevel name
CONF_INITIAL_WEIGHT: Final = "initial_weight"  # kg (float); 0 -> 0xFFFF sentinel

# Identity profile keys (used by the automatic identification engine).
# expected_weight_kg drives the identity *range* (expected +/- tolerance).
# A value of 0 means "not configured" -> falls back to initial weight, then
# to validated history, then the user is not auto-identifiable.
CONF_EXPECTED_WEIGHT: Final = "expected_weight_kg"
# Per-user tolerances; 0 means "use the global default".
CONF_WEIGHT_TOLERANCE: Final = "weight_tolerance_kg"

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

# Measurement record status values
STATUS_ASSIGNED: Final = "assigned"
STATUS_UNKNOWN: Final = "unknown"

# --- Services ---
SERVICE_RECONNECT: Final = "reconnect"
SERVICE_ASSIGN_MEASUREMENT: Final = "assign_measurement"

# Service / event field names
FIELD_MEASUREMENT_ID: Final = "measurement_id"
FIELD_USER: Final = "user"
FIELD_USER_ID: Final = "user_id"
FIELD_STATUS: Final = "status"

# --- Persistent measurement store ---
STORE_VERSION: Final = 1
STORE_MAX_ASSIGNED: Final = 200   # keep this many most-recent assigned records
STORE_MAX_UNKNOWN: Final = 50     # keep this many unassigned records

# --- Sensor helpers ---
ATTR_MEASUREMENT_TIME: Final = "measurement_time"
ATTR_IMPEDANCE: Final = "impedance"
