"""Thin adapter to the primary Prompt Studio local-LLM services."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

from .contracts import shared_wire_contracts


def _service_api():
    """Load only the pure versioned API; never discover or import route modules."""
    name = "_promptstudio_companion_api"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[2] / "ComfyUI_PromptStudio" / "product_services.py"
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None or not path.is_file():
            raise RuntimeError("PromptStudio_Video requires the companion ComfyUI_PromptStudio. Install or update both studios.")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(name, None)
            raise
    return sys.modules[name]


def _primary_routes():
    """Compatibility wrapper returning explicitly published services, not routes."""
    return _service_api().require_services()


def generation_status(data):
    """Use Prompt Studio's shared provider health and live-token implementation."""
    return _primary_routes().shared_llm_status(data)


def job_ledger():
    return _service_api().require_services(capabilities={"jobs.activity"}).shared_job_ledger()


def job_status(job_id):
    return _service_api().require_services(capabilities={"jobs.activity"}).shared_job_status(job_id, studio="video")


def abort_generation(data):
    """Use Prompt Studio's provider-specific abort implementation."""
    return _primary_routes().shared_llm_abort(data)


def generate_chat(data, messages, images=None):
    """Run a Video Director request through Prompt Studio's provider dispatcher."""
    return _primary_routes().shared_llm_generate(_validated_settings(data), messages, images or [])


def _validated_settings(data):
    normalized = shared_wire_contracts().normalize_provider_settings(data)
    # Retain operation fields and legacy omitted-field defaults. Normalize only
    # supplied shared settings instead of injecting new inference defaults.
    return {**data, **{key: value for key, value in normalized.items() if key in data}}


async def run_operation(data, operation, *, cancellation_check=None, job_context=None):
    """Acquire shared resources once for the complete multistage Director turn."""
    options = {"job_context": job_context} if job_context else {}
    return await _primary_routes().shared_llm_run(
        _validated_settings(data), operation, priority=0, cancellation_check=cancellation_check,
        **options,
    )


def check_admission(*, local_full=False):
    """Use the companion's overload contract before creating a Video job."""
    primary = _primary_routes()
    if local_full:
        raise primary.LlmOverloadedError("Video Director capacity is full; retry after a job finishes")
    primary.shared_llm_check_admission()
