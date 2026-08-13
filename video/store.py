"""Durable project and workflow stores for the standalone Video Studio."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import tempfile
import time

from .contracts import PromptDocumentError, normalize_document


STORE_VERSION = 1
PROJECT_STORE_VERSION = 2
MAX_PROJECTS = 500
MAX_GENERATIONS_PER_PROJECT = 200
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


def _normalize_project(value, index):
    if not isinstance(value, dict):
        raise ValueError(f"Project {index + 1} must be an object")
    project_id = _identifier(value.get("id"), f"Project {index + 1}")
    generations = value.get("generations") or []
    if not isinstance(generations, list):
        raise ValueError(f"Project {index + 1} generations must be a list")
    generations = generations[-MAX_GENERATIONS_PER_PROJECT:]
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
    return {
        "id": project_id,
        "name": _text(value.get("name") or "Untitled video", MAX_PROJECT_NAME_CHARS, "Project name"),
        "brief": document["main_description"],
        "document": document,
        "workflow_id": str(value.get("workflow_id") or "").strip()[:1024],
        "generations": normalized_generations,
        "created_at": created_at,
        "updated_at": _timestamp(value.get("updated_at") or created_at),
    }


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
    try:
        with open(_project_index_path(directory), "r", encoding="utf-8") as file:
            index = json.load(file)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid Video Studio project index: {exc}") from exc
    if (
        not isinstance(index, dict)
        or index.get("version") != PROJECT_STORE_VERSION
        or not isinstance(index.get("projectFiles"), list)
    ):
        raise RuntimeError("Video Studio project index must contain a projectFiles list")
    return index


def _read_split_project_store(directory, index):
    projects = []
    seen_ids = set()
    for position, entry in enumerate(index["projectFiles"]):
        if not isinstance(entry, dict):
            raise RuntimeError(f"Video Studio project index entry {position + 1} must be an object")
        project_id = str(entry.get("id") or "").strip()
        expected_file = _project_file_name(project_id) if project_id else ""
        if not project_id or entry.get("file") != expected_file or project_id in seen_ids:
            raise RuntimeError(f"Invalid Video Studio project index entry {position + 1}")
        seen_ids.add(project_id)
        path = os.path.join(directory, expected_file)
        try:
            with open(path, "r", encoding="utf-8") as file:
                project = json.load(file)
        except FileNotFoundError as exc:
            raise RuntimeError(f"Video Studio project file is missing: {expected_file}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid Video Studio project file {expected_file}: {exc}") from exc
        if not isinstance(project, dict) or str(project.get("id") or "").strip() != project_id:
            raise RuntimeError(f"Video Studio project file does not match index id {project_id!r}")
        projects.append(project)
    return normalize_project_store({
        "version": PROJECT_STORE_VERSION,
        "revision": index.get("revision"),
        "active_project_id": index.get("active_project_id"),
        "projects": projects,
    })


def _write_split_project_store(directory, data):
    previous_index = _read_project_index(directory)
    previous_entries = previous_index.get("projectFiles", []) if previous_index else []
    os.makedirs(directory, exist_ok=True)
    entries = []
    for project in data["projects"]:
        project_id = project["id"]
        filename = _project_file_name(project_id)
        entries.append({"id": project_id, "file": filename})
        _atomic_write(
            _project_file_path(directory, project_id),
            project,
            MAX_PROJECT_STORE_BYTES,
            backup_path=_project_backup_path(directory, project_id),
            skip_unchanged=True,
        )
    index = {
        "version": PROJECT_STORE_VERSION,
        "revision": _revision(data.get("revision")),
        "active_project_id": data.get("active_project_id"),
        "projectFiles": entries,
    }
    _atomic_write(
        _project_index_path(directory),
        index,
        MAX_PROJECT_STORE_BYTES,
        backup_path=os.path.join(_project_backups_directory(directory), "index.bak"),
    )
    retained_files = {entry["file"] for entry in entries}
    for entry in previous_entries:
        project_id = str(entry.get("id") or "").strip() if isinstance(entry, dict) else ""
        filename = entry.get("file") if isinstance(entry, dict) else None
        if not project_id or filename != _project_file_name(project_id) or filename in retained_files:
            continue
        project_path = os.path.join(directory, filename)
        if os.path.isfile(project_path):
            os.makedirs(_project_backups_directory(directory), exist_ok=True)
            os.replace(project_path, _project_backup_path(directory, project_id))
    return data


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
        os.replace(path, os.path.join(backups, "legacy_store.bak"))
    legacy_backup = f"{path}.bak"
    if os.path.isfile(legacy_backup):
        os.replace(legacy_backup, os.path.join(backups, "legacy_previous_store.bak"))


def read_project_store(path, directory=None):
    directory = _project_store_directory(path, directory)
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
    current = read_project_store(path, directory)
    if _revision(value.get("revision") if isinstance(value, dict) else None) != current["revision"]:
        raise StoreConflictError("Video projects changed in another browser. Reload before saving again.")
    normalized = normalize_project_store(value)
    normalized["revision"] = current["revision"] + 1
    return _write_split_project_store(directory, normalized)


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
    return {
        "id": path,
        "path": path,
        "name": _text(value.get("name") or filename[:-5], 300, "Workflow name"),
        "adapter": "minimax_h3",
        "director_node_id": director_id,
        "result_node_ids": result_ids,
        "result_fields": [str(item) for item in (value.get("result_fields") or ["videos", "gifs", "images"])],
        "snapshot": copy.deepcopy(snapshot),
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
