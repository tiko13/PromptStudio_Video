"""Portable, runtime-independent authoring contract for H3 adapters."""
import json
import math
from pathlib import PurePosixPath, PureWindowsPath

MAX_ADAPTERS = 8


def normalize_adapter_stack(value, *, references=False):
    if isinstance(value, str):
        if len(value) > 65536:
            raise ValueError("Adapter selection is too large")
        value = json.loads(value)
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_ADAPTERS:
        raise ValueError(f"Select at most {MAX_ADAPTERS} adapters per stack")
    result, seen = [], set()
    for entry in value:
        if not isinstance(entry, dict):
            raise ValueError("Each adapter must be an object")
        name = entry.get("name", "")
        if not isinstance(name, str) or not name or len(name) > 1024:
            raise ValueError("Adapter name is missing or too long")
        portable = name.replace("\\", "/")
        if PureWindowsPath(name).drive or PurePosixPath(portable).is_absolute() or ".." in portable.split("/"):
            raise ValueError("Adapter names must be registered relative paths")
        kind = entry.get("kind", "refmod") if references else "lora"
        if kind not in ({"refmod", "reflora"} if references else {"lora"}):
            raise ValueError("Unknown adapter kind")
        category = entry.get("category", "loras" if kind != "refmod" else "refmods")
        if category not in {"loras", "refmods", "audio_refmods"} or (kind == "lora" and category != "loras"):
            raise ValueError("Invalid adapter folder category")
        key = (category, portable.casefold())
        if key in seen:
            raise ValueError(f"Duplicate adapter: {name}")
        seen.add(key)
        row = {"name": name}
        fields = {"strength": (1.0, -100, 100)} if not references else {
            "lora_strength": (1.0, -100, 100), "visual_strength": (1.0, 0, 1), "audio_strength": (1.0, 0, 1)}
        for field, (default, low, high) in fields.items():
            raw = entry.get(field, default)
            if isinstance(raw, bool):
                raise ValueError(f"Invalid {field}")
            try:
                number = float(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid {field}") from exc
            if not math.isfinite(number) or not low <= number <= high:
                raise ValueError(f"{field} must be between {low} and {high}")
            row[field] = number
        if references:
            components = entry.get("components", "all")
            if components not in {"all", "visual", "audio"}:
                raise ValueError("Reference components must be all, visual or audio")
            row.update(kind=kind, category=category, components=components)
        result.append(row)
    return result


def has_reference_adapters(document):
    return any((row.get("components", "all") != "audio" and row.get("visual_strength", 1) > 0)
               or (row.get("components", "all") != "visual" and row.get("audio_strength", 1) > 0)
               for row in document.get("reference_adapters", []))
