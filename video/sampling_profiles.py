"""Versioned H3 sampling contracts. Legacy Turbo policy remains unchanged."""
from dataclasses import asdict, dataclass

POLICY_VERSION = 2
PRESETS = ("balanced", "fast", "full_quality", "experimental_taomate", "experimental_fasth3")
BASE_MODES = {"t2va", "i2va", "fl2va", "l2va"}


@dataclass(frozen=True)
class SamplingProfile:
    id: str
    steps: int
    shift_video: float
    shift_audio: float
    lora: str = ""
    strength: float = 1.0
    sampler: str = "euler"
    scheduler: str = "simple"
    experimental: bool = False
    sigmas: tuple[float, ...] = ()


def select_profile(mode, width, height, preset="balanced"):
    if mode not in BASE_MODES | {"ref2va"}:
        raise ValueError("Unsupported H3 generation mode")
    if preset not in PRESETS:
        raise ValueError("Unsupported H3 sampling preset")
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not value > 0 or not value < float("inf") or int(value) != value
           for value in (width, height)):
        raise ValueError("H3 canvas dimensions must be positive whole numbers")
    width, height = int(width), int(height)
    if min(width, height) <= 0 or width % 32 or height % 32:
        raise ValueError("H3 sampling requires positive canvas dimensions divisible by 32")
    if width * height > 1344 * 768:
        raise ValueError("This sampling profile supports up to 1344×768 pixels; generate then upscale")
    if preset == "full_quality":
        return SamplingProfile("base_quality_v2", 25 if mode == "ref2va" else 20, 11.0, 4.0)
    if preset == "experimental_fasth3":
        if mode not in BASE_MODES:
            raise ValueError("FastH3 has no distilled REF2VA support; choose a base H3 workflow")
        return SamplingProfile("fasth3_v2_8step", 8, 10.0, 3.0,
                               sampler="res_multistep", experimental=True)
    if preset == "experimental_taomate":
        if mode != "t2va":
            raise ValueError("The experimental TaoMate profile is restricted to T2VA")
        return SamplingProfile("taomate_3step_experimental", 3, 12.0, 3.0,
                               "minimax_h3_taomate_3step_lora_avg_rank_19_bf16.safetensors",
                               scheduler="taomate_50grid", experimental=True,
                               sigmas=tuple(12 * (49 - index) / (49 + 11 * (49 - index))
                                            for index in (0, 16, 33, 49)))
    if mode == "ref2va":
        # There is no new official 4-step reference adapter. Both user intents
        # resolve to this dedicated reference model rather than an FL2V LoRA.
        return SamplingProfile("ref2va_768p_8step_v1.0", 8, 12.0, 3.0,
                               "minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors")
    if preset == "fast":
        return SamplingProfile("fl2va_768p_4step_v1.2", 4, 6.0, 3.0,
                               "minimax_h3_fl2v_turbo_4step_v1.2_768p_comfyui_bf16.safetensors")
    return SamplingProfile("fl2va_768p_8step_v1.0", 8, 6.0, 3.0,
                           "minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors")


def profile_catalog():
    return {"version": POLICY_VERSION, "presets": list(PRESETS), "profiles": {
        mode: {preset: asdict(select_profile(mode, 1344, 768, preset))
               for preset in PRESETS
               if not (preset == "experimental_fasth3" and mode == "ref2va")
               and not (preset == "experimental_taomate" and mode != "t2va")}
        for mode in sorted(BASE_MODES | {"ref2va"})}}
