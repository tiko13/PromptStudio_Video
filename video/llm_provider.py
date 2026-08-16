"""Thin adapter to the primary Prompt Studio local-LLM services."""

from __future__ import annotations

import importlib
import sys


_PRIMARY_ROUTES_MODULE = "ComfyUI_PromptStudio.routes"
_REQUIRED_SERVICES = ("shared_llm_generate", "shared_llm_status", "shared_llm_abort")


def _primary_routes():
    """Resolve the already-loaded primary Prompt Studio routes module."""
    try:
        module = importlib.import_module(_PRIMARY_ROUTES_MODULE)
    except ModuleNotFoundError:
        module = next(
            (
                candidate
                for name, candidate in tuple(sys.modules.items())
                if name.endswith(".routes")
                and all(hasattr(candidate, service) for service in _REQUIRED_SERVICES)
            ),
            None,
        )
    if module is None or not all(hasattr(module, service) for service in _REQUIRED_SERVICES):
        raise RuntimeError(
            "PromptStudio_Video requires the companion ComfyUI_PromptStudio extension. "
            "Install or update Prompt Studio, then restart ComfyUI."
        )
    return module


def generation_status(data):
    """Use Prompt Studio's shared provider health and live-token implementation."""
    return _primary_routes().shared_llm_status(data)


def abort_generation(data):
    """Use Prompt Studio's provider-specific abort implementation."""
    return _primary_routes().shared_llm_abort(data)


def generate_chat(data, messages, images=None):
    """Run a Video Director request through Prompt Studio's provider dispatcher."""
    return _primary_routes().shared_llm_generate(data, messages, images or [])
