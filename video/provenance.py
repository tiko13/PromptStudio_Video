"""H3 dependency selection for the shared Image provenance service."""
import copy
import json

TURBO_LORA_INPUTS = frozenset({"fl2va_mixed_8step_lora", "fl2va_mixed_4step_lora", "fl2va_768p_4step_lora", "ref2va_4step_lora"})


def effective_asset_snapshot(snapshot, select_turbo_profile):
    """Return an identity-only clone with inactive H3 Turbo assets removed.

    Pass the authoritative nodes.minimax_h3_turbo_profile.select_turbo_profile.
    Never queue this filtered clone: the original remains the replay payload.
    Unresolved graphs conservatively retain all candidate dependencies.
    """
    result = copy.deepcopy(snapshot)
    output = result.get("output", {})
    if not isinstance(output, dict):
        return result
    for node in output.values():
        if not isinstance(node, dict) or node.get("class_type") != "PSV_MiniMaxH3TurboProfile":
            continue
        inputs = node.get("inputs", {})
        if not isinstance(inputs, dict):
            continue
        enabled = inputs.get("enabled", True)
        selected = None
        if enabled not in (False, "false"):
            def resolve(field):
                value = inputs.get(field)
                if not isinstance(value, list):
                    return value
                upstream = output.get(str(value[0]), {}) if value else {}
                if upstream.get("class_type") != "PSV_MiniMaxH3Director":
                    raise ValueError("Unresolved Turbo input")
                document = upstream.get("inputs", {}).get("document_json")
                document = json.loads(document) if isinstance(document, str) else document
                if not isinstance(document, dict):
                    raise ValueError("Unresolved Director document")
                return document.get("resolved_mode" if field == "mode" else field)
            try:
                selected = select_turbo_profile(resolve("mode"), resolve("width"), resolve("height"), inputs.get("preset", "auto_quality")).lora_input
            except (ValueError, TypeError, KeyError, OverflowError):
                continue
        for name in TURBO_LORA_INPUTS:
            if name != selected:
                inputs.pop(name, None)
    return result
