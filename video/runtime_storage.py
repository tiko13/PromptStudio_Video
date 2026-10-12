"""Use the companion's storage migration without importing its ComfyUI entrypoint."""

import importlib.util
from pathlib import Path


def migrate_runtime_state():
    root = Path(__file__).resolve().parents[1]
    path = root.parent / "ComfyUI_PromptStudio" / "backend" / "runtime_storage.py"
    spec = importlib.util.spec_from_file_location("_promptstudio_runtime_storage", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.migrate_legacy_state(root, module.VIDEO_STATE)

