"""Bundled Video Studio workflows and resumable first-run model setup."""

from __future__ import annotations

import base64
import copy
import gzip
import json
import os
import shutil
import threading
import time
import urllib.request
import uuid


DEFAULT_WORKFLOW_PAYLOADS = {
    "[PSV] MiniMax H3.json": "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",
    "[PSV] MiniMax H3 Turbo.json": "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",
}


MODEL_ASSETS = (
    {
        "id": "fl2va",
        "category": "diffusion_models",
        "relative_path": r"MiniMax3\minimax_h3_fl2va_pruned_int8_convrot.safetensors",
        "url": "https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors",
        "size": 20_970_379_616,
    },
    {
        "id": "ref2va",
        "category": "diffusion_models",
        "relative_path": r"MiniMax3\minimax_h3_ref2va_pruned_int8_convrot.safetensors",
        "url": "https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors",
        "size": 20_970_379_616,
    },
    {
        "id": "text_encoder",
        "category": "text_encoders",
        "relative_path": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
        "url": "https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
        "size": 15_687_142_551,
    },
    {
        "id": "video_vae",
        "category": "vae",
        "relative_path": "minimax_h3_video_vae_fp16.safetensors",
        "url": "https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_video_vae_fp16.safetensors",
        "size": 5_207_808_496,
    },
    {
        "id": "audio_vae",
        "category": "vae",
        "relative_path": "minimax_h3_audio_vae_fp32.safetensors",
        "url": "https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_audio_vae_fp32.safetensors",
        "size": 605_254_808,
    },
    {
        "id": "turbo_fl2va_mixed_8step",
        "category": "loras",
        "relative_path": r"MiniMax3\Turbo\minimax_h3_fl2v_lightx2v_turbo_8step_v1.0_resized_avg_rank_24_bf16.safetensors",
        "url": "https://huggingface.co/Kijai/MiniMax-H3_comfy/resolve/main/loras/minimax_h3_fl2v_lightx2v_turbo_8step_v1.0_resized_avg_rank_24_bf16.safetensors",
        "size": 364_638_304,
    },
    {
        "id": "turbo_fl2va_mixed_4step",
        "category": "loras",
        "relative_path": r"MiniMax3\Turbo\minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy_resized_avg_rank_21_bf16.safetensors",
        "url": "https://huggingface.co/Kijai/MiniMax-H3_comfy/resolve/main/loras/minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy_resized_avg_rank_21_bf16.safetensors",
        "size": 314_878_200,
    },
    {
        "id": "turbo_fl2va_768p_4step",
        "category": "loras",
        "relative_path": r"MiniMax3\Turbo\minimax_h3_fl2v_lightx2v_turbo_4step_v1.0_768p_resized_avg_rank_31_bf16.safetensors",
        "url": "https://huggingface.co/Kijai/MiniMax-H3_comfy/resolve/main/loras/minimax_h3_fl2v_lightx2v_turbo_4step_v1.0_768p_resized_avg_rank_31_bf16.safetensors",
        "size": 440_873_704,
    },
    {
        "id": "turbo_ref2va_4step",
        "category": "loras",
        "relative_path": r"MiniMax3\Turbo\minimax_h3_ref2v_lightx2v_turbo_4step_v0.1_resized_avg_rank_20_bf16.safetensors",
        "url": "https://huggingface.co/Kijai/MiniMax-H3_comfy/resolve/main/loras/minimax_h3_ref2v_lightx2v_turbo_4step_v0.1_resized_avg_rank_20_bf16.safetensors",
        "size": 306_731_560,
    },
)


_SETUP_LOCK = threading.RLock()
_SETUP_JOBS = {}
_ACTIVE_JOB_ID = None


def _folder_paths_module():
    import folder_paths

    return folder_paths


def _normalized_relative(path):
    return str(path or "").replace("/", os.sep).replace("\\", os.sep)


def _valid_asset_file(path, expected_size):
    try:
        return os.path.isfile(path) and os.path.getsize(path) == expected_size
    except OSError:
        return False


def _find_existing_asset(asset, folder_paths_module):
    category = asset["category"]
    expected = _normalized_relative(asset["relative_path"])
    basename = os.path.basename(expected).casefold()
    names = list(folder_paths_module.get_filename_list(category))
    names.sort(key=lambda name: (str(name).replace("/", os.sep).casefold() != expected.casefold(), str(name)))
    for name in names:
        relative = _normalized_relative(name)
        if os.path.basename(relative).casefold() != basename:
            continue
        full_path = folder_paths_module.get_full_path(category, name)
        if full_path and _valid_asset_file(full_path, asset["size"]):
            return str(name).replace("/", "\\")
    return None


