"""Versioned API and standalone-page routes for Prompt Studio Video."""

import asyncio
import json
import os
import threading
import time
import uuid

from aiohttp import web
from server import PromptServer

from .video.compiler import compile_prompt
from .video.contracts import (
    ANCHOR_ROLES,
    AUDIO_RETENTION,
    CANVAS_MULTIPLE,
    CAMERA_TYPES,
    DEFAULT_CANVAS_MEGAPIXELS,
    FPS,
    MAX_FRAME_COUNT,
    MAX_CANVAS_MEGAPIXELS,
    MIN_CANVAS_MEGAPIXELS,
    MODES,
    REFERENCE_ROLES,
    TASK_TYPES,
    VISUAL_RETENTION,
    PromptDocumentError,
    default_document,
    effective_duration,
    frame_count_for_duration,
    normalize_document,
)
from .video.media import (
    MAX_REFERENCE_SECONDS,
    MAX_REFERENCE_TOTAL_SECONDS,
    MIN_REFERENCE_SECONDS,
)
from .video.director import director_chat, preview_changeset
from .video.llm_provider import abort_generation, generation_status
from .video.store import (
    StoreConflictError,
    read_project_store,
    read_workflow_store,
    update_project_store,
    update_workflow_store,
)


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_STORE_PATH = os.path.join(BASE_DIR, "promptstudio_video_projects.json")
PROJECT_STORE_DIR = os.path.join(BASE_DIR, "promptstudio_video_projects")
WORKFLOW_STORE_PATH = os.path.join(BASE_DIR, "promptstudio_video_workflows.json")
STANDALONE_PAGE_PATH = os.path.join(BASE_DIR, "web", "prompt_studio_video.html")
STANDALONE_ALIAS_PATH = "/PromptStudioVideo"
MAX_DOCUMENT_REQUEST_BYTES = 2 * 1024 * 1024
MAX_PROJECT_REQUEST_BYTES = 100 * 1024 * 1024
MAX_WORKFLOW_REQUEST_BYTES = 100 * 1024 * 1024
MAX_DIRECTOR_REQUEST_BYTES = 2 * 1024 * 1024
PROJECT_LOCK = asyncio.Lock()
WORKFLOW_LOCK = asyncio.Lock()
DIRECTOR_LLM_LOCK = asyncio.Lock()
DIRECTOR_JOBS = {}
DIRECTOR_TASKS = set()
MAX_DIRECTOR_JOBS = 32
STUDIO_PRESENCE_TIMEOUT_SECONDS = 8.0
STUDIO_HANDOFF_TIMEOUT_SECONDS = 60.0
STUDIO_BRIDGE_LOCK = threading.Lock()
STUDIO_PRESENCES = {}
STUDIO_HANDOFFS = {}


CAPABILITY = {
    "api_version": 1,
    "prompt_document_version": 1,
    "workflow_prefix": "[PSV]",
    "adapters": ["minimax_h3"],
    "node_types": ["PSV_MiniMaxH3Director"],
    "features": [
        "project_director",
        "selected_shot_director",
        "director_vision_attachments",
        "kobold_control",
        "studio_image_handoff",
    ],
    "standalone_page": "/extensions/PromptStudio_Video/prompt_studio_video.html",
}


def _clean_studio_bridge(now=None):
    now = time.monotonic() if now is None else now
    expired_instances = [
        instance_id for instance_id, presence in STUDIO_PRESENCES.items()
        if now - presence["seen_at"] > STUDIO_PRESENCE_TIMEOUT_SECONDS
    ]
    for instance_id in expired_instances:
        STUDIO_PRESENCES.pop(instance_id, None)
    expired_handoffs = [
        request_id for request_id, handoff in STUDIO_HANDOFFS.items()
        if now - handoff["created_at"] > STUDIO_HANDOFF_TIMEOUT_SECONDS
    ]
    for request_id in expired_handoffs:
        STUDIO_HANDOFFS.pop(request_id, None)


