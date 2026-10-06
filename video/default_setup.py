"""Bundled Video Studio workflows and resumable first-run model setup."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import shutil
import threading
import time
import urllib.request
import uuid
from pathlib import Path


# Load the companion's standalone service without running its ComfyUI __init__.
_ACQUISITION_PATH = Path(__file__).resolve().parents[2] / "ComfyUI_PromptStudio" / "asset_acquisition.py"
_ACQUISITION_SPEC = importlib.util.spec_from_file_location("prompt_studio_asset_acquisition", _ACQUISITION_PATH)
ACQUISITION = importlib.util.module_from_spec(_ACQUISITION_SPEC)
_ACQUISITION_SPEC.loader.exec_module(ACQUISITION)


DEFAULT_WORKFLOW_NAMES = ("[PSV] MiniMax H3.json", "[PSV] MiniMax H3 Turbo.json")
WORKFLOW_SOURCE_DIRECTORY = Path(__file__).resolve().parents[1] / "workflows"
OPTIONAL_MODEL_ASSETS = tuple(json.loads((Path(__file__).parent / "optional_assets.json").read_text(encoding="utf-8")))
WORKFLOW_BUNDLES = {
    "legacy": {"label": "Original Normal and Turbo", "workflows": DEFAULT_WORKFLOW_NAMES},
    "modern": {"label": "H3 Fast, Balanced and Full quality", "workflows": (
        "[PSV] MiniMax H3 Fast v2.json", "[PSV] MiniMax H3 Balanced v2.json", "[PSV] MiniMax H3 Full quality v2.json"),
        "assets": ("fl2va", "ref2va", "text_encoder", "video_vae", "audio_vae", "turbo_v12", "turbo_768_8", "turbo_ref_8")},
    "sparse": {"label": "Experimental H3 sparse attention", "workflows": ("[PSV] MiniMax H3 Sparse experiment.json",),
        "assets": ("fl2va", "ref2va", "text_encoder", "video_vae", "audio_vae")},
    "taomate": {"label": "Experimental TaoMate T2VA", "workflows": ("[PSV] MiniMax H3 TaoMate experiment.json",),
        "assets": ("fl2va", "text_encoder", "video_vae", "audio_vae", "taomate")},
    "fasth3": {"label": "Experimental FastH3 V2", "workflows": ("[PSV] FastH3 V2 experiment.json",),
        "assets": ("fasth3", "text_encoder", "video_vae", "audio_vae")},
}


# Sizes and LFS SHA-256 values verified from each repository's Hugging Face
# /api/models/{repository}?blobs=true metadata on 2026-09-05. Resolve URLs pin
# that same response's immutable commit, including the four resized Turbo LoRAs.
MODEL_ASSETS = (
    {
        "id": "fl2va",
        "category": "diffusion_models",
        "relative_path": r"MiniMax3\minimax_h3_fl2va_pruned_int8_convrot.safetensors",
        "url": "https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/4cc1d817b6184899b41293954329f576cb5ae86b/diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors",
        "size": 20_970_379_616,
        "sha256": "e889202c41dafb67b10d67b97f0d8541508036a6090af23425a5c2615d03c47a",
    },
    {
        "id": "ref2va",
        "category": "diffusion_models",
        "relative_path": r"MiniMax3\minimax_h3_ref2va_pruned_int8_convrot.safetensors",
        "url": "https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/4cc1d817b6184899b41293954329f576cb5ae86b/diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors",
        "size": 20_970_379_616,
        "sha256": "9255f52b6677845ad238f20dfaafa94727053694127ab7f255c048f0f9365779",
    },
    {
        "id": "text_encoder",
        "category": "text_encoders",
        "relative_path": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
        "url": "https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/4cc1d817b6184899b41293954329f576cb5ae86b/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
        "size": 15_687_142_551,
        "sha256": "35a88d51044231fe332301d7a62aa81e3f2cba62febeb446e2c1e3e0ef76f2c6",
    },
    {
        "id": "video_vae",
        "category": "vae",
        "relative_path": "minimax_h3_video_vae_fp16.safetensors",
        "url": "https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/4cc1d817b6184899b41293954329f576cb5ae86b/vae/minimax_h3_video_vae_fp16.safetensors",
        "size": 5_207_808_496,
        "sha256": "7c1f131492e7eddacaac9069a61b81bdd39de5cc96561e677c5eab1cdce5e522",
    },
    {
        "id": "audio_vae",
        "category": "vae",
        "relative_path": "minimax_h3_audio_vae_fp32.safetensors",
        "url": "https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/4cc1d817b6184899b41293954329f576cb5ae86b/vae/minimax_h3_audio_vae_fp32.safetensors",
        "size": 605_254_808,
        "sha256": "8e505d95dd1561d47abd43d4238fd40d9bb1ae9e147ed0a4cba778d76ae4db48",
    },
    {
        "id": "turbo_fl2va_mixed_8step",
        "category": "loras",
        "relative_path": r"MiniMax3\Turbo\minimax_h3_fl2v_lightx2v_turbo_8step_v1.0_resized_avg_rank_24_bf16.safetensors",
        "url": "https://huggingface.co/Kijai/MiniMax-H3_comfy/resolve/2dc3cedb9b58b0e448d9e950f794f25bf28dbbb5/loras/minimax_h3_fl2v_lightx2v_turbo_8step_v1.0_resized_avg_rank_24_bf16.safetensors",
        "size": 364_638_304,
        "sha256": "8e05b7b982c3aff7deb692a188c8a8d8acaeff8a12abfe1aeac822fb8ee3f0b7",
    },
    {
        "id": "turbo_fl2va_mixed_4step",
        "category": "loras",
        "relative_path": r"MiniMax3\Turbo\minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy_resized_avg_rank_21_bf16.safetensors",
        "url": "https://huggingface.co/Kijai/MiniMax-H3_comfy/resolve/2dc3cedb9b58b0e448d9e950f794f25bf28dbbb5/loras/minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy_resized_avg_rank_21_bf16.safetensors",
        "size": 314_878_200,
        "sha256": "3a069f26fbc33f377a60dc72dd9e15f2aa42aa1d1b44915fded835716672dd36",
    },
    {
        "id": "turbo_fl2va_768p_4step",
        "category": "loras",
        "relative_path": r"MiniMax3\Turbo\minimax_h3_fl2v_lightx2v_turbo_4step_v1.0_768p_resized_avg_rank_31_bf16.safetensors",
        "url": "https://huggingface.co/Kijai/MiniMax-H3_comfy/resolve/2dc3cedb9b58b0e448d9e950f794f25bf28dbbb5/loras/minimax_h3_fl2v_lightx2v_turbo_4step_v1.0_768p_resized_avg_rank_31_bf16.safetensors",
        "size": 440_873_704,
        "sha256": "9515eee9f642aa0e7fcc401f56d408ef2d6388f81881fe50bddded8220870a4d",
    },
    {
        "id": "turbo_ref2va_4step",
        "category": "loras",
        "relative_path": r"MiniMax3\Turbo\minimax_h3_ref2v_lightx2v_turbo_4step_v0.1_resized_avg_rank_20_bf16.safetensors",
        "url": "https://huggingface.co/Kijai/MiniMax-H3_comfy/resolve/2dc3cedb9b58b0e448d9e950f794f25bf28dbbb5/loras/minimax_h3_ref2v_lightx2v_turbo_4step_v0.1_resized_avg_rank_20_bf16.safetensors",
        "size": 306_731_560,
        "sha256": "9ea3bd3a6aac22994153e294cf1ecab0a8766fc0f8d056ace645a01d1a6a4daf",
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


def _valid_asset_file(path, expected_size, expected_sha256=""):
    return ACQUISITION.verified_file(path, expected_size, expected_sha256)


def _find_existing_asset(asset, folder_paths_module):
    category = asset["category"]
    expected = _normalized_relative(asset["relative_path"])
    basename = os.path.basename(expected).casefold()
    names = list(folder_paths_module.get_filename_list(category))
    names.sort(key=lambda name: (_normalized_relative(name).casefold() != expected.casefold(), str(name)))
    for name in names:
        relative = _normalized_relative(name)
        if os.path.basename(relative).casefold() != basename:
            continue
        full_path = folder_paths_module.get_full_path(category, name)
        if full_path and _valid_asset_file(full_path, asset["size"], asset["sha256"]):
            return str(name)
    return None


def _replace_workflow_values(value, replacements):
    if isinstance(value, dict):
        return {key: _replace_workflow_values(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_workflow_values(item, replacements) for item in value]
    if isinstance(value, str):
        return replacements.get(_normalized_relative(value), value)
    return value


def serialize_workflow_source(workflow):
    """Canonical readable package representation; no timestamps or compression."""
    return json.dumps(workflow, ensure_ascii=False, indent=2) + "\n"


def load_bundled_workflow(name):
    if name not in {name for bundle in WORKFLOW_BUNDLES.values() for name in bundle["workflows"]}:
        raise ValueError("Unknown bundled Video workflow")
    workflow = json.loads((WORKFLOW_SOURCE_DIRECTORY / name).read_text(encoding="utf-8"))
    if not isinstance(workflow, dict) or not isinstance(workflow.get("nodes"), list):
        raise ValueError("Bundled Video workflow must contain native workflow nodes")
    return workflow


def workflow_setup_plan(folder_paths_module=None, bundle="legacy"):
    if bundle not in WORKFLOW_BUNDLES:
        raise ValueError("Unknown Video workflow bundle")
    selection = WORKFLOW_BUNDLES[bundle]
    folder_paths_module = folder_paths_module or _folder_paths_module()
    replacements = {}
    models = []
    sources = MODEL_ASSETS if bundle == "legacy" else tuple(
        asset for asset in (*MODEL_ASSETS, *OPTIONAL_MODEL_ASSETS) if asset["id"] in selection["assets"])
    for source in sources:
        asset = copy.deepcopy(source)
        resolved = _find_existing_asset(asset, folder_paths_module)
        asset["name"] = os.path.basename(_normalized_relative(asset["relative_path"]))
        asset["installed"] = resolved is not None
        asset["resolved_path"] = resolved or _normalized_relative(asset["relative_path"])
        replacements[_normalized_relative(asset["relative_path"])] = asset["resolved_path"]
        models.append(asset)
    workflows = [
        {
            "path": name,
            "name": name[:-5],
            "data": _replace_workflow_values(load_bundled_workflow(name), replacements),
        }
        for name in selection["workflows"]
    ]
    return {
        "bundle": bundle,
        "label": selection["label"],
        "bundles": [{"id": key, "label": value["label"]} for key, value in WORKFLOW_BUNDLES.items()],
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


def _download_asset(asset, target, progress, stage_progress=None):
    def report(done, total, stage):
        if stage_progress:
            stage_progress(stage)
        # Hash progress is distinct from bytes already transferred.
        progress(total if stage in {"Verifying", "Verified"} else done)

    ACQUISITION.acquire_asset(asset, target, report, opener=urllib.request.urlopen)


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


def _set_asset_stage(job_id, asset_id, stage):
    with _SETUP_LOCK:
        job = _SETUP_JOBS[job_id]
        job["stage"] = stage.casefold()
        for item in job["models"]:
            if item["id"] == asset_id:
                item["status"] = stage.casefold()
                break
        job["updated_at"] = time.time() * 1000


def _refresh_model_cache(folder_paths_module):
    cache_helper = getattr(folder_paths_module, "cache_helper", None)
    if cache_helper is not None and hasattr(cache_helper, "clear"):
        cache_helper.clear()
    cache = getattr(folder_paths_module, "filename_list_cache", None)
    if isinstance(cache, dict):
        for category in {asset["category"] for asset in (*MODEL_ASSETS, *OPTIONAL_MODEL_ASSETS)}:
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
                lambda stage, asset_id=asset["id"]: _set_asset_stage(job_id, asset_id, stage),
            )
            with _SETUP_LOCK:
                asset["installed"] = True
                asset["status"] = "installed"
        _refresh_model_cache(folder_paths_module)
        with _SETUP_LOCK:
            job["status"] = "complete"
            job["stage"] = "verified"
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


def start_default_model_setup(folder_paths_module=None, bundle="legacy"):
    global _ACTIVE_JOB_ID
    folder_paths_module = folder_paths_module or _folder_paths_module()
    with _SETUP_LOCK:
        if _ACTIVE_JOB_ID and _ACTIVE_JOB_ID in _SETUP_JOBS:
            if _SETUP_JOBS[_ACTIVE_JOB_ID].get("bundle", "legacy") != bundle:
                raise ValueError("Another workflow bundle is installing; wait for it to finish")
            return _public_job(_SETUP_JOBS[_ACTIVE_JOB_ID])
    plan = workflow_setup_plan(folder_paths_module, bundle=bundle)
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
        "bundle": bundle,
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
