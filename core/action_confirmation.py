"""Deterministic confirmation policy for consequential computer-use actions.

This supplements, rather than replaces, PermissionEngine. Callers must obtain
human approval outside the model tool channel and bind it to action_fingerprint.
``effect`` and ``target`` are optional *trusted executor* observations; never
forward model-supplied policy claims here. A control's label is useful evidence,
not a guarantee about what arbitrary application code will do on activation.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ConfirmationRequirement:
    category: str
    summary: str
    reason: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def action_fingerprint(
    tool_name: str, arguments: Mapping[str, Any], *, mission_id: str,
    state_token: str = "",
) -> str:
    """Bind one approval to exact input, mission and optional observed state.

    This is an identity, not an authorization token. The caller must separately
    track the human decision, expiration, cancellation and one-time consumption.
    Unserializable/non-finite input fails closed instead of hashing a repr.
    """
    if not mission_id:
        raise ValueError("Confirmation requires an active mission identity")
    encoded = json.dumps(
        {"version": 1, "mission": mission_id, "state": state_token,
         "tool": tool_name, "arguments": dict(arguments)},
        sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _require(category: str, summary: str, reason: str) -> ConfirmationRequirement:
    return ConfirmationRequirement(category, summary, reason)


_DIRECT_ACTIONS = {
    "discord_send_message": "send", "discord_navigate_and_send": "send",
    "send_message": "send", "send_email": "send", "post_message": "send",
    "file_delete": "delete", "delete_file": "delete", "directory_delete": "delete",
    "delete_directory": "delete", "empty_recycle_bin": "delete",
    "purchase": "purchase", "place_order": "purchase", "checkout": "purchase",
    "change_password": "critical_settings", "set_firewall": "critical_settings",
    "set_registry_value": "critical_settings", "registry_write": "critical_settings",
    "system_shutdown": "critical_settings", "system_restart": "critical_settings",
    "git_push": "publish",
}
_EFFECTS = {
    "send": ("Send or publish this content", "Content will be delivered outside the local draft."),
    "delete": ("Delete the selected content", "Deletion may remove data that cannot be recovered."),
    "purchase": ("Complete this purchase or payment", "This action can create a financial commitment."),
    "critical_settings": ("Change critical system or account settings", "The change can affect security, access or device operation."),
    "publish": ("Publish these changes", "The action changes shared or externally visible state."),
    "overwrite": ("Replace existing content", "The existing version may be lost."),
}
_SAFE_EFFECTS = {"navigate", "focus", "select", "draft", "search", "scroll", "open", "dismiss"}
_ACTIVATION_TOOLS = {
    "interaction_click", "ui_activate", "browser_click", "browser_semantic_click",
    "dialog_click_button", "wincom_click", "wincom_invoke", "desktop_click",
    "desktop_click_button", "desktop_double_click", "desktop_mouse_down",
    "desktop_drag",
}
_COORDINATE_TOOLS = {
    "desktop_click", "desktop_click_button", "desktop_double_click",
    "desktop_mouse_down", "desktop_drag",
}
_KEY_TOOLS = {
    "desktop_press", "desktop_hotkey", "desktop_key_down", "ui_hotkey",
    "interaction_hotkey", "browser_press", "browser_semantic_press", "chrome_press",
}
_TYPE_TOOLS = {
    "interaction_type", "ui_type", "ui_type_native", "desktop_type", "browser_type",
    "browser_semantic_type", "browser_semantic_fill", "wincom_type",
    "open_application_and_type", "dialog_set_field",
}
_BATCH_OPS = {"activate": "ui_activate", "focus": "ui_focus", "type": "ui_type",
              "hotkey": "ui_hotkey", "wait": "wait", "assert_visible": "ui_wait_state"}


def _normal(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split()).strip(" .…:!")


def _semantic_labels(arguments: Mapping[str, Any], target: Mapping[str, Any] | None) -> list[str]:
    labels: list[str] = []
    # Only structured control identity fields; never draft text, queries, titles,
    # checkpoint evidence, descriptions or page body text.
    for value in (target, arguments.get("target"), arguments.get("browser_target"), arguments.get("selector")):
        if isinstance(value, Mapping):
            labels.extend(str(value[key]) for key in ("name", "label", "accessible_name")
                          if isinstance(value.get(key), str) and value[key].strip())
        elif isinstance(value, str) and value.strip():
            if value == arguments.get("selector"):
                # Recognize explicit labels in common Playwright/CSS selectors.
                # Arbitrary CSS ids, node ids and positional selectors are opaque.
                matches = re.findall(r'''(?:aria-label\s*=\s*|:has-text\(\s*|:text\(\s*)["']([^"']+)["']''', value)
                labels.extend(matches)
                if value.startswith("text="):
                    labels.append(value[5:].strip("\"'"))
            else:
                labels.append(value)
    return [_normal(label) for label in labels]


def _label_effect(label: str) -> str | None:
    patterns = (
        ("send", r"(?:send|publish|post|reply|forward|ارسال|إرسال|نشر|أرسل)(?:\s|$)"),
        ("delete", r"(?:delete|remove|erase|destroy|empty recycle bin|permanently delete|حذف|احذف|إزالة)(?:\s|$)"),
        ("purchase", r"(?:buy|purchase|pay|place order|confirm order|complete order|checkout|subscribe|شراء|دفع)(?:\s|$)"),
        ("overwrite", r"(?:replace existing|overwrite|discard changes|don't save|do not save)(?:\s|$)"),
        ("critical_settings", r"(?:change password|reset password|reset pc|factory reset|format drive|disable firewall|disable antivirus|delete account|grant administrator|shutdown|shut down|restart now)(?:\s|$)"),
    )
    for category, pattern in patterns:
        if re.match(pattern, label):
            return category
    return None


def _effect_requirement(effect: str) -> ConfirmationRequirement:
    summary, reason = _EFFECTS[effect]
    return _require(effect, summary, reason)


def _keys(arguments: Mapping[str, Any]) -> set[str]:
    raw = arguments.get("keys", arguments.get("key", []))
    values = [raw] if isinstance(raw, str) else raw if isinstance(raw, (list, tuple)) else []
    aliases = {"control": "ctrl", "return": "enter", "del": "delete", "spacebar": "space"}
    return {aliases.get(item.strip().casefold(), item.strip().casefold())
            for value in values if isinstance(value, str) for item in value.split("+")}


def classify_action(
    tool_name: str, arguments: Mapping[str, Any], *, effect: str | None = None,
    target: Mapping[str, Any] | None = None,
) -> ConfirmationRequirement | None:
    """Classify one dispatch without I/O, model calls or content-based intent guessing.

    Composite workflows must authorize each child at dispatch. ui_batch has no
    child ToolRegistry dispatch, so its known operations are checked as a unit.
    Full Access and model-provided ``approved``/``effect`` fields never bypass it.
    """
    if effect is not None and effect not in _EFFECTS and effect not in _SAFE_EFFECTS:
        raise ValueError(f"Unknown trusted action effect: {effect}")
    direct = _DIRECT_ACTIONS.get(tool_name)
    if direct:
        return _effect_requirement(direct)
    if tool_name == "run_powershell":
        return _require("execution", "Run this system command",
                        "Arbitrary commands can delete data or change critical settings.")
    if tool_name in {"write_file", "notepad_save_as", "dialog_save_file"}:
        return _require("overwrite", "Write this file content", "Writing can replace an existing file. Review the destination and content.")
    if effect in _EFFECTS:
        return _effect_requirement(effect)
    if tool_name == "browser_semantic_action":
        action = arguments.get("action")
        if action == "press":
            return classify_action("browser_press", {**arguments, "key": arguments.get("value", "")},
                                   effect=effect, target=target)
        if action in {"click", "check", "uncheck", "select"}:
            return classify_action("browser_semantic_click", arguments, effect=effect, target=target)
        return None
    if tool_name == "ui_batch":
        for action in arguments.get("actions", []):
            if isinstance(action, Mapping):
                requirement = classify_action(_BATCH_OPS.get(str(action.get("op")), ""), action)
                if requirement:
                    return requirement
        return None
    if tool_name in _TYPE_TOOLS and arguments.get("submit") is True:
        return _require("submit", "Submit the entered content",
                        "Submitting can send a message or commit a form outside the draft.")
    if arguments.get("overwrite") is True and tool_name in {"write_file", "file_copy", "notepad_save_as"}:
        return _effect_requirement("overwrite")
    if tool_name in _ACTIVATION_TOOLS:
        labels = _semantic_labels(arguments, target)
        for label in labels:
            category = _label_effect(label)
            if category:
                return _effect_requirement(category)
        if effect in _SAFE_EFFECTS:
            return None
        # A label supplied beside a node ID is not evidence of that node's effect.
        # Only an executor-resolved target can disambiguate an opaque identity.
        selectors = (arguments, arguments.get("target"), arguments.get("browser_target"))
        opaque = target is None and any(isinstance(value, Mapping) and value.get("node_id") for value in selectors)
        if any(label in {"ok", "yes", "confirm", "apply", "submit", "continue", "allow", "grant", "نعم", "تأكيد", "موافق"}
               for label in labels):
            return _require("uncertain_commit", "Confirm this interface action",
                            "The control can accept a consequential operation; its effect is not established.")
        if not labels or opaque or tool_name in _COORDINATE_TOOLS:
            return _require("unresolved_activation", "Activate this screen target",
                            "Coordinates or opaque selectors do not establish whether the action is reversible.")
    if tool_name in _KEY_TOOLS:
        keys = _keys(arguments)
        if keys & {"delete"}:
            if target and _normal(str(target.get("role", target.get("type", "")))) in {"textbox", "edit", "textarea", "document"}:
                return None
            return _effect_requirement("delete")
        if "enter" in keys or keys == {"space"}:
            if effect in _SAFE_EFFECTS:
                return None
            return _require("uncertain_commit", "Press an activation key",
                            "Enter or Space can submit content or confirm the focused action.")
    return None
