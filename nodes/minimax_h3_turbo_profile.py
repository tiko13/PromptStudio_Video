"""Mode-aware MiniMax H3 Turbo LoRA and sampling profiles."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePath


BASE_MODES = frozenset({"t2va", "i2va", "fl2va", "l2va"})
SUPPORTED_MODES = frozenset({*BASE_MODES, "ref2va"})
PROFILE_PRESETS = ("auto_quality", "fast_4step")

DEFAULT_FL2VA_MIXED_8STEP_LORA = (
    "MiniMax3\\minimax_h3_fl2v_lightx2v_turbo_8step_v1.0_"
    "resized_avg_rank_24_bf16.safetensors"
)
DEFAULT_FL2VA_MIXED_4STEP_LORA = (
    "MiniMax3\\minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy_"
    "resized_avg_rank_21_bf16.safetensors"
)
DEFAULT_FL2VA_768P_4STEP_LORA = (
    "MiniMax3\\minimax_h3_fl2v_lightx2v_turbo_4step_v1.0_768p_"
    "resized_avg_rank_31_bf16.safetensors"
)
DEFAULT_REF2VA_4STEP_LORA = (
    "MiniMax3\\minimax_h3_ref2v_lightx2v_turbo_4step_v0.1_"
    "resized_avg_rank_20_bf16.safetensors"
)


@dataclass(frozen=True)
class TurboSelection:
    profile_id: str
    lora_input: str
    steps: int
    shift_video: float
    shift_audio: float


def select_turbo_profile(mode, width, height, preset="auto_quality"):
    """Resolve one stable Turbo profile from the Director's concrete job."""
    normalized_mode = str(mode or "").strip().lower()
    normalized_preset = str(preset or "").strip().lower()
    width = int(width)
    height = int(height)
    if normalized_mode not in SUPPORTED_MODES:
        raise ValueError(f"Unsupported MiniMax H3 Turbo mode '{normalized_mode or mode}'")
    if normalized_preset not in PROFILE_PRESETS:
        raise ValueError(f"Unsupported MiniMax H3 Turbo preset '{normalized_preset or preset}'")
    if width <= 0 or height <= 0:
        raise ValueError("MiniMax H3 Turbo width and height must be positive")

    if normalized_mode == "ref2va":
        return TurboSelection("ref2va_4step_v0.1", "ref2va_4step_lora", 4, 12.0, 3.0)

    if (width, height) == (1344, 768):
        return TurboSelection("fl2va_768p_4step_v1.0", "fl2va_768p_4step_lora", 4, 6.0, 3.0)

    if normalized_preset == "fast_4step":
        return TurboSelection("fl2va_mixed_4step_v0.1", "fl2va_mixed_4step_lora", 4, 12.0, 3.0)

    return TurboSelection("fl2va_mixed_8step_v1.0", "fl2va_mixed_8step_lora", 8, 12.0, 3.0)


def _normalized_lora_name(value):
    return str(value or "").strip().replace("\\", "/").casefold()


def _lora_basename(value):
    return PurePath(str(value or "").replace("\\", "/")).name.casefold()


def resolve_installed_lora(configured_name, installed_names):
    """Resolve a pinned name exactly, then by an unambiguous basename."""
    configured_name = str(configured_name or "").strip()
    installed_names = [str(name) for name in installed_names]
    normalized = _normalized_lora_name(configured_name)
    exact = [name for name in installed_names if _normalized_lora_name(name) == normalized]
    if len(exact) == 1:
        return exact[0]

    basename = _lora_basename(configured_name)
    matches = [name for name in installed_names if _lora_basename(name) == basename]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        locations = ", ".join(matches)
        raise ValueError(
            f"Turbo LoRA '{configured_name}' is ambiguous; select its exact installed path: {locations}"
        )
    raise FileNotFoundError(
        f"Required MiniMax H3 Turbo LoRA '{configured_name}' is not installed in ComfyUI/models/loras"
    )


def _lora_choices(preferred_name):
    import folder_paths

    installed = list(folder_paths.get_filename_list("loras"))
    try:
        preferred = resolve_installed_lora(preferred_name, installed)
    except (FileNotFoundError, ValueError):
        preferred = preferred_name
    choices = []
    for name in (preferred, preferred_name, *installed):
        if name not in choices:
            choices.append(name)
    return choices


class PromptStudioMiniMaxH3TurboProfile:
    DESCRIPTION = (
        "Applies the installed LightX2V MiniMax H3 Turbo LoRA matching the Director's "
        "resolved mode and canvas, and returns its coupled step and sigma-shift settings."
    )

    def __init__(self):
        self._lora_loader = None

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("MODEL",),
                "mode": ("STRING", {"forceInput": True}),
                "width": ("INT", {"forceInput": True}),
                "height": ("INT", {"forceInput": True}),
                "preset": (PROFILE_PRESETS,),
                "fl2va_mixed_8step_lora": (_lora_choices(DEFAULT_FL2VA_MIXED_8STEP_LORA),),
                "fl2va_mixed_4step_lora": (_lora_choices(DEFAULT_FL2VA_MIXED_4STEP_LORA),),
                "fl2va_768p_4step_lora": (_lora_choices(DEFAULT_FL2VA_768P_4STEP_LORA),),
                "ref2va_4step_lora": (_lora_choices(DEFAULT_REF2VA_4STEP_LORA),),
                "strength_model": (
                    "FLOAT",
                    {"default": 0.75, "min": 0.0, "max": 2.0, "step": 0.05},
                ),
                "enabled": ("BOOLEAN", {"default": True}),
            }
        }

    RETURN_TYPES = ("MODEL", "INT", "FLOAT", "FLOAT", "STRING", "STRING")
    RETURN_NAMES = ("model", "steps", "shift_video", "shift_audio", "profile", "lora_name")
    FUNCTION = "apply_profile"
    CATEGORY = "Prompt Studio/Video"

    def apply_profile(
        self,
        model,
        mode,
        width,
        height,
        preset,
        fl2va_mixed_8step_lora,
        fl2va_mixed_4step_lora,
        fl2va_768p_4step_lora,
        ref2va_4step_lora,
        strength_model,
        enabled,
    ):
        if not enabled:
            return model, 20, 11.0, 4.0, "disabled", ""

        selection = select_turbo_profile(mode, width, height, preset)
        configured_names = {
            "fl2va_mixed_8step_lora": fl2va_mixed_8step_lora,
            "fl2va_mixed_4step_lora": fl2va_mixed_4step_lora,
            "fl2va_768p_4step_lora": fl2va_768p_4step_lora,
            "ref2va_4step_lora": ref2va_4step_lora,
        }

        import folder_paths
        from nodes import LoraLoaderModelOnly

        lora_name = resolve_installed_lora(
            configured_names[selection.lora_input],
            folder_paths.get_filename_list("loras"),
        )
        if self._lora_loader is None:
            self._lora_loader = LoraLoaderModelOnly()
        patched_model = self._lora_loader.load_lora_model_only(
            model, lora_name, float(strength_model),
        )[0]
        return (
            patched_model,
            selection.steps,
            selection.shift_video,
            selection.shift_audio,
            selection.profile_id,
            lora_name,
        )


__all__ = [
    "PromptStudioMiniMaxH3TurboProfile",
    "TurboSelection",
    "resolve_installed_lora",
    "select_turbo_profile",
]
