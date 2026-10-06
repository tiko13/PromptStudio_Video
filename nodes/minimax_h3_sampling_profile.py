"""Native H3 sampling profiles with an explicit safe attention baseline."""
import logging

from ..video.sampling_profiles import PRESETS, select_profile
from .minimax_h3_turbo_profile import resolve_installed_lora


class PromptStudioMiniMaxH3SamplingProfile:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "model": ("MODEL",), "mode": ("STRING", {"forceInput": True}),
            "width": ("INT", {"forceInput": True}), "height": ("INT", {"forceInput": True}),
            "preset": (PRESETS,),
            "attention": (("safe_dense", "experimental_sol"),),
        }}

    RETURN_TYPES = ("MODEL", "INT", "FLOAT", "FLOAT", "STRING", "STRING", "SAMPLER")
    RETURN_NAMES = ("model", "steps", "shift_video", "shift_audio", "profile", "lora_name", "sampler")
    FUNCTION = "apply"
    CATEGORY = "Prompt Studio/Video"
    DESCRIPTION = "Version 2 sampling policy. Uses PyTorch dense attention as the safe baseline; experimental sparse attention preserves audio rows. Legacy Turbo workflows are unchanged."

    def __init__(self):
        self._loader = None

    def apply(self, model, mode, width, height, preset, attention):
        import comfy.samplers
        import folder_paths
        from nodes import NODE_CLASS_MAPPINGS, LoraLoaderModelOnly

        profile = select_profile(mode, width, height, preset)
        if attention not in {"safe_dense", "experimental_sol"}:
            raise ValueError("Unknown H3 attention policy")
        diffusion = model.get_model_object("diffusion_model")
        blocks = getattr(diffusion, "blocks", ())
        is_fasth3 = bool(blocks and getattr(getattr(blocks[0], "attn", None), "to_gate_compress", None) is not None)
        if is_fasth3 != (preset == "experimental_fasth3"):
            raise ValueError("FastH3 VSA checkpoint and sampling profile must be selected together")
        lora_name = ""
        if profile.lora:
            lora_name = resolve_installed_lora(profile.lora, folder_paths.get_filename_list("loras"))
            if self._loader is None:
                self._loader = LoraLoaderModelOnly()
            model = self._loader.load_lora_model_only(model, lora_name, profile.strength)[0]
        backend = NODE_CLASS_MAPPINGS.get("ModelAttentionBackend")
        if backend is None:
            raise RuntimeError("This workflow requires ComfyUI's Model Attention Backend node")
        model = backend.execute(model=model, attention="pytorch attention").result[0]
        if attention == "experimental_sol" or is_fasth3:
            # Sparse attention captures sigma thresholds when installed. Apply
            # the coupled schedule first; downstream identical shifts are safe.
            shift = NODE_CLASS_MAPPINGS.get("MiniMaxH3SigmaShift")
            if shift is None:
                raise RuntimeError("This profile requires MiniMaxH3SigmaShift")
            model = shift.execute(model=model, shift_video=profile.shift_video,
                                  shift_audio=profile.shift_audio).result[0]
            sparse = NODE_CLASS_MAPPINGS.get("BlockSparseAttention")
            if sparse is None:
                raise RuntimeError("This profile requires native Model Sparse Attention support")
            # Fail explicitly rather than advertise a speedup while silently
            # falling back for a missing kernel. Short sequences may stay dense.
            import comfy_kitchen
            import comfy.model_management
            device = comfy.model_management.get_torch_device()
            if not comfy_kitchen.sol_attn_is_available(device):
                raise RuntimeError("Sparse attention kernel is unavailable on this device; choose safe_dense")
            selection = {"selection": "vsa", "keep_percent": 10.0} if is_fasth3 else {"selection": "sol-attn", "tau": 1.0}
            model = sparse.execute(model=model, selection=selection,
                                   start_percent=0.2 if is_fasth3 else 0.4,
                                   end_percent=1.0 if is_fasth3 else 0.9,
                                   min_tokens=12288, extra_tokens=0 if is_fasth3 else 256,
                                   sink_conditioning="exact_kv_and_rows", verbose=True).result[0]
        logging.info("Prompt Studio H3 profile=%s attention=%s LoRA=%s strength=%s",
                     profile.id, "vsa" if is_fasth3 else attention, lora_name, profile.strength)
        return (model, profile.steps, profile.shift_video, profile.shift_audio,
                profile.id, lora_name, comfy.samplers.sampler_object(profile.sampler))
