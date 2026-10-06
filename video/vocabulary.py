"""Load the companion's pure vocabulary rules without ComfyUI startup imports."""

import importlib.util
from pathlib import Path
import sys


def shared_rules():
    name = "_promptstudio_forbidden_words"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[2] / "ComfyUI_PromptStudio" / "forbidden_words.py"
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError("Update Prompt Studio to load shared forbidden-word rules")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules[name] = module
    return sys.modules[name]
