"""Consume Prompt Studio's shared folder Type contract without runtime imports."""
import importlib.util
from pathlib import Path
import sys

_name = "_promptstudio_model_catalog"
if _name not in sys.modules:
    _path = Path(__file__).resolve().parents[2] / "ComfyUI_PromptStudio" / "model_catalog.py"
    _spec = importlib.util.spec_from_file_location(_name, _path)
    _module = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_module)
    sys.modules[_name] = _module

matches_folder_type = sys.modules[_name].matches_folder_type
normalized_folder_type = sys.modules[_name].normalized_folder_type
