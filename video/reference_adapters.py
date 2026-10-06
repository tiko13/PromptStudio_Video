"""Read published H3 RefMod v2/v4/v5 and RefLoRA v1 files using native ComfyUI.

Format references: Luisacaotica/ComfyUI-MiniMaxH3Mod BUNDLE_FORMAT.md and
malcolmamal/ComfyUI-MiniMaxH3RefLoRA HYBRID_FORMAT.md. No third-party node import.
"""
import json
import math
from functools import lru_cache
from pathlib import Path

from .adapter_contract import normalize_adapter_stack
from .model_catalog import matches_folder_type

MAX_HEADER_BYTES = 8 * 1024 * 1024
MAX_REFERENCE_BYTES = 128 * 1024 * 1024
DEFAULT_TOKEN_BUDGET = 16384


def register_reference_folders():
    import folder_paths
    for category in ("refmods", "audio_refmods"):
        if category not in folder_paths.folder_names_and_paths:
            folder_paths.add_model_folder_path(category, str(Path(folder_paths.models_dir) / category))
        roots, extensions = folder_paths.folder_names_and_paths[category]
        folder_paths.folder_names_and_paths[category] = (roots, set(extensions) | {".safetensors"})


def resolve_asset(category, name):
    import folder_paths
    register_reference_folders()
    key = name.replace("\\", "/").casefold()
    names = folder_paths.get_filename_list(category)
    matches = [item for item in names if item.replace("\\", "/").casefold() == key]
    if name in matches:
        canonical = name
    elif len(matches) == 1:
        canonical = matches[0]
    else:
        raise ValueError(f"Adapter is missing or ambiguous: {name}")
    return canonical, Path(folder_paths.get_full_path_or_raise(category, canonical))


def inspect_adapter(path):
    """Read bounded headers; validate references before allocating any tensor."""
    path = Path(path)
    stat = path.stat()
    sidecar = path.with_suffix(".json")
    side_stat = sidecar.stat() if sidecar.is_file() else None
    signature = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns,
                 (side_stat.st_size, side_stat.st_mtime_ns, side_stat.st_ctime_ns) if side_stat else None)
    return _inspect_adapter(str(path), signature)


