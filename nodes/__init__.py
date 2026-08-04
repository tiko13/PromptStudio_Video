from .minimax_h3_director import PromptStudioMiniMaxH3Director


NODE_CLASS_MAPPINGS = {
    "PSV_MiniMaxH3Director": PromptStudioMiniMaxH3Director,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "PSV_MiniMaxH3Director": "Prompt Studio MiniMax H3 Director",
}


__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