def _studio_instances():
    with STUDIO_BRIDGE_LOCK:
        _clean_studio_bridge()
        return [
            {
                "instanceId": instance_id,
                "open": True,
                "openedAt": presence["opened_at"],
                "activeProjectId": presence["active_project_id"],
                "projectName": presence["project_name"],
            }
            for instance_id, presence in STUDIO_PRESENCES.items()
        ]


async def promptstudio_video_capabilities(_request):
    return web.json_response({**CAPABILITY, "studio_instances": _studio_instances()})


def _bridge_text(value, name, maximum=256, required=False):
    text = str(value or "").strip()
    if required and not text:
        raise ValueError(f"{name} is required")
    if len(text) > maximum:
        raise ValueError(f"{name} exceeds {maximum} characters")
    return text


async def promptstudio_video_presence(request):
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        instance_id = _bridge_text(data.get("instanceId"), "instanceId", 128, required=True)
        now = time.monotonic()
        handoffs = []
        with STUDIO_BRIDGE_LOCK:
            _clean_studio_bridge(now)
            if data.get("open"):
                STUDIO_PRESENCES[instance_id] = {
                    "seen_at": now,
                    "opened_at": float(data.get("openedAt") or 0),
                    "active_project_id": _bridge_text(data.get("activeProjectId"), "activeProjectId", 128),
                    "project_name": _bridge_text(data.get("projectName"), "projectName", 256),
                }
                for request_id, handoff in STUDIO_HANDOFFS.items():
                    if handoff["target_instance_id"] != instance_id or handoff["status"] != "pending":
                        continue
                    handoff["status"] = "delivered"
                    handoffs.append({"requestId": request_id, "image": handoff["image"]})
            else:
                STUDIO_PRESENCES.pop(instance_id, None)
        return web.json_response({"ok": True, "handoffs": handoffs})
    except (ValueError, json.JSONDecodeError) as exc:
        return web.json_response({"error": str(exc)}, status=400)


async def promptstudio_video_handoff_create(request):
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        request_id = _bridge_text(data.get("requestId"), "requestId", 128, required=True)
        target_instance_id = _bridge_text(data.get("targetInstanceId"), "targetInstanceId", 128, required=True)
        image = data.get("image")
        if not isinstance(image, dict) or not str(image.get("url") or "").startswith("/"):
            raise ValueError("image must contain a same-server URL")
        now = time.monotonic()
        with STUDIO_BRIDGE_LOCK:
            _clean_studio_bridge(now)
            if target_instance_id not in STUDIO_PRESENCES:
                return web.json_response({"error": "Video Studio is no longer open."}, status=409)
            STUDIO_HANDOFFS[request_id] = {
                "created_at": now,
                "target_instance_id": target_instance_id,
                "image": image,
                "status": "pending",
                "result": None,
                "error": "",
            }
        return web.json_response({"ok": True}, status=202)
    except (ValueError, json.JSONDecodeError) as exc:
        return web.json_response({"error": str(exc)}, status=400)


async def promptstudio_video_handoff_result(request):
    request_id = _bridge_text(request.match_info.get("request_id"), "requestId", 128, required=True)
    with STUDIO_BRIDGE_LOCK:
        _clean_studio_bridge()
        handoff = STUDIO_HANDOFFS.get(request_id)
        if handoff is None:
            return web.json_response({"error": "Image handoff was not found or expired."}, status=404)
        if handoff["status"] != "complete":
            return web.json_response({"status": handoff["status"]}, status=202)
        STUDIO_HANDOFFS.pop(request_id, None)
        return web.json_response({
            "ok": not handoff["error"],
            "result": handoff["result"] or {},
            "error": handoff["error"],
        })


