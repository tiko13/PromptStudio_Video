"""Durable project and workflow stores for the standalone Video Studio."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import re
import shutil
import tempfile
import time
import sys
from pathlib import Path

from .contracts import PromptDocumentError, normalize_document


def shared_transactional_store():
    """Consume the primary pure persistence service without ComfyUI startup."""
    name = "_promptstudio_shared_transactional_store"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[2] / "ComfyUI_PromptStudio" / "transactional_store.py"
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError("Prompt Studio shared persistence service is unavailable")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules[name] = module
    return sys.modules[name]


# Import initialization is serialized by Python; eager resolution keeps nested
# cross-thread store calls on the same shared module and reentrant lock registry.
_TRANSACTIONAL_STORE = shared_transactional_store()


STORE_VERSION = 1
PROJECT_STORE_VERSION = 2
MAX_PROJECTS = 500
MAX_PROJECT_NAME_CHARS = 200
MAX_PROJECT_BRIEF_CHARS = 32 * 1024
MAX_PROJECT_STORE_BYTES = 100 * 1024 * 1024
MAX_WORKFLOW_STORE_BYTES = 100 * 1024 * 1024


class StoreConflictError(RuntimeError):
    """Raised when another browser saved a newer revision."""


def _revision(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _text(value, maximum, field):
    result = str(value or "").strip()
    if len(result) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters")
    return result


def _identifier(value, field):
    result = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", result):
        raise ValueError(f"{field} has an invalid identifier")
    return result


def _timestamp(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = time.time() * 1000
    return max(0.0, result)


def _atomic_write(path, data, maximum_bytes, *, backup_path=None, skip_unchanged=False):
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > maximum_bytes:
        raise ValueError(f"Store exceeds the {maximum_bytes // (1024 * 1024)} MB limit")
    if skip_unchanged:
        try:
            with open(path, "rb") as file:
                if file.read() == encoded:
                    return data
        except FileNotFoundError:
            pass
    descriptor, temporary = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(encoded)
            file.flush()
            os.fsync(file.fileno())
        if os.path.exists(path):
            resolved_backup_path = backup_path or f"{path}.bak"
            backup_directory = os.path.dirname(resolved_backup_path)
            if backup_directory:
                os.makedirs(backup_directory, exist_ok=True)
            shutil.copy2(path, resolved_backup_path)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return data


def _read_json(path, empty, label):
    try:
        with open(path, "r", encoding="utf-8") as file:
            value = json.load(file)
    except FileNotFoundError:
        return empty()
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"Invalid {label}: root must be an object")
    return value


def empty_project_store():
    return {"version": PROJECT_STORE_VERSION, "revision": 0, "active_project_id": None, "projects": []}


def _normalize_generation(value, index):
    if not isinstance(value, dict):
        raise ValueError(f"Generation {index + 1} must be an object")
    result = copy.deepcopy(value)
    result["id"] = _identifier(result.get("id"), f"Generation {index + 1}")
    result["prompt_id"] = str(result.get("prompt_id") or "").strip()[:200]
    status = str(result.get("status") or "queued").strip().lower()
    if status not in {
        "validating", "compiling", "queueing", "queued", "generating",
        "complete", "error", "interrupted", "cancelled",
    }:
        raise ValueError(f"Generation {index + 1} has invalid status")
    result["status"] = status
    result["created_at"] = _timestamp(result.get("created_at"))
    result["updated_at"] = _timestamp(result.get("updated_at") or result["created_at"])
    if "document" in result:
        result["document"] = normalize_document(result["document"])
    if "workflow_snapshot" in result and not isinstance(result["workflow_snapshot"], dict):
        raise ValueError(f"Generation {index + 1} workflow snapshot must be an object")
    if "outputs" in result and not isinstance(result["outputs"], list):
        raise ValueError(f"Generation {index + 1} outputs must be a list")
    kind = str(result.get("kind") or ("extension" if result.get("parent_generation_id") else "base")).strip().lower()
    if kind not in {"base", "extension"}:
        raise ValueError(f"Generation {index + 1} has invalid kind")
    parent_id = str(result.get("parent_generation_id") or "").strip()
    if parent_id and not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", parent_id):
        raise ValueError(f"Generation {index + 1} has an invalid parent identifier")
    if kind == "extension" and not parent_id:
        raise ValueError(f"Generation {index + 1} extension has no parent")
    result["kind"] = kind
    result["parent_generation_id"] = parent_id
    result["root_generation_id"] = str(result.get("root_generation_id") or "").strip()
    result["depth"] = max(0, int(result.get("depth") or 0))
    result["total_effective_duration"] = max(
        0.0,
        float(result.get("total_effective_duration") or result.get("effective_duration") or 0),
    )
    if "segment_outputs" in result and not isinstance(result["segment_outputs"], list):
        raise ValueError(f"Generation {index + 1} segment outputs must be a list")
    result.setdefault("segment_outputs", [])
    if "assembly_outputs" in result and not isinstance(result["assembly_outputs"], list):
        raise ValueError(f"Generation {index + 1} assembly outputs must be a list")
    result.setdefault("assembly_outputs", [])
    if "continuation" in result and not isinstance(result["continuation"], dict):
        raise ValueError(f"Generation {index + 1} continuation metadata must be an object")
    return result


def _normalize_generation_lineage(generations, project_index):
    by_id = {generation["id"]: generation for generation in generations}
    if len(by_id) != len(generations):
        raise ValueError(f"Project {project_index + 1} generation identifiers must be unique")
    resolved = {}

    def lineage(generation_id, trail=()):
        if generation_id in resolved:
            return resolved[generation_id]
        if generation_id in trail:
            raise ValueError(f"Project {project_index + 1} generation lineage contains a cycle")
        generation = by_id[generation_id]
        parent_id = generation["parent_generation_id"]
        if not parent_id:
            value = (generation_id, 0)
        else:
            if parent_id not in by_id:
                # History is intentionally capped.  Keep the persisted lineage
                # coordinates when an older ancestor has fallen out of the
                # retained window; continuation metadata carries the source
                # segment descriptors needed to assemble the full version.
                saved_root = generation.get("root_generation_id") or generation_id
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", str(saved_root)):
                    saved_root = generation_id
                value = (str(saved_root), max(1, int(generation.get("depth") or 1)))
                resolved[generation_id] = value
                return value
            if parent_id == generation_id:
                raise ValueError(f"Project {project_index + 1} generation cannot parent itself")
            root_id, parent_depth = lineage(parent_id, (*trail, generation_id))
            value = (root_id, parent_depth + 1)
        resolved[generation_id] = value
        return value

    for generation in generations:
        root_id, depth = lineage(generation["id"])
        generation["root_generation_id"] = root_id
        generation["depth"] = depth
        generation["kind"] = "extension" if depth else "base"
    return generations


def _normalize_extension_source(value, project_index):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"Project {project_index + 1} extension source must be an object")
    parent_project_id = _identifier(
        value.get("parent_project_id"), f"Project {project_index + 1} extension parent project"
    )
    parent_generation_id = _identifier(
        value.get("parent_generation_id"), f"Project {project_index + 1} extension parent generation"
    )
    root_generation_id = str(value.get("root_generation_id") or parent_generation_id).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", root_generation_id):
        raise ValueError(f"Project {project_index + 1} extension root generation is invalid")
    source = copy.deepcopy(value.get("source"))
    if not isinstance(source, dict) or not str(source.get("filename") or "").strip():
        raise ValueError(f"Project {project_index + 1} extension source output is invalid")
    source_segments = copy.deepcopy(value.get("source_segments") or [])
    if not isinstance(source_segments, list) or not source_segments:
        raise ValueError(f"Project {project_index + 1} extension source segments must be a non-empty list")
    workflow_snapshot = copy.deepcopy(value.get("workflow_snapshot"))
    source_assembly_segments = copy.deepcopy(
        value.get("source_assembly_segments") or source_segments
    )
    if not isinstance(source_assembly_segments, list) or not source_assembly_segments:
        raise ValueError(f"Project {project_index + 1} extension assembly sources must be a non-empty list")
    if not isinstance(workflow_snapshot, dict):
        raise ValueError(f"Project {project_index + 1} extension workflow snapshot must be an object")
    result_node_ids = [str(item)[:200] for item in (value.get("result_node_ids") or [])]
    result_fields = [str(item)[:200] for item in (value.get("result_fields") or [])]
    director_context = copy.deepcopy(value.get("director_context") or {})
    if not isinstance(director_context, dict):
        raise ValueError(f"Project {project_index + 1} extension Director context must be an object")
    return {
        "engine": "native_h3_soft_av_39",
        "parent_project_id": parent_project_id,
        "parent_generation_id": parent_generation_id,
        "root_generation_id": root_generation_id,
        "depth": max(1, int(value.get("depth") or 1)),
        "continuation_base_duration": max(0.0, float(value.get("continuation_base_duration") or 0)),
        "source": source,
        "source_segments": source_segments,
        "source_document": normalize_document(value.get("source_document") or {}),
        "source_assembly_segments": source_assembly_segments,
        "parent_context_latent_path": str(value.get("parent_context_latent_path") or "")[:4096],
        "workflow_id": str(value.get("workflow_id") or "")[:1024],
        "workflow_name": str(value.get("workflow_name") or "")[:1024],
        "workflow_snapshot": workflow_snapshot,
        "workflow_director_node_id": str(value.get("workflow_director_node_id") or "")[:200],
        "result_node_ids": result_node_ids,
        "result_fields": result_fields,
        "director_context": director_context,
    }


def _normalize_pending_generation_restore(value):
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("Saved generation restore has an unsupported format")
    generation = _normalize_generation(value.get("generation"), 0)
    snapshot = generation.get("workflow_snapshot")
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("output"), dict):
        raise ValueError("Saved generation restore requires executable workflow inputs")
    if "workflow" in snapshot and not isinstance(snapshot["workflow"], dict):
        raise ValueError("Saved generation restore workflow envelope is invalid")
    if not isinstance(generation.get("document"), dict):
        raise ValueError("Saved generation restore requires an authored document")
    fingerprint = value.get("fingerprint")
    if not isinstance(fingerprint, dict) or not isinstance(fingerprint.get("document"), dict):
        raise ValueError("Saved generation restore requires its authoring fingerprint")
    return {
        "version": 1,
        "generation": generation,
        "fingerprint": {
            "document": normalize_document(fingerprint["document"]),
            "workflow_id": str(fingerprint.get("workflow_id") or "").strip()[:1024],
            "additional_input_selections": _normalize_additional_input_selections(
                fingerprint.get("additional_input_selections")
            ),
        },
    }


def _normalize_project(value, index):
    if not isinstance(value, dict):
        raise ValueError(f"Project {index + 1} must be an object")
    project_id = _identifier(value.get("id"), f"Project {index + 1}")
    generations = value.get("generations") or []
    if not isinstance(generations, list):
        raise ValueError(f"Project {index + 1} generations must be a list")
    # Retain complete history. The store byte bound rejects an oversized save
    # explicitly; normalization must never silently remove historical renders.
    created_at = _timestamp(value.get("created_at"))
    brief = _text(value.get("brief"), MAX_PROJECT_BRIEF_CHARS, "Project brief")
    document_value = copy.deepcopy(value.get("document") or {})
    if isinstance(document_value, dict) and not document_value.get("main_description") and brief:
        document_value["main_description"] = brief
        shots = document_value.get("shots")
        if (
            isinstance(shots, list)
            and shots
            and isinstance(shots[0], dict)
            and str(shots[0].get("action") or "").strip() == brief
        ):
            shots[0]["action"] = ""
    document = normalize_document(document_value)
    normalized_generations = [
        _normalize_generation(item, item_index)
        for item_index, item in enumerate(generations)
    ]
    _normalize_generation_lineage(normalized_generations, index)
    result = {
        "id": project_id,
        "name": _text(value.get("name") or "Untitled video", MAX_PROJECT_NAME_CHARS, "Project name"),
        "brief": document["main_description"],
        "document": document,
        "workflow_id": str(value.get("workflow_id") or "").strip()[:1024],
        "additional_input_selections": _normalize_additional_input_selections(
            value.get("additional_input_selections")
        ),
        "generations": normalized_generations,
        "created_at": created_at,
        "updated_at": _timestamp(value.get("updated_at") or created_at),
    }
    extension_source = _normalize_extension_source(value.get("extension_source"), index)
    pending_restore = _normalize_pending_generation_restore(value.get("pending_generation_restore"))
    if pending_restore is not None:
        result["pending_generation_restore"] = pending_restore
    if extension_source is not None:
        # Keep persisted authoring state lossless. Continuation execution owns
        # capability validation, including the current restriction on adding
        # media references to a structured extension. Rejecting that state here
        # would make one incompatible session prevent the entire store from
        # loading, leaving every otherwise-valid session inaccessible.
        result["extension_source"] = extension_source
    return result


def _normalize_additional_input_selections(value):
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > 1000:
        raise ValueError("Additional Input selections must be an object with at most 1000 entries")
    normalized = {}
    for key, entry in value.items():
        key = str(key)
        if not key or len(key) > 2048 or not isinstance(entry, dict):
            raise ValueError("Additional Input selection is invalid")
        selected = entry.get("value")
        if selected is not None and not isinstance(selected, (str, int, float, bool)):
            raise ValueError("Additional Input values must be JSON scalars")
        fingerprint = str(entry.get("schemaFingerprint") or "")
        if len(fingerprint) > 256 * 1024:
            raise ValueError("Additional Input schema fingerprint is too large")
        normalized[key] = {"value": selected, "schemaFingerprint": fingerprint}
    return normalized


def normalize_project_store(value):
    if not isinstance(value, dict):
        raise ValueError("Project store must be an object")
    if int(value.get("version") or STORE_VERSION) not in {STORE_VERSION, PROJECT_STORE_VERSION}:
        raise ValueError("Unsupported project store version")
    projects = value.get("projects") or []
    if not isinstance(projects, list) or len(projects) > MAX_PROJECTS:
        raise ValueError(f"Project store may contain at most {MAX_PROJECTS} projects")
    normalized = [_normalize_project(project, index) for index, project in enumerate(projects)]
    ids = [project["id"] for project in normalized]
    if len(ids) != len(set(ids)):
        raise ValueError("Project identifiers must be unique")
    active = value.get("active_project_id")
    active = str(active).strip() if active is not None else None
    if active not in set(ids):
        active = normalized[0]["id"] if normalized else None
    return {
        "version": PROJECT_STORE_VERSION,
        "revision": _revision(value.get("revision")),
        "active_project_id": active,
        "projects": normalized,
    }


def _project_store_directory(path, directory=None):
    return directory or os.path.splitext(path)[0]


def _project_index_path(directory):
    return os.path.join(directory, "index.json")


def _project_backups_directory(directory):
    return os.path.join(directory, "_backups")


def _project_file_name(project_id):
    digest = hashlib.sha256(project_id.encode("utf-8")).hexdigest()
    return f"project_{digest}.json"


def _project_file_path(directory, project_id):
    return os.path.join(directory, _project_file_name(project_id))


def _project_backup_path(directory, project_id):
    digest = hashlib.sha256(project_id.encode("utf-8")).hexdigest()
    return os.path.join(_project_backups_directory(directory), f"project_{digest}.bak")


def _read_project_index(directory):
    return shared_transactional_store().read_manifest(directory, "projectFiles", "project")


def _read_split_project_store(directory, index):
    index, projects = shared_transactional_store().read_snapshot(directory, "projectFiles", "project", index)
    result = normalize_project_store({
        "version": PROJECT_STORE_VERSION,
        "revision": index.get("revision"),
        "active_project_id": index.get("active_project_id"),
        "projects": projects,
    })
    if index.get("_recovery"):
        result["recovery"] = index["_recovery"]
    return result


def _write_split_project_store(directory, data):
    metadata = {key: value for key, value in data.items() if key not in {"projects", "recovery"}}
    shared_transactional_store().commit_records(
        directory, metadata, data["projects"], "projectFiles", "project",
        maximum_bytes=MAX_PROJECT_STORE_BYTES, summary_builder=_project_summary,
    )
    return data


def _project_summary(project):
    generations = project.get("generations") or []
    return {"id": project["id"], "name": str(project.get("name") or "Untitled video")[:200],
            "brief": str(project.get("brief") or "")[:200], "workflow_id": project.get("workflow_id", ""),
            "created_at": project.get("created_at", 0), "updated_at": project.get("updated_at", 0),
            "generation_count": len(generations),
            "pending_count": sum(generation.get("status") in {"validating", "compiling", "queueing", "queued", "generating"} for generation in generations),
            "last_generation_status": generations[-1].get("status") if generations else None}


def read_project_query(path, directory, query, _index=None):
    service = shared_transactional_store()
    index = _read_project_index(directory) if _index is None else _index
    if index is None:
        if (query.get("summaries") == "1" or query.get("limit") is not None) and os.path.isfile(path):
            return {"maintenance_required": True, "projects": [], "summaries": []}, 202
        data = read_project_store(path, directory)
        if query.get("revision") is not None and _revision(query.get("revision")) == data["revision"]:
            return {"revision": data["revision"]}, 204
        return data, 200
    revision = _revision(index.get("revision"))
    if not index.get("_recovery") and query.get("revision") is not None and _revision(query.get("revision")) == revision:
        return {"revision": revision}, 204
    entries = index["projectFiles"]
    base = {"version": PROJECT_STORE_VERSION, "revision": revision, "active_project_id": index.get("active_project_id"), "total": len(entries)}
    if index.get("_recovery"):
        base["recovery"] = index["_recovery"]
    project_id = query.get("project_id")
    if project_id is not None:
        selected_index, records = service.read_selected_snapshot(directory, index, "projectFiles", "project", [project_id])
        if selected_index is not index:
            return read_project_query(path, directory, query, selected_index)
        return ({**base, "projects": [_normalize_project(value, 0) for value in records], "partial": True}, 200) if records else ({"error": "Project was not found"}, 404)
    if query.get("limit") is None and query.get("summaries") != "1":
        return _read_split_project_store(directory, index), 200
    limit, offset = int(query.get("limit", 20)), int(query.get("offset", 0))
    if not 1 <= limit <= 100 or offset < 0:
        raise ValueError("Project page offset or limit is invalid")
    missing = sum(not isinstance(entry.get("summary"), dict) for entry in entries)
    if missing:
        return {**base, "projects": [], "summaries": [], "maintenance_required": True, "summary_records_remaining": missing}, 202
    ordered = sorted((entry["summary"] for entry in entries), key=lambda item: (-float(item["updated_at"]), -float(item["created_at"]), item["id"]))
    cursor = [query.get(key) for key in ("before_updated", "before_created", "before_id")]
    if any(value is not None for value in cursor):
        if any(value is None for value in cursor):
            raise ValueError("Project page cursor is incomplete")
        boundary = (-float(cursor[0]), -float(cursor[1]), cursor[2])
        ordered = [item for item in ordered if (-float(item["updated_at"]), -float(item["created_at"]), item["id"]) > boundary]
        offset = 0
    page = ordered[offset:offset + limit]
    selected = list(page)
    active = index.get("active_project_id")
    if query.get("include_active") == "1" and offset == 0 and active and all(item["id"] != active for item in selected):
        selected.extend(entry["summary"] for entry in entries if entry["id"] == active)
    if query.get("include_pending") == "1" and offset == 0:
        selected_ids = {item["id"] for item in selected}
        selected.extend(entry["summary"] for entry in entries if entry["id"] not in selected_ids and entry["summary"].get("pending_count", 0) > 0)
    result = {**base, "offset": offset, "nextOffset": offset + len(page), "hasMore": offset + len(page) < len(ordered),
              "nextCursor": ({"updated_at": page[-1]["updated_at"], "created_at": page[-1]["created_at"], "id": page[-1]["id"]} if page else None)}
    if query.get("summaries") == "1":
        result.update({"summaries": selected, "projects": []})
    else:
        selected_index, loaded = service.read_selected_snapshot(directory, index, "projectFiles", "project", [item["id"] for item in selected])
        if selected_index is not index:
            return read_project_query(path, directory, query, selected_index)
        by_id = {project["id"]: project for project in loaded}
        result["projects"] = [_normalize_project(by_id[item["id"]], position) for position, item in enumerate(selected)]
    return result, 200


def maintain_project_store(path, directory, offset=0, limit=100):
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0 or not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("Maintenance requires a nonnegative offset and a limit between 1 and 100")
    service = shared_transactional_store()
    with service.store_lock(directory):
        index = _read_project_index(directory)
        if index is None:
            read_project_store(path, directory)
            index = _read_project_index(directory)
        if index is None:
            return {"revision": 0, "processed": 0, "hasMore": False, "nextOffset": 0}
        if index.get("_recovery"):
            raise service.RecoveryRequiredError(index["_recovery"]["message"])
        entries = index["projectFiles"][offset:offset + limit]
        records = service.read_records(directory, index, "projectFiles", "project", [entry["id"] for entry in entries])
        saved = service.commit_record_updates(directory, {"revision": index["revision"] + 1}, records,
                                              "projectFiles", "project", expected_revision=index["revision"], summary_builder=_project_summary)
        return {"revision": saved["revision"], "processed": len(records), "nextOffset": offset + len(records),
                "hasMore": offset + len(records) < len(index["projectFiles"])}


def _read_legacy_project_store(path):
    try:
        with open(path, "r", encoding="utf-8") as file:
            value = json.load(file)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid Video Studio project store: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Invalid Video Studio project store: root must be an object")
    return value


def _archive_legacy_project_store(path, directory):
    backups = _project_backups_directory(directory)
    os.makedirs(backups, exist_ok=True)
    if os.path.isfile(path):
        shutil.copy2(path, os.path.join(backups, "legacy_store.bak"))
    legacy_backup = f"{path}.bak"
    if os.path.isfile(legacy_backup):
        shutil.copy2(legacy_backup, os.path.join(backups, "legacy_previous_store.bak"))


def read_project_store(path, directory=None):
    directory = _project_store_directory(path, directory)
    with shared_transactional_store().store_lock(directory):
        return _read_project_store_unlocked(path, directory)


def _read_project_store_unlocked(path, directory):
    try:
        index = _read_project_index(directory)
        if index is not None:
            return _read_split_project_store(directory, index)
        legacy = _read_legacy_project_store(path)
        if legacy is None:
            return empty_project_store()
        normalized = normalize_project_store(legacy)
        _write_split_project_store(directory, normalized)
        _archive_legacy_project_store(path, directory)
        return normalized
    except (ValueError, PromptDocumentError) as exc:
        raise RuntimeError(f"Invalid Video Studio project store: {exc}") from exc


def update_project_store(path, value, directory=None):
    directory = _project_store_directory(path, directory)
    with shared_transactional_store().store_lock(directory):
        return _update_project_store_unlocked(path, value, directory)


def _update_project_store_unlocked(path, value, directory):
    if isinstance(value, dict) and value.get("partial") is True:
        index = _read_project_index(directory)
        if index is not None:
            return _update_project_records(directory, value, index)
    current = read_project_store(path, directory)
    if current.get("recovery"):
        raise shared_transactional_store().RecoveryRequiredError(current["recovery"]["message"])
    if _revision(value.get("revision") if isinstance(value, dict) else None) != current["revision"]:
        raise StoreConflictError("Video projects changed in another browser. Reload before saving again.")
    normalized = normalize_project_store(value)
    normalized["revision"] = current["revision"] + 1
    return _write_split_project_store(directory, normalized)


def _update_project_records(directory, value, index):
    service = shared_transactional_store()
    if index.get("_recovery"):
        raise service.RecoveryRequiredError(index["_recovery"]["message"])
    if _revision(value.get("revision")) != index["revision"]:
        raise StoreConflictError("Video projects changed in another browser. Reload before saving again.")
    deleted = value.get("deletedProjectIds", [])
    if not isinstance(deleted, list) or any(not isinstance(item, str) for item in deleted):
        raise ValueError("deletedProjectIds must be a list of project ids")
    deleted = {item.strip() for item in deleted if item.strip()}
    normalized = normalize_project_store(value)
    records = [project for project in normalized["projects"] if project["id"] not in deleted]
    retained = ({entry["id"] for entry in index["projectFiles"]} | {project["id"] for project in records}) - deleted
    if len(retained) > MAX_PROJECTS:
        raise ValueError(f"Project store may contain at most {MAX_PROJECTS} projects")
    active = value.get("active_project_id", index.get("active_project_id"))
    if active not in retained:
        active = next((entry["id"] for entry in index["projectFiles"] if entry["id"] in retained), next(iter(sorted(retained)), None))
    saved = service.commit_record_updates(directory, {"revision": index["revision"] + 1, "active_project_id": active,
                                                      "deletedProjectIds": sorted(set(index.get("deletedProjectIds", [])) | deleted)},
                                         records, "projectFiles", "project", expected_revision=index["revision"], deleted_ids=deleted,
                                         maximum_bytes=MAX_PROJECT_STORE_BYTES, summary_builder=_project_summary)
    return {"version": PROJECT_STORE_VERSION, "revision": saved["revision"], "active_project_id": active, "projects": records, "partial": True}


def empty_workflow_store():
    return {"version": STORE_VERSION, "revision": 0, "templates": []}


def _normalize_workflow(value, index):
    if not isinstance(value, dict):
        raise ValueError(f"Workflow {index + 1} must be an object")
    path = str(value.get("path") or value.get("id") or "").strip().replace("\\", "/")
    filename = path.rsplit("/", 1)[-1]
    if not path or not filename.startswith("[PSV]") or not filename.lower().endswith(".json"):
        raise ValueError(f"Workflow {index + 1} must be a [PSV] JSON workflow")
    snapshot = value.get("snapshot")
    output = snapshot.get("output") if isinstance(snapshot, dict) else None
    if not isinstance(output, dict):
        raise ValueError(f"Workflow {index + 1} has no executable snapshot")
    director_id = str(value.get("director_node_id") or "").strip()
    if not director_id or output.get(director_id, {}).get("class_type") != "PSV_MiniMaxH3Director":
        raise ValueError(f"Workflow {index + 1} has no executable Prompt Studio Video Director")
    result_ids = [str(item) for item in (value.get("result_node_ids") or [])]
    if not result_ids or any(item not in output for item in result_ids):
        raise ValueError(f"Workflow {index + 1} has invalid result nodes")
    additional_inputs_value = value.get("additionalInputs", [])
    if additional_inputs_value is None:
        additional_inputs_value = []
    additional_inputs = copy.deepcopy(additional_inputs_value)
    if not isinstance(additional_inputs, list) or len(additional_inputs) > 1000:
        raise ValueError(f"Workflow {index + 1} has invalid Additional Inputs")
    seen_additional_ids = set()
    for descriptor in additional_inputs:
        if not isinstance(descriptor, dict):
            raise ValueError(f"Workflow {index + 1} has an invalid Additional Input")
        source_id = str(descriptor.get("id") or "").strip()
        target_id = str(descriptor.get("targetNodeId") or "").strip()
        input_name = str(descriptor.get("targetInputName") or "").strip()
        schema = descriptor.get("schema")
        input_type = str(schema.get("type") if isinstance(schema, dict) else "").upper()
        target_inputs = output.get(target_id, {}).get("inputs")
        if not source_id or source_id in seen_additional_ids:
            raise ValueError(f"Workflow {index + 1} has duplicate Additional Input nodes")
        if not isinstance(target_inputs, dict) or input_name not in target_inputs:
            raise ValueError(f"Workflow {index + 1} has an invalid Additional Input target")
        if input_type not in {"INT", "FLOAT", "BOOLEAN", "STRING", "COMBO"}:
            raise ValueError(f"Workflow {index + 1} has an unsupported Additional Input type")
        options = schema.get("options", []) if isinstance(schema, dict) else []
        if input_type == "COMBO" and (
            not isinstance(options, list)
            or any(not isinstance(option, (str, int, float)) or isinstance(option, bool) for option in options)
        ):
            raise ValueError(f"Workflow {index + 1} has invalid Additional Input options")
        default_value = descriptor.get("defaultValue")
        if default_value is not None and not isinstance(default_value, (str, int, float, bool)):
            raise ValueError(f"Workflow {index + 1} has an invalid Additional Input default")
        seen_additional_ids.add(source_id)
    cache_identity = value.get("cacheIdentity")
    cache_fields = ("version", "adapterId", "adapterVersion", "conversionVersion", "inputVersion", "contentHash", "capabilityHash")
    valid_cache = (
        isinstance(cache_identity, dict) and cache_identity.get("version") == 1
        and cache_identity.get("adapterId") in {"image", "minimax_h3"}
        and all(isinstance(cache_identity.get(key), int) and not isinstance(cache_identity[key], bool) and cache_identity[key] > 0
                for key in ("version", "adapterVersion", "conversionVersion", "inputVersion"))
        and all(isinstance(cache_identity.get(key), str) and re.fullmatch(r"[0-9a-f]{64}", cache_identity[key])
                for key in ("contentHash", "capabilityHash"))
    )
    return {
        "id": path,
        "path": path,
        "name": _text(value.get("name") or filename[:-5], 300, "Workflow name"),
        "adapter": "minimax_h3",
        "director_node_id": director_id,
        "result_node_ids": result_ids,
        "result_fields": [str(item) for item in (value.get("result_fields") or ["videos", "gifs", "images"])],
        "additionalInputs": additional_inputs,
        "promptStudioInputVersion": max(0, int(value.get("promptStudioInputVersion") or 0)),
        "snapshot": copy.deepcopy(snapshot),
        **({"cacheIdentity": {key: cache_identity[key] for key in cache_fields}} if valid_cache else {}),
        "source_modified": _timestamp(value.get("source_modified")),
        "updated_at": _timestamp(value.get("updated_at")),
        "stale": bool(value.get("stale", False)),
        "error": str(value.get("error") or "")[:2000],
    }


def normalize_workflow_store(value):
    if not isinstance(value, dict):
        raise ValueError("Workflow store must be an object")
    if int(value.get("version") or STORE_VERSION) != STORE_VERSION:
        raise ValueError("Unsupported workflow store version")
    templates = value.get("templates") or []
    if not isinstance(templates, list):
        raise ValueError("Workflow store templates must be a list")
    normalized = [_normalize_workflow(item, index) for index, item in enumerate(templates)]
    paths = [item["path"] for item in normalized]
    if len(paths) != len(set(paths)):
        raise ValueError("Workflow paths must be unique")
    return {"version": STORE_VERSION, "revision": _revision(value.get("revision")), "templates": normalized}


def read_workflow_store(path):
    try:
        return normalize_workflow_store(_read_json(path, empty_workflow_store, "Video Studio workflow store"))
    except ValueError as exc:
        raise RuntimeError(f"Invalid Video Studio workflow store: {exc}") from exc


def update_workflow_store(path, value):
    current = read_workflow_store(path)
    if _revision(value.get("revision") if isinstance(value, dict) else None) != current["revision"]:
        raise StoreConflictError("Video workflows changed in another browser. Reload before saving again.")
    normalized = normalize_workflow_store(value)
    normalized["revision"] = current["revision"] + 1
    return _atomic_write(path, normalized, MAX_WORKFLOW_STORE_BYTES)
