"""Validate and encode Director image attachments from ComfyUI input storage."""

from __future__ import annotations

import base64
import io
import mimetypes
import os

from PIL import Image, ImageOps


MAX_DIRECTOR_IMAGES = 4
MAX_DIRECTOR_IMAGE_BYTES = 20 * 1024 * 1024
DIRECTOR_IMAGE_USAGES = {
    "describe",
    "first_frame",
    "last_frame",
    "subject",
    "scene",
    "style",
    "action",
    "pose",
    "camera",
    "storyboard",
}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
MAX_VISION_DIMENSION = 2048


def _text(value, maximum):
    return str(value or "").strip()[:maximum]


def normalize_attachments(value):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("Director attachments must be a list")
    if len(value) > MAX_DIRECTOR_IMAGES:
        raise ValueError(f"Director accepts at most {MAX_DIRECTOR_IMAGES} images per turn")
    normalized = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"Director attachment {index + 1} must be an object")
        path = _text(item.get("path"), 1_024).replace("\\", "/")
        if not path:
            raise ValueError(f"Director attachment {index + 1} has no ComfyUI input path")
        usage = _text(item.get("usage"), 40).casefold() or "describe"
        if usage not in DIRECTOR_IMAGE_USAGES:
            raise ValueError(f"Director attachment {index + 1} has unsupported usage '{usage}'")
        normalized.append({
            "id": _text(item.get("id"), 100),
            "path": path,
            "name": _text(item.get("name") or os.path.basename(path), 300),
            "usage": usage,
            "reference_id": _text(item.get("reference_id"), 100),
            "source_width": max(0, int(item.get("source_width") or 0)),
            "source_height": max(0, int(item.get("source_height") or 0)),
        })
    return normalized


def _input_path(relative_path):
    import folder_paths

    if os.path.isabs(relative_path) or os.path.splitdrive(relative_path)[0]:
        raise ValueError("Director attachment path must be relative to ComfyUI input storage")
    normalized = os.path.normpath(relative_path.replace("/", os.sep))
    if normalized == ".." or normalized.startswith(".." + os.sep):
        raise ValueError("Director attachment path escapes ComfyUI input storage")
    input_root = os.path.realpath(folder_paths.get_input_directory())
    path = os.path.realpath(os.path.join(input_root, normalized))
    try:
        inside = os.path.commonpath([input_root, path]) == input_root
    except ValueError:
        inside = False
    if not inside:
        raise ValueError("Director attachment path escapes ComfyUI input storage")
    return path


def load_vision_images(value):
    attachments = normalize_attachments(value)
    images = []
    for index, attachment in enumerate(attachments):
        path = _input_path(attachment["path"])
        extension = os.path.splitext(path)[1].casefold()
        if extension not in IMAGE_EXTENSIONS:
            raise ValueError(f"Director attachment {index + 1} is not a supported image")
        try:
            size = os.path.getsize(path)
        except OSError as exc:
            raise ValueError(f"Director attachment {index + 1} is missing from ComfyUI input storage") from exc
        if size <= 0:
            raise ValueError(f"Director attachment {index + 1} is empty")
        if size > MAX_DIRECTOR_IMAGE_BYTES:
            raise ValueError(f"Director attachment {index + 1} exceeds the 20 MB image limit")
        source_mime_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if not source_mime_type.startswith("image/"):
            raise ValueError(f"Director attachment {index + 1} has an invalid image type")
        try:
            with Image.open(path) as source:
                source.seek(0)
                image = ImageOps.exif_transpose(source)
                if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
                    rgba = image.convert("RGBA")
                    flattened = Image.new("RGB", rgba.size, "white")
                    flattened.paste(rgba, mask=rgba.getchannel("A"))
                    image = flattened
                else:
                    image = image.convert("RGB")
                if max(image.size) > MAX_VISION_DIMENSION:
                    image.thumbnail(
                        (MAX_VISION_DIMENSION, MAX_VISION_DIMENSION),
                        Image.Resampling.LANCZOS,
                    )
                buffer = io.BytesIO()
                image.save(buffer, format="PNG", optimize=True)
                encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise ValueError(f"Director attachment {index + 1} could not be decoded as an image") from exc
        mime_type = "image/png"
        images.append({
            "base64": encoded,
            "data_uri": f"data:{mime_type};base64,{encoded}",
            "mime_type": mime_type,
            "name": attachment["name"],
            "usage": attachment["usage"],
        })
    return attachments, images
