"""Pure regression tests for the multi-user options flow.

These validate what can be validated without Home Assistant's runtime:

- the flow exposes the steps/actions the UI needs,
- every menu action / step has a translation entry (a missing translation
  must not break the Options flow),
- the storage invariants of the full user journey (add / edit / remove /
  active-user / zero users) hold through parse/build round-trips.
"""

from __future__ import annotations

import ast
import json
import pathlib

from custom_components.realme_scale.const import (
    DEFAULT_AUTO_ASSIGN_KG,
    DEFAULT_IMPEDANCE_TOL_OHM,
)
from custom_components.realme_scale.scale_controller import (
    ScaleUser,
    build_user_options,
    parse_user_options,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG_FLOW = ROOT / "custom_components" / "realme_scale" / "config_flow.py"
INIT_FILE = ROOT / "custom_components" / "realme_scale" / "__init__.py"
STRINGS = ROOT / "custom_components" / "realme_scale" / "strings.json"
EN = ROOT / "custom_components" / "realme_scale" / "translations" / "en.json"


def _flow_ast() -> ast.Module:
    return ast.parse(CONFIG_FLOW.read_text(encoding="utf-8"))


def _class_methods(tree: ast.Module, name: str) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != name:
            continue
        result[node.name] = {
            stmt.name
            for stmt in node.body
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
    return result


def _module_constants(tree: ast.Module) -> dict[str, str]:
    constants: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if (
            isinstance(target, ast.Name)
            and target.id.startswith("ACTION_")
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            constants[target.id] = node.value.value
    return constants


def test_options_flow_exposes_all_steps() -> None:
    tree = _flow_ast()
    flows = _class_methods(tree, "RealmeScaleOptionsFlow")
    methods = flows.get("RealmeScaleOptionsFlow", set())
    for step in (
        "async_step_init",
        "async_step_menu",
        "async_step_add_user",
        "async_step_edit_user",
        "async_step_edit_user_form",
        "async_step_remove_user",
        "async_step_remove_user_confirm",
        "async_step_active_user",
        "async_step_settings",
        "async_step_assign_pick",
        "async_step_reassign_pick",
        "async_step_save_close",
    ):
        assert step in methods, f"{step} missing from RealmeScaleOptionsFlow"


def test_action_reassign_is_reassign_pick() -> None:
    """BUG 1 regression: ACTION_REASSIGN must resolve to reassign_pick."""
    actions = _module_constants(_flow_ast())
    assert actions["ACTION_REASSIGN"] == "reassign_pick"
    assert actions["ACTION_ASSIGN"] == "assign_pick"


def test_every_menu_option_points_to_implemented_handler() -> None:
    """BUG 1: no menu option may navigate to a missing step."""
    tree = _flow_ast()
    actions = _module_constants(tree)
    methods = _class_methods(tree, "RealmeScaleOptionsFlow").get(
        "RealmeScaleOptionsFlow", set()
    )
    assert actions, "no ACTION_* constants parsed"
    values = list(actions.values())
    assert len(set(values)) == len(values), "duplicate menu step ids"
    for constant, step_id in actions.items():
        assert f"async_step_{step_id}" in methods, (
            f"{constant} -> async_step_{step_id} is missing"
        )
    for language_file in (STRINGS, EN):
        data = json.loads(language_file.read_text(encoding="utf-8"))
        menu_options = data["options"]["step"]["menu"]["menu_options"]
        assert set(actions.values()) <= set(menu_options), (
            f"{language_file.name}: menu label missing for {sorted(actions.values())}"
        )


def test_no_stale_navigation_ids() -> None:
    """assign_unknown / reassign_measurement must not navigate anywhere."""
    for path in (CONFIG_FLOW, STRINGS, EN):
        text = path.read_text(encoding="utf-8")
        assert "reassign_measurement" not in text, path.name
        assert "assign_unknown" not in text, path.name


def test_build_menu_returns_list_of_step_ids() -> None:
    """BUG: menu must be a list of step ids so HA looks up labels."""
    text = CONFIG_FLOW.read_text(encoding="utf-8")
    assert "def _build_menu(self) -> list[str]:" in text
    assert "menu: list[str] = [ACTION_ADD_USER]" in text
    assert "menu[" not in text, "dict-style menu assignment must be gone"
    # Values appended are action constants (== step ids), not label strings.
    assert "menu.append(ACTION_SETTINGS)" in text
    assert "menu.append(ACTION_SAVE)" in text
    assert "menu.append(ACTION_ASSIGN)" in text
    assert "menu.append(ACTION_REASSIGN)" in text


def test_build_menu_conditional_entries_preserved() -> None:
    """assign_pick / reassign_pick stay conditional on work existing."""
    text = CONFIG_FLOW.read_text(encoding="utf-8")
    assert "if users and coordinator.unknown_count:" in text
    assert "if users and coordinator.assigned_records(1):" in text
    # User-only entries are gated on at least one user existing.
    assert "if users:" in text
    assert "ACTION_EDIT_USER" in text
    assert "ACTION_REMOVE_USER" in text


def test_menu_translations_snapshot_unchanged() -> None:
    """strings.json/en.json labels stay as shipped (do not revert)."""
    expected = {
        "add_user": "👤 Add user",
        "edit_user": "✏️ Edit user",
        "remove_user": "🗑️ Remove user",
        "active_user": "🔗 Scale handshake profile",
        "settings": "⚙️ Settings",
        "assign_pick": "📌 Assign measurement",
        "reassign_pick": "🔄 Reassign measurement",
        "save_close": "💾 Save & Close",
    }
    for language_file in (STRINGS, EN):
        data = json.loads(language_file.read_text(encoding="utf-8"))
        menu_options = data["options"]["step"]["menu"]["menu_options"]
        assert menu_options == expected, language_file.name


def _options_updated_node() -> ast.AsyncFunctionDef | None:
    tree = ast.parse(INIT_FILE.read_text(encoding="utf-8"))
    for node in tree.body:
        if getattr(node, "name", None) == "_async_options_updated":
            return node
    return None


def test_options_update_listener_is_async_and_awaits_reload() -> None:
    """BUG (0.6.2): listener must be a coroutine awaiting async_reload."""
    node = _options_updated_node()
    assert node is not None, "_async_options_updated missing from __init__.py"
    assert isinstance(node, ast.AsyncFunctionDef), "listener must be async def"
    decorators = {
        ast.unparse(d).split(".")[-1] for d in node.decorator_list
    }
    assert "callback" not in decorators, "remove @callback from the listener"

    body_text = ast.get_source_segment(
        INIT_FILE.read_text(encoding="utf-8"), node
    ) or ""
    assert "async_reload(entry.entry_id)" in body_text
    assert "await hass.config_entries.async_reload" in body_text
    assert "async_create_task" not in body_text

    # Early returns for no coordinator / no actual change come before reload.
    order = body_text.index("async_reload")
    assert body_text.index("coordinator is None") < order
    assert body_text.index("_options_snapshot") < order


def test_callback_import_still_used_elsewhere() -> None:
    text = INIT_FILE.read_text(encoding="utf-8")
    tree = ast.parse(text)
    used = any(
        ast.unparse(d).split(".")[-1] == "callback"
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        for d in node.decorator_list
    )
    assert used, "@callback is still used (e.g. _register_services_once)"
    assert "from homeassistant.core import HomeAssistant, ServiceCall, callback" in text


def test_setup_entry_not_gated_on_ble_reachability() -> None:
    """BUG 2: async_setup_entry must start without the scale being online."""
    init_path = ROOT / "custom_components" / "realme_scale" / "__init__.py"
    init_text = init_path.read_text(encoding="utf-8")
    assert "ConfigEntryNotReady" not in init_text
    assert "async_resolve_ble_device" not in init_text
    assert "await coordinator.async_start()" in init_text

    # The coordinator keeps its background retry loop (unchanged behavior).
    coordinator_text = (
        ROOT / "custom_components" / "realme_scale" / "coordinator.py"
    ).read_text(encoding="utf-8")
    assert "async_resolve_ble_device" in coordinator_text
    assert "_run_connection_loop" in coordinator_text


def test_options_flow_hook_is_callback_and_sync() -> None:
    """async_get_options_flow must exist, be decorated @callback and sync."""
    tree = _flow_ast()
    found = False
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef,)) or node.name != "async_get_options_flow":
            continue
        if not isinstance(node, ast.AsyncFunctionDef):
            found = True
        decorators = {
            ast.unparse(d).split(".")[-1]
            for d in node.decorator_list
        }
        assert "callback" in decorators, "missing @callback decorator"
    assert found, "async_get_options_flow must be a synchronous def"


def test_every_menu_action_has_translation() -> None:
    tree = _flow_ast()
    actions = _module_constants(tree)
    assert actions.get("ACTION_ADD_USER") == "add_user"
    for language_file in (STRINGS, EN):
        data = json.loads(language_file.read_text(encoding="utf-8"))
        menu_options = data["options"]["step"]["menu"]["menu_options"]
        for value in actions.values():
            assert value in menu_options, (
                f"{language_file.name}: missing menu translation for {value}"
            )


def test_every_options_step_and_error_has_translation() -> None:
    for language_file in (STRINGS, EN):
        data = json.loads(language_file.read_text(encoding="utf-8"))
        steps = data["options"]["step"]
        for step_id in (
            "menu",
            "add_user",
            "edit_user",
            "edit_user_form",
            "remove_user",
            "remove_user_confirm",
            "active_user",
            "settings",
            "assign_pick",
            "reassign_pick",
            "save_close",
        ):
            assert step_id in steps, (
                f"{language_file.name}: missing options step {step_id}"
            )
        errors = data["options"].get("error", {})
        for key in ("name_required", "confirm_required", "user_missing"):
            assert key in errors, f"{language_file.name}: missing error {key}"


def test_config_aborts_have_translations() -> None:
    for language_file in (STRINGS, EN):
        data = json.loads(language_file.read_text(encoding="utf-8"))
        aborts = data["config"]["abort"]
        for reason in ("already_configured", "not_supported", "not_connectable"):
            assert reason in aborts, f"{language_file.name}: missing abort {reason}"


def _user(user_id: str, name: str) -> ScaleUser:
    return ScaleUser(user_id=user_id, name=name, age=30)


def test_regression_journey_add_edit_remove_persist() -> None:
    """The original user journey: one user -> add -> edit -> save -> 2 users."""
    # Initial setup: User A becomes users[0], active = A.
    alice = _user("u-alice", "Alice")
    options = build_user_options([alice], alice.user_id, DEFAULT_AUTO_ASSIGN_KG)
    users, active, *_ = parse_user_options(options)
    assert [u.name for u in users] == ["Alice"]
    assert active == "u-alice"

    # Options flow: add User B (working copy).
    carol = _user("u-carol", "Carol")
    users.append(carol)
    options = build_user_options(users, active, DEFAULT_AUTO_ASSIGN_KG)
    users, active, *_ = parse_user_options(options)
    assert [u.name for u in users] == ["Alice", "Carol"]
    assert active == "u-alice"  # adding a user does not steal active

    # Edit B: same id, new property.
    target = next(u for u in users if u.user_id == "u-carol")
    target.age = 32
    options = build_user_options(users, active, DEFAULT_AUTO_ASSIGN_KG)
    users, *_ = parse_user_options(options)
    edited = next(u for u in users if u.user_id == "u-carol")
    assert edited.user_id == "u-carol"
    assert edited.name == "Carol"
    assert edited.age == 32

    # Change active to Carol; still two users.
    options = build_user_options(users, "u-carol", DEFAULT_AUTO_ASSIGN_KG)
    users, active, *_ = parse_user_options(options)
    assert len(users) == 2
    assert active == "u-carol"

    # Remove Alice: Carol remains and becomes active if she wasn't.
    remaining = [u for u in users if u.user_id != "u-alice"]
    active = "u-carol" if active == "u-alice" else active
    if active not in {u.user_id for u in remaining}:
        active = remaining[0].user_id
    options = build_user_options(remaining, active, DEFAULT_AUTO_ASSIGN_KG)
    users, active, *_ = parse_user_options(options)
    assert [u.user_id for u in users] == ["u-carol"]
    assert active == "u-carol"

    # Remove the final user: zero users, no active, no crash.
    options = build_user_options([], None, DEFAULT_AUTO_ASSIGN_KG)
    users, active, tolerance, impedance = parse_user_options(options)
    assert users == []
    assert active is None
    assert tolerance == DEFAULT_AUTO_ASSIGN_KG
    assert impedance == DEFAULT_IMPEDANCE_TOL_OHM