@lru_cache(maxsize=2048)
def _inspect_adapter(filename, signature):
    # Cache only the compact inspection result, never tensors. File and sidecar
    # replacement invalidates it; Refresh need not reread every large header.
    path = Path(filename)
    with path.open("rb") as stream:
        size = int.from_bytes(stream.read(8), "little")
        if not 2 <= size <= MAX_HEADER_BYTES:
            raise ValueError("Invalid or oversized safetensors header")
        header = json.loads(stream.read(size))
    if not isinstance(header, dict):
        raise ValueError("Invalid safetensors header")
    metadata = header.get("__metadata__", {})
    if not isinstance(metadata, dict):
        raise ValueError("Invalid safetensors metadata")
    raw = metadata.get("refmod_meta", metadata.get("audio_refmod_meta"))
    if raw is None:
        # Legacy standalone RefMods used an adjacent JSON metadata file.
        sidecar = path.with_suffix(".json")
        if sidecar.is_file() and sidecar.stat().st_size <= MAX_HEADER_BYTES:
            candidate = json.loads(sidecar.read_text(encoding="utf-8"))
            if isinstance(candidate, dict) and candidate.get("kind") in {"image", "video", "audio"}:
                raw = json.dumps(candidate)
    if raw is None:
        if "h3_hybrid" in metadata or "latent" in header or any(key.startswith("ref_") for key in header):
            raise ValueError("Reference tensors are missing RefMod metadata")
        return {"kind": "lora", "members": [], "weight_keys": [k for k in header if k != "__metadata__"]}
    meta = json.loads(raw)
    if not isinstance(meta, dict):
        raise ValueError("Invalid RefMod metadata")
    bundle = meta.get("kind") == "bundle"
    version = meta.get("_format_version", 2)
    if version not in ({5} if bundle else {2, 4}):
        raise ValueError(f"Unsupported RefMod version: {version}")
    members = meta.get("members") if bundle else [meta]
    if not isinstance(members, list) or not 1 <= len(members) <= 256:
        raise ValueError("RefMod must contain 1–256 references")
    result, total_bytes = [], 0
    for index, member in enumerate(members):
        if not isinstance(member, dict):
            raise ValueError("Invalid reference member")
        key = f"ref_{index}" if bundle else "latent"
        spec = header.get(key, {})
        shape = spec.get("shape", [])
        kind = member.get("kind")
        if not isinstance(shape, list) or any(type(dim) is not int or dim < 1 for dim in shape):
            raise ValueError("Invalid reference shape")
        if kind == "audio":
            valid = len(shape) == 4 and shape[:3] == [1, 32, 2]
            tokens = 2 * shape[-1] if valid else 0
        else:
            valid = kind in {"image", "video"} and len(shape) == 5 and shape[:2] == [1, 24]
            valid = valid and shape[3] % 2 == 0 and shape[4] % 2 == 0 and (kind != "image" or shape[2] == 1)
            tokens = shape[2] * (shape[3] // 2) * (shape[4] // 2) if valid else 0
        dtype_size = {"F16": 2, "BF16": 2, "F32": 4}.get(spec.get("dtype"))
        if not valid or dtype_size is None:
            raise ValueError(f"Invalid {kind} reference tensor {key}")
        nbytes = math.prod(shape) * dtype_size
        offsets = spec.get("data_offsets", [])
        if (len(offsets) != 2 or any(type(n) is not int for n in offsets)
                or offsets[0] < 0 or offsets[1] - offsets[0] != nbytes or offsets[1] > path.stat().st_size - 8 - size):
            raise ValueError("Invalid reference data offsets")
        total_bytes += nbytes
        result.append({"key": key, "kind": kind, "shape": shape, "tokens": tokens, "bytes": nbytes})
    if total_bytes > MAX_REFERENCE_BYTES:
        raise ValueError("Reference file exceeds the 128 MiB tensor budget")
    ref_keys = {m["key"] for m in result}
    weights = [key for key in header if key != "__metadata__" and key not in ref_keys]
    if any(key.startswith("ref_") or key == "latent" for key in weights):
        raise ValueError("Unlisted reference tensors in adapter")
    hybrid = json.loads(metadata["h3_hybrid"]) if "h3_hybrid" in metadata else None
    if hybrid is not None:
        if (not isinstance(hybrid, dict) or hybrid.get("version") != 1
                or hybrid.get("lora", {}).get("keys") != len(weights)
                or hybrid.get("refmod_count") != len(result) or not weights):
            raise ValueError("Damaged or unsupported RefLoRA container")
    return {"kind": "reflora" if weights else "refmod", "members": result, "weight_keys": weights}


def adapter_catalog(adapter_type="*"):
    import folder_paths
    register_reference_folders()
    entries, errors = [], []
    for category in ("loras", "refmods", "audio_refmods"):
        for name in folder_paths.get_filename_list(category):
            if not matches_folder_type(name, adapter_type):
                continue
            if not name.lower().endswith(".safetensors"):
                continue
            try:
                path = Path(folder_paths.get_full_path_or_raise(category, name))
                info = inspect_adapter(path)
                if info["kind"] == "lora" and name.replace("\\", "/").rsplit("/", 1)[-1].startswith("_"):
                    continue
                if category != "loras" and info["kind"] == "lora":
                    raise ValueError("Not a RefMod or RefLoRA file")
                entries.append({"name": name, "category": category, "kind": info["kind"],
                                "modalities": sorted({m["kind"] for m in info["members"]}),
                                "tokens": sum(m["tokens"] for m in info["members"])})
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                errors.append({"name": name, "category": category, "error": str(exc)})
    return {"adapters": entries, "errors": errors, "max_reference_tokens": DEFAULT_TOKEN_BUDGET}


def _reference_block(tensor, member, strength):
    import torch
    import torch.nn.functional as F
    if not torch.isfinite(tensor).all():
        raise ValueError("Reference tensor contains non-finite values")
    kind = member["kind"]
    if strength < 1:
        # Upstream retention semantics: mix toward a low-pass latent, not zero/noise.
        if kind == "audio":
            flat = tensor.float().reshape(64, 1, tensor.shape[-1])
            pooled = F.adaptive_avg_pool1d(flat, max(1, tensor.shape[-1] // 8))
            blurred = F.interpolate(pooled, size=tensor.shape[-1], mode="linear", align_corners=False).reshape_as(tensor)
        else:
            t, h, w = tensor.shape[2:]
            pooled = F.adaptive_avg_pool3d(tensor.float(), (t, max(1, h // 8), max(1, w // 8)))
            blurred = F.interpolate(pooled, size=(t, h, w), mode="trilinear", align_corners=False)
        tensor = (strength * tensor + (1 - strength) * blurred).to(tensor.dtype)
    if kind == "audio":
        return {"kind": kind, "audio_latent": tensor, "ref_audio_t": tensor.shape[-1], "refmod": True}
    block = {"kind": kind, "latent": tensor, "latent_h": tensor.shape[3], "latent_w": tensor.shape[4], "refmod": True}
    if kind == "video":
        block.update(latent_t=tensor.shape[2], ref_audio_t=0, audio_latent=None)
    return block


def apply_reference_adapters(model, positive, mode, stack, max_reference_tokens=DEFAULT_TOKEN_BUDGET):
    from safetensors import safe_open
    rows = normalize_adapter_stack(stack, references=True)
    if not rows:
        return model, positive
    if type(max_reference_tokens) is not int or not 1 <= max_reference_tokens <= 65536:
        raise ValueError("Reference token budget must be 1–65536")
    plans, total, total_bytes = [], 0, 0
    for row in rows:
        _, path = resolve_asset(row["category"], row["name"])
        info = inspect_adapter(path)
        if info["kind"] != row["kind"]:
            raise ValueError(f"Adapter type changed: {row['name']}; refresh the catalog")
        members = []
        for member in info["members"]:
            audio = member["kind"] == "audio"
            strength = row["audio_strength" if audio else "visual_strength"]
            if strength == 0 or row["components"] == ("visual" if audio else "audio"):
                continue
            members.append((member, strength))
            total += member["tokens"]
            total_bytes += member["bytes"]
        plans.append((row, path, info, members))
        wants_refs = (row["components"] != "audio" and row["visual_strength"] > 0) or (row["components"] != "visual" and row["audio_strength"] > 0)
        if wants_refs and not members:
            raise ValueError(f"The selected reference components are not present in {row['name']}")
    if total and mode != "ref2va":
        raise ValueError("Active RefMod references require REF2VA mode and its matching checkpoint")
    if total > max_reference_tokens or total_bytes > MAX_REFERENCE_BYTES:
        raise ValueError(f"Reference adapters exceed the budget ({total} tokens); reduce the selected references")
    blocks = []
    for row, path, info, members in plans:
        with safe_open(str(path), framework="pt", device="cpu") as handle:
            for member, strength in members:
                blocks.append(_reference_block(handle.get_tensor(member["key"]), member, strength))
            if info["kind"] == "reflora" and row["lora_strength"] != 0:
                import comfy.sd
                weights = {key: handle.get_tensor(key) for key in info["weight_keys"]}
                model, _ = comfy.sd.load_lora_for_models(model, None, weights, row["lora_strength"], 0)
    if not blocks:
        return model, positive
    return model, [[entry[0], {**entry[1], "minimax_refs": list(entry[1].get("minimax_refs") or []) + blocks}]
                   for entry in positive]