def _replace_workflow_values(value, replacements):
    if isinstance(value, dict):
        return {key: _replace_workflow_values(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_workflow_values(item, replacements) for item in value]
    if isinstance(value, str):
        return replacements.get(value.replace("/", "\\"), value)
    return value


def _decode_workflow(encoded):
    payload = gzip.decompress(base64.b64decode(encoded))
    return json.loads(payload.decode("utf-8"))


def workflow_setup_plan(folder_paths_module=None):
    folder_paths_module = folder_paths_module or _folder_paths_module()
    replacements = {}
    models = []
    for source in MODEL_ASSETS:
        asset = copy.deepcopy(source)
        resolved = _find_existing_asset(asset, folder_paths_module)
        asset["name"] = os.path.basename(_normalized_relative(asset["relative_path"]))
        asset["installed"] = resolved is not None
        asset["resolved_path"] = resolved or asset["relative_path"]
        if resolved:
            replacements[asset["relative_path"].replace("/", "\\")] = resolved
        models.append(asset)
    workflows = [
        {
            "path": name,
            "name": name[:-5],
            "data": _replace_workflow_values(_decode_workflow(payload), replacements),
        }
        for name, payload in DEFAULT_WORKFLOW_PAYLOADS.items()
    ]
    return {
        "workflows": workflows,
        "models": models,
        "total_bytes": sum(asset["size"] for asset in models),
        "missing_bytes": sum(asset["size"] for asset in models if not asset["installed"]),
    }


def _target_for_asset(asset, folder_paths_module):
    roots = folder_paths_module.get_folder_paths(asset["category"])
    if not roots:
        raise RuntimeError(f"ComfyUI has no configured {asset['category']} model directory")

    def available_bytes(root):
        probe = os.path.abspath(root)
        while not os.path.exists(probe):
            parent = os.path.dirname(probe)
            if parent == probe:
                return -1
            probe = parent
        try:
            return shutil.disk_usage(probe).free
        except OSError:
            return -1

    root = max(roots, key=available_bytes)
    relative = _normalized_relative(asset["relative_path"])
    target = os.path.abspath(os.path.join(root, relative))
    if os.path.commonpath((os.path.abspath(root), target)) != os.path.abspath(root):
        raise RuntimeError("Invalid default model target")
    return target


def _validate_target_capacity(models, targets):
    volumes = {}
    for asset in models:
        target = targets.get(asset["id"])
        if not target:
            continue
        partial = f"{target}.part"
        resumed = os.path.getsize(partial) if os.path.isfile(partial) else 0
        required = max(0, asset["size"] - resumed)
        drive = os.path.splitdrive(os.path.abspath(target))[0].casefold()
        key = drive or os.path.abspath(os.sep)
        volume = volumes.setdefault(key, {"required": 0, "probe": os.path.dirname(target)})
        volume["required"] += required
    for key, volume in volumes.items():
        probe = os.path.abspath(volume["probe"])
        while not os.path.exists(probe):
            parent = os.path.dirname(probe)
            if parent == probe:
                break
            probe = parent
        free = shutil.disk_usage(probe).free
        if free < volume["required"]:
            label = key.upper() if key else probe
            shortage = volume["required"] - free
            raise RuntimeError(
                f"Not enough free space on {label}: {shortage / (1024 ** 3):.2f} GB more is required"
            )


def _download_asset(asset, target, progress):
    expected_size = int(asset["size"])
    if _valid_asset_file(target, expected_size):
        progress(expected_size)
        return
    os.makedirs(os.path.dirname(target), exist_ok=True)
    partial = f"{target}.part"
    offset = os.path.getsize(partial) if os.path.isfile(partial) else 0
    if offset > expected_size:
        os.unlink(partial)
        offset = 0
    progress(offset)
    if offset == expected_size:
        os.replace(partial, target)
        progress(expected_size)
        return
    headers = {"User-Agent": "PromptStudioVideo/1.0"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = urllib.request.Request(asset["url"], headers=headers)
    response = urllib.request.urlopen(request, timeout=60)
    status = getattr(response, "status", None) or response.getcode()
    if offset and status != 206:
        response.close()
        offset = 0
        progress(0)
        request = urllib.request.Request(asset["url"], headers={"User-Agent": "PromptStudioVideo/1.0"})
        response = urllib.request.urlopen(request, timeout=60)
    with response, open(partial, "ab" if offset else "wb") as output:
        completed = offset
        while True:
            chunk = response.read(8 * 1024 * 1024)
            if not chunk:
                break
            output.write(chunk)
            completed += len(chunk)
            progress(completed)
        output.flush()
        os.fsync(output.fileno())
    if os.path.getsize(partial) != expected_size:
        raise RuntimeError(
            f"{asset['name']} downloaded {os.path.getsize(partial)} bytes; expected {expected_size}"
        )
    os.replace(partial, target)
    progress(expected_size)


def _public_job(job):
    return copy.deepcopy({key: value for key, value in job.items() if key != "targets"})


def _set_asset_progress(job_id, asset_id, downloaded):
    with _SETUP_LOCK:
        job = _SETUP_JOBS[job_id]
        for item in job["models"]:
            if item["id"] == asset_id:
                item["downloaded_bytes"] = max(0, min(int(downloaded), item["size"]))
                break
        job["downloaded_bytes"] = sum(item["downloaded_bytes"] for item in job["models"])
        job["updated_at"] = time.time() * 1000


def _refresh_model_cache(folder_paths_module):
    cache_helper = getattr(folder_paths_module, "cache_helper", None)
    if cache_helper is not None and hasattr(cache_helper, "clear"):
        cache_helper.clear()
    cache = getattr(folder_paths_module, "filename_list_cache", None)
    if isinstance(cache, dict):
        for category in {asset["category"] for asset in MODEL_ASSETS}:
            cache.pop(category, None)


def _run_setup(job_id, folder_paths_module):
    global _ACTIVE_JOB_ID
    try:
        with _SETUP_LOCK:
            job = _SETUP_JOBS[job_id]
            job["status"] = "downloading"
            job["updated_at"] = time.time() * 1000
        for asset in job["models"]:
            if asset["installed"]:
                continue
            with _SETUP_LOCK:
                job["current_model"] = asset["name"]
                asset["status"] = "downloading"
            target = job["targets"][asset["id"]]
            _download_asset(
                asset,
                target,
                lambda downloaded, asset_id=asset["id"]: _set_asset_progress(job_id, asset_id, downloaded),
            )
            with _SETUP_LOCK:
                asset["installed"] = True
                asset["status"] = "installed"
        _refresh_model_cache(folder_paths_module)
        with _SETUP_LOCK:
            job["status"] = "complete"
            job["current_model"] = ""
            job["downloaded_bytes"] = job["total_bytes"]
            job["updated_at"] = time.time() * 1000
    except Exception as exc:
        with _SETUP_LOCK:
            job = _SETUP_JOBS[job_id]
            job["status"] = "error"
            job["error"] = str(exc)
            job["updated_at"] = time.time() * 1000
    finally:
        with _SETUP_LOCK:
            if _ACTIVE_JOB_ID == job_id:
                _ACTIVE_JOB_ID = None


def start_default_model_setup(folder_paths_module=None):
    global _ACTIVE_JOB_ID
    folder_paths_module = folder_paths_module or _folder_paths_module()
    with _SETUP_LOCK:
        if _ACTIVE_JOB_ID and _ACTIVE_JOB_ID in _SETUP_JOBS:
            return _public_job(_SETUP_JOBS[_ACTIVE_JOB_ID])
    plan = workflow_setup_plan(folder_paths_module)
    now = time.time() * 1000
    models = []
    targets = {}
    for asset in plan["models"]:
        item = copy.deepcopy(asset)
        item["status"] = "installed" if item["installed"] else "pending"
        item["downloaded_bytes"] = item["size"] if item["installed"] else 0
        if not item["installed"]:
            target = _target_for_asset(item, folder_paths_module)
            targets[item["id"]] = target
            partial = f"{target}.part"
            if os.path.isfile(partial):
                item["downloaded_bytes"] = min(os.path.getsize(partial), item["size"])
        models.append(item)
    _validate_target_capacity(models, targets)
    job_id = uuid.uuid4().hex
    job = {
        "id": job_id,
        "status": "complete" if all(item["installed"] for item in models) else "starting",
        "current_model": "",
        "error": "",
        "models": models,
        "targets": targets,
        "total_bytes": sum(item["size"] for item in models),
        "downloaded_bytes": sum(item["downloaded_bytes"] for item in models),
        "created_at": now,
        "updated_at": now,
    }
    with _SETUP_LOCK:
        _SETUP_JOBS[job_id] = job
        if job["status"] != "complete":
            _ACTIVE_JOB_ID = job_id
            thread = threading.Thread(
                target=_run_setup,
                args=(job_id, folder_paths_module),
                name="PromptStudioVideoDefaultSetup",
                daemon=True,
            )
            thread.start()
    return _public_job(job)


def default_model_setup_status(job_id=None):
    with _SETUP_LOCK:
        resolved = str(job_id or _ACTIVE_JOB_ID or "").strip()
        if not resolved or resolved not in _SETUP_JOBS:
            return {"status": "idle"}
        return _public_job(_SETUP_JOBS[resolved])
