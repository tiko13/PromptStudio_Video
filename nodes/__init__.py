from .minimax_h3_director import PromptStudioMiniMaxH3Director
from .h3_motion_context import (
    NODE_CLASS_MAPPINGS as CONTEXT_NODE_CLASS_MAPPINGS,
    NODE_DISPLAY_NAME_MAPPINGS as CONTEXT_NODE_DISPLAY_NAME_MAPPINGS,
)


NODE_CLASS_MAPPINGS = {
    "PSV_MiniMaxH3Director": PromptStudioMiniMaxH3Director,
    **CONTEXT_NODE_CLASS_MAPPINGS,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "PSV_MiniMaxH3Director": "Prompt Studio MiniMax H3 Director",
    **CONTEXT_NODE_DISPLAY_NAME_MAPPINGS,
}


__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