async def promptstudio_video_handoff_complete(request):
    try:
        request_id = _bridge_text(request.match_info.get("request_id"), "requestId", 128, required=True)
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        with STUDIO_BRIDGE_LOCK:
            _clean_studio_bridge()
            handoff = STUDIO_HANDOFFS.get(request_id)
            if handoff is None:
                return web.json_response({"error": "Image handoff was not found or expired."}, status=404)
            handoff["status"] = "complete"
            handoff["result"] = data.get("result") if isinstance(data.get("result"), dict) else {}
            handoff["error"] = _bridge_text(data.get("error"), "error", 2048)
        return web.json_response({"ok": True})
    except (ValueError, json.JSONDecodeError) as exc:
        return web.json_response({"error": str(exc)}, status=400)


async def promptstudio_video_page(_request):
    return web.FileResponse(STANDALONE_PAGE_PATH)


async def promptstudio_video_config(_request):
    return web.json_response({
        "document_version": 1,
        "fps": FPS,
        "maximum_frames": MAX_FRAME_COUNT,
        "canvas": {
            "multiple": CANVAS_MULTIPLE,
            "default_megapixels": DEFAULT_CANVAS_MEGAPIXELS,
            "minimum_megapixels": MIN_CANVAS_MEGAPIXELS,
            "maximum_megapixels": MAX_CANVAS_MEGAPIXELS,
        },
        "standard_duration_seconds": {"minimum": 5, "maximum": 15},
        "experimental_duration_seconds": {"maximum": MAX_FRAME_COUNT / FPS},
        "reference_duration_seconds": {
            "minimum": MIN_REFERENCE_SECONDS,
            "maximum": MAX_REFERENCE_SECONDS,
            "maximum_total": MAX_REFERENCE_TOTAL_SECONDS,
        },
        "reference_limits": {"images": 9, "videos": 3, "audio_tracks": 3, "active_items": 12},
        "modes": sorted(MODES),
        "anchor_roles": sorted(ANCHOR_ROLES),
        "reference_roles": sorted(REFERENCE_ROLES),
        "camera_types": sorted(CAMERA_TYPES),
        "task_types": sorted(TASK_TYPES),
        "visual_retention": sorted(VISUAL_RETENTION),
        "audio_retention": sorted(AUDIO_RETENTION),
        "default_document": default_document(),
        "aspect_presets": [
            {"label": "Landscape 16:9", "width": 1344, "height": 768},
            {"label": "Portrait 9:16", "width": 768, "height": 1344},
            {"label": "Square 1:1", "width": 1024, "height": 1024},
            {"label": "Classic 4:3", "width": 1152, "height": 864},
            {"label": "Portrait 3:4", "width": 864, "height": 1152},
        ],
    })


async def promptstudio_video_runtime_health(_request):
    prompt_worker_alive = any(
        thread.is_alive()
        and (
            "prompt_worker" in thread.name.casefold()
            or getattr(getattr(thread, "_target", None), "__name__", "") == "prompt_worker"
        )
        for thread in threading.enumerate()
    )
    return web.json_response({"prompt_worker_alive": prompt_worker_alive})


async def _document_body(request):
    if request.content_length is not None and request.content_length > MAX_DOCUMENT_REQUEST_BYTES:
        raise ValueError("Video document request exceeds the 2 MB limit")
    data = await request.json()
    if not isinstance(data, dict):
        raise ValueError("JSON body must be an object")
    value = data.get("document", data)
    return normalize_document(value)


def _document_response(document, include_prompt=False):
    response = {
        "valid": True,
        "document": document,
        "resolved_mode": document["resolved_mode"],
        "frame_count": frame_count_for_duration(document["duration_seconds"]),
        "effective_duration": effective_duration(document),
    }
    if include_prompt:
        response["compiled_prompt"] = compile_prompt(document)
    return response


async def promptstudio_video_validate(request):
    try:
        return web.json_response(_document_response(await _document_body(request)))
    except (ValueError, PromptDocumentError, json.JSONDecodeError) as exc:
        return web.json_response({"valid": False, "error": str(exc), "code": "invalid_document"}, status=400)


async def promptstudio_video_compile(request):
    try:
        return web.json_response(_document_response(await _document_body(request), include_prompt=True))
    except (ValueError, PromptDocumentError, json.JSONDecodeError) as exc:
        return web.json_response({"valid": False, "error": str(exc), "code": "invalid_document"}, status=400)


