"""Apply timed conditioning through the native H3 guide node."""
from .contracts import PromptDocumentError, frame_count_for_duration
from .media_budget import bounded_media


@bounded_media
def apply_timeline_guides(document, positive, latent, video_vae, audio_vae):
    guides = [item for item in document["references"] if "timeline_guide" in item["roles"]]
    if not guides:
        return positive, latent
    from nodes import NODE_CLASS_MAPPINGS
    from .media import _load_image, _load_audio, _video_components
    node = NODE_CLASS_MAPPINGS.get("MiniMaxH3AddGuide")
    if node is None:
        raise PromptDocumentError("Timeline guides require ComfyUI's MiniMaxH3AddGuide node")
    # Decode/apply one source at a time under the shared media admission guard.
    for guide in sorted(guides, key=lambda value: value["guide_frame"]):
        image = audio = None
        if guide["kind"] == "image":
            image = _load_image(guide["path"])
        elif guide["kind"] == "video":
            image, audio, _fps = _video_components(guide["path"], guide["trim_start"], guide["trim_end"], guide["use_embedded_audio"], minimum=5 / 24)
        else:
            audio = _load_audio(guide["path"], guide["trim_start"], guide["trim_end"], minimum=0.01)
        count = len(image) if image is not None else 1
        count = count - (count - 5) % 17 if count >= 5 else 1
        if guide["guide_frame"] + count > frame_count_for_duration(document["duration_seconds"]):
            raise PromptDocumentError("The guide clip extends beyond the video; trim it or move it earlier")
        positive = node.execute(positive=positive, latent=latent,
                                       frame_idx=guide["guide_frame"], vae=video_vae,
                                       audio_vae=audio_vae, image=image, audio=audio).result[0]
    return positive, latent
