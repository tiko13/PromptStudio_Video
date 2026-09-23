"""Video-owned catalog adapter to Prompt Studio's pure help implementation."""
import importlib.util
from pathlib import Path
import sys

CATALOG_ROOT = Path(__file__).resolve().parents[1] / "assistant-help"


def shared_help():
    name = "_promptstudio_assistant_help"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[2] / "ComfyUI_PromptStudio" / "assistant_help.py"
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError("Update Prompt Studio to use shared assistant help")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules[name] = module
    return sys.modules[name]