async def promptstudio_video_projects_get(_request):
    try:
        async with PROJECT_LOCK:
            data = await asyncio.to_thread(read_project_store, PROJECT_STORE_PATH, PROJECT_STORE_DIR)
        return web.json_response(data)
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


async def promptstudio_video_projects_put(request):
    try:
        if request.content_length is not None and request.content_length > MAX_PROJECT_REQUEST_BYTES:
            raise ValueError("Video project store exceeds the 100 MB limit")
        data = await request.json()
        async with PROJECT_LOCK:
            saved = await asyncio.to_thread(update_project_store, PROJECT_STORE_PATH, data, PROJECT_STORE_DIR)
        return web.json_response({"ok": True, "revision": saved["revision"]})
    except StoreConflictError as exc:
        return web.json_response({"error": str(exc)}, status=409)
    except (ValueError, PromptDocumentError, json.JSONDecodeError) as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


async def promptstudio_video_workflows_get(_request):
    try:
        async with WORKFLOW_LOCK:
            data = await asyncio.to_thread(read_workflow_store, WORKFLOW_STORE_PATH)
        return web.json_response(data)
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


async def promptstudio_video_workflows_put(request):
    try:
        if request.content_length is not None and request.content_length > MAX_WORKFLOW_REQUEST_BYTES:
            raise ValueError("Video workflow store exceeds the 100 MB limit")
        data = await request.json()
        async with WORKFLOW_LOCK:
            saved = await asyncio.to_thread(update_workflow_store, WORKFLOW_STORE_PATH, data)
        return web.json_response({"ok": True, "revision": saved["revision"]})
    except StoreConflictError as exc:
        return web.json_response({"error": str(exc)}, status=409)
    except (ValueError, json.JSONDecodeError) as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


async def _director_body(request):
    if request.content_length is not None and request.content_length > MAX_DIRECTOR_REQUEST_BYTES:
        raise ValueError("Video Director request exceeds the 2 MB limit")
    data = await request.json()
    if not isinstance(data, dict):
        raise ValueError("JSON body must be an object")
    return data


def _prune_director_jobs():
    if len(DIRECTOR_JOBS) < MAX_DIRECTOR_JOBS:
        return
    finished = sorted(
        (
            (job_id, job)
            for job_id, job in DIRECTOR_JOBS.items()
            if job["status"] in {"complete", "failed"}
        ),
        key=lambda item: item[1].get("finished_at", item[1]["created_at"]),
    )
    for job_id, _job in finished[: max(1, len(DIRECTOR_JOBS) - MAX_DIRECTOR_JOBS + 1)]:
        DIRECTOR_JOBS.pop(job_id, None)


async def _run_director_job(job_id, data):
    job = DIRECTOR_JOBS[job_id]

    def update_progress(progress):
        job["director_progress"] = dict(progress)

    try:
        async with DIRECTOR_LLM_LOCK:
            job["status"] = "running"
            job["started_at"] = time.time()
            job["result"] = await asyncio.to_thread(director_chat, data, update_progress)
        job["status"] = "complete"
    except Exception as exc:
        job["status"] = "failed"
        job["error"] = str(exc) or exc.__class__.__name__
    finally:
        job["finished_at"] = time.time()


def _start_director_job(data):
    _prune_director_jobs()
    job_id = str(uuid.uuid4())
    DIRECTOR_JOBS[job_id] = {
        "status": "queued",
        "created_at": time.time(),
        "provider_settings": {
            "llm_provider": data.get("llm_provider"),
            "kobold_url": data.get("kobold_url"),
        },
    }
    task = asyncio.create_task(_run_director_job(job_id, data))
    DIRECTOR_TASKS.add(task)
    task.add_done_callback(DIRECTOR_TASKS.discard)
    return job_id


