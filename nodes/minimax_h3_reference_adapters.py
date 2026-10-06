"""Saved reference adapters stay separate from ordinary and Turbo LoRAs."""
import hashlib
from ..video.adapter_contract import normalize_adapter_stack
from ..video.model_catalog import matches_folder_type
from ..video.reference_adapters import apply_reference_adapters, resolve_asset, register_reference_folders, adapter_catalog, inspect_adapter


def selection(reference_stack_json, adapter="None", lora_strength=1.0, visual_strength=1.0, audio_strength=1.0, components="all", adapter_type="*"):
    rows = normalize_adapter_stack(reference_stack_json, references=True)
    if adapter != "None":
        if rows:
            raise ValueError("Choose a single adapter or a Studio stack, not both")
        category, name = adapter.split(":", 1)
        _, path = resolve_asset(category, name)
        rows = [{"category":category, "name":name, "kind":inspect_adapter(path)["kind"],
                 "lora_strength":lora_strength, "visual_strength":visual_strength,
                 "audio_strength":audio_strength, "components":components}]
    for row in rows:
        if not matches_folder_type(row["name"], adapter_type):
            raise ValueError(f"Adapter '{row['name']}' is not inside the '{adapter_type}' adapter Type folder")
    return rows


class PromptStudioMiniMaxH3ReferenceAdapters:
    @classmethod
    def INPUT_TYPES(cls):
        register_reference_folders()
        choices = ["None"] + [f"{row['category']}:{row['name']}" for row in adapter_catalog()["adapters"] if row["kind"] != "lora"]
        return {"required": {"model": ("MODEL",), "positive": ("CONDITIONING",),
                             "mode": ("STRING", {"default": "ref2va", "forceInput": True})},
                "optional": {"reference_stack_json": ("STRING", {"default": "[]", "multiline": True}),
                             "max_reference_tokens": ("INT", {"default": 16384, "min": 1, "max": 65536}),
                             "adapter": (choices,),
                             "lora_strength": ("FLOAT", {"default": 1, "min": -100, "max":100, "step":0.05}),
                             "visual_strength": ("FLOAT", {"default": 1, "min": 0, "max":1, "step":0.05}),
                             "audio_strength": ("FLOAT", {"default": 1, "min": 0, "max":1, "step":0.05}),
                             "components": (["all", "visual", "audio"],),
                             "adapter_type": ("STRING", {"default": "MiniMax3", "tooltip": "Top-level folder under loras, refmods or audio_refmods; * offers all folders. Filters Studio selections and validates execution."})}}

    RETURN_TYPES = ("MODEL", "CONDITIONING")
    RETURN_NAMES = ("model", "positive")
    FUNCTION = "apply"
    CATEGORY = "Prompt Studio/Video"
    DESCRIPTION = "Load RefMods and RefLoRAs. RefLoRA weight and visual/audio reference strengths are independent. Active references require REF2VA."

    @classmethod
    def IS_CHANGED(cls, reference_stack_json="[]", **kwargs):
        stamps = []
        for row in selection(reference_stack_json, **{k:v for k,v in kwargs.items() if k in {"adapter","lora_strength","visual_strength","audio_strength","components","adapter_type"}}):
            _, path = resolve_asset(row["category"], row["name"])
            for candidate in (path, path.with_suffix(".json")):
                if candidate.is_file():
                    stat = candidate.stat()
                    stamps.append((str(candidate), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
        return hashlib.sha256(repr(stamps).encode()).hexdigest()

    def apply(self, model, positive, mode, reference_stack_json="[]", max_reference_tokens=16384,
              adapter="None", lora_strength=1.0, visual_strength=1.0, audio_strength=1.0, components="all", adapter_type="*"):
        rows = selection(reference_stack_json, adapter, lora_strength, visual_strength, audio_strength, components, adapter_type)
        return apply_reference_adapters(model, positive, mode, rows, max_reference_tokens)
