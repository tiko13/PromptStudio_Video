"""Structured MiniMax H3 Director built on current native ComfyUI nodes."""

from __future__ import annotations

import json

from ..video.compiler import compile_prompt
from ..video.contracts import default_document, effective_duration, frame_count_for_duration, normalize_document
from ..video.media import anchor_images, reference_inputs


DEFAULT_DOCUMENT_JSON = json.dumps(default_document(), separators=(",", ":"))


class PromptStudioMiniMaxH3Director:
    DESCRIPTION = (
        "Builds guide-compliant MiniMax H3 prompts and native conditioning from a "
        "structured shot, sound, dialogue, and reference document."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "clip": ("CLIP",),
                "video_vae": ("VAE",),
                "audio_vae": ("VAE",),
                "document_json": (
                    "STRING",
                    {"default": DEFAULT_DOCUMENT_JSON, "multiline": False},
                ),
            },
            "optional": {
                "fl2va_model": ("MODEL", {"lazy": True}),
                "ref2va_model": ("MODEL", {"lazy": True}),
            },
        }

    RETURN_TYPES = (
        "MODEL", "CONDITIONING", "LATENT", "INT", "FLOAT", "STRING", "STRING", "INT", "INT",
    )
    RETURN_NAMES = (
        "model",
        "positive",
        "latent",
        "frame_count",
        "effective_duration",
        "compiled_prompt",
        "mode",
        "width",
        "height",
    )
    FUNCTION = "build"
    CATEGORY = "Prompt Studio/Video"

    def check_lazy_status(
        self,
        clip,
        video_vae,
        audio_vae,
        document_json,
        fl2va_model=None,
        ref2va_model=None,
    ):
        del clip, video_vae, audio_vae
        mode = normalize_document(document_json)["resolved_mode"]
        selected = "ref2va_model" if mode == "ref2va" else "fl2va_model"
        value = ref2va_model if selected == "ref2va_model" else fl2va_model
        return [selected] if value is None else []

    def build(
        self,
        clip,
        video_vae,
        audio_vae,
        document_json,
        fl2va_model=None,
        ref2va_model=None,
    ):
        from comfy_extras.nodes_minimax_h3 import MiniMaxH3ImageToVideo, MiniMaxH3ReferenceToVideo

        document = normalize_document(document_json)
        prompt = compile_prompt(document)
        mode = document["resolved_mode"]
        frame_count = frame_count_for_duration(document["duration_seconds"])
        width = document["width"]
        height = document["height"]

        if mode == "ref2va":
            if ref2va_model is None:
                raise ValueError("REF2VA mode requires the ref2va_model input")
            ref_images, ref_videos, ref_video_audios, ref_audios = reference_inputs(document)
            result = MiniMaxH3ReferenceToVideo.execute(
                clip=clip,
                vae=video_vae,
                audio_vae=audio_vae,
                prompt=prompt,
                width=width,
                height=height,
                length=frame_count,
                ref_image_size=document["ref_image_size"],
                ref_images=ref_images,
                ref_videos=ref_videos,
                ref_video_audios=ref_video_audios,
                ref_audios=ref_audios,
            ).result
            selected_model = ref2va_model
        else:
            if fl2va_model is None:
                raise ValueError(f"{mode.upper()} mode requires the fl2va_model input")
            first_frame, last_frame = anchor_images(document)
            result = MiniMaxH3ImageToVideo.execute(
                clip=clip,
                vae=video_vae,
                prompt=prompt,
                width=width,
                height=height,
                length=frame_count,
                first_frame=first_frame,
                last_frame=last_frame,
            ).result
            selected_model = fl2va_model

        positive, latent = result
        return (
            selected_model,
            positive,
            latent,
            frame_count,
            effective_duration(document),
            prompt,
            mode,
            width,
            height,
        )