async def promptstudio_video_director_chat(request):
    try:
        data = await _director_body(request)
        if data.get("async") is True:
            job_id = _start_director_job(data)
            return web.json_response({"job_id": job_id, "status": "queued"}, status=202)
        async with DIRECTOR_LLM_LOCK:
            result = await asyncio.to_thread(director_chat, data)
        return web.json_response(result)
    except (ValueError, PromptDocumentError, json.JSONDecodeError) as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=502)


async def promptstudio_video_director_status(request):
    job = DIRECTOR_JOBS.get(request.match_info.get("job_id", ""))
    if job is None:
        return web.json_response({"error": "Director job was not found"}, status=404)
    response = {"status": job["status"]}
    if job["status"] == "running":
        if job.get("director_progress"):
            response["director_progress"] = dict(job["director_progress"])
        try:
            response["provider_status"] = await asyncio.to_thread(
                generation_status, job["provider_settings"]
            )
        except Exception:
            provider = str(job["provider_settings"].get("llm_provider") or "koboldcpp").strip().casefold()
            response["provider_status"] = {
                "provider": provider,
                "reachable": False,
                "busy": None,
            }
    elif job["status"] == "complete":
        response["result"] = job.get("result")
    elif job["status"] == "failed":
        response["error"] = job.get("error") or "Director request failed"
    return web.json_response(response)


async def promptstudio_video_director_preview(request):
    try:
        data = await _director_body(request)
        return web.json_response(preview_changeset(data.get("document"), data.get("proposal")))
    except (ValueError, PromptDocumentError, json.JSONDecodeError) as exc:
        status = 409 if "changed after this proposal" in str(exc) else 400
        return web.json_response({"valid": False, "error": str(exc)}, status=status)


async def promptstudio_video_kobold_status(request):
    try:
        data = await _director_body(request)
        data["llm_provider"] = "koboldcpp"
        return web.json_response(await asyncio.to_thread(generation_status, data))
    except (ValueError, json.JSONDecodeError) as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=502)


async def promptstudio_video_kobold_abort(request):
    try:
        data = await _director_body(request)
        return web.json_response(await asyncio.to_thread(abort_generation, data))
    except (ValueError, json.JSONDecodeError) as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=502)


def register_routes():
    """Register after ComfyUI has created its PromptServer singleton."""
    server = getattr(PromptServer, "instance", None)
    if server is None:
        return False
    routes = server.routes
    routes.get("/promptstudio-video/capabilities")(promptstudio_video_capabilities)
    routes.post("/promptstudio-video/studio-presence")(promptstudio_video_presence)
    routes.post("/promptstudio-video/studio-handoff")(promptstudio_video_handoff_create)
    routes.get("/promptstudio-video/studio-handoff/{request_id}")(promptstudio_video_handoff_result)
    routes.post("/promptstudio-video/studio-handoff/{request_id}")(promptstudio_video_handoff_complete)
    routes.get(STANDALONE_ALIAS_PATH)(promptstudio_video_page)
    routes.get("/promptstudio-video/config")(promptstudio_video_config)
    routes.get("/promptstudio-video/runtime-health")(promptstudio_video_runtime_health)
    routes.post("/promptstudio-video/document/validate")(promptstudio_video_validate)
    routes.post("/promptstudio-video/document/compile")(promptstudio_video_compile)
    routes.get("/promptstudio-video/projects")(promptstudio_video_projects_get)
    routes.put("/promptstudio-video/projects")(promptstudio_video_projects_put)
    routes.get("/promptstudio-video/workflows")(promptstudio_video_workflows_get)
    routes.put("/promptstudio-video/workflows")(promptstudio_video_workflows_put)
    routes.post("/promptstudio-video/director/chat")(promptstudio_video_director_chat)
    routes.get("/promptstudio-video/director/chat/{job_id}")(promptstudio_video_director_status)
    routes.post("/promptstudio-video/director/preview")(promptstudio_video_director_preview)
    routes.post("/promptstudio-video/kobold/status")(promptstudio_video_kobold_status)
    routes.post("/promptstudio-video/kobold/abort")(promptstudio_video_kobold_abort)
    return True


register_routes()
