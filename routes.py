"""Versioned integration surface for ComfyUI_PromptStudio."""

from aiohttp import web
from server import PromptServer


CAPABILITY = {
    "api_version": 1,
    "prompt_document_version": 1,
    "workflow_prefix": "[PSV]",
    "adapters": ["minimax_h3"],
    "node_types": ["PSV_MiniMaxH3Director"],
    "standalone_page": None,
}


async def promptstudio_video_capabilities(_request):
    return web.json_response(CAPABILITY)


def register_routes():
    """Register after ComfyUI has created its PromptServer singleton."""
    server = getattr(PromptServer, "instance", None)
    if server is None:
        return False
    server.routes.get("/promptstudio-video/capabilities")(promptstudio_video_capabilities)
    return True


register_routes()
