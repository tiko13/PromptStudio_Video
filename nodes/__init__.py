from .minimax_h3_director import PromptStudioMiniMaxH3Director
from .minimax_h3_turbo_profile import PromptStudioMiniMaxH3TurboProfile
from .h3_motion_context import (
    NODE_CLASS_MAPPINGS as CONTEXT_NODE_CLASS_MAPPINGS,
    NODE_DISPLAY_NAME_MAPPINGS as CONTEXT_NODE_DISPLAY_NAME_MAPPINGS,
)


NODE_CLASS_MAPPINGS = {
    "PSV_MiniMaxH3Director": PromptStudioMiniMaxH3Director,
    "PSV_MiniMaxH3TurboProfile": PromptStudioMiniMaxH3TurboProfile,
    **CONTEXT_NODE_CLASS_MAPPINGS,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "PSV_MiniMaxH3Director": "Prompt Studio MiniMax H3 Director",
    "PSV_MiniMaxH3TurboProfile": "Prompt Studio MiniMax H3 Turbo Profile",
    **CONTEXT_NODE_DISPLAY_NAME_MAPPINGS,
}


__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
