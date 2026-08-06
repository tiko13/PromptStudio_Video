# PromptStudio_Video repository context

This repository is the standalone video companion for `ComfyUI_PromptStudio`.
It is normally installed at:

`C:\EasyDiffusion\ComfyUI\custom_nodes\PromptStudio_Video`

Keep video-specific nodes, prompt contracts, workflows, routes, and frontend code
in this repository. The image-oriented `ComfyUI_PromptStudio` repository should
only receive capability detection, settings, and navigation integration.

## Runtime boundaries

- Prefer current native ComfyUI MiniMax H3 and video nodes.
- KJNodes may be used for optional optimization paths.
- Do not depend on ComfyUI-DaSiWa-Nodes, rgthree, or ComfyUI-GGUF.
- Run Python commands through
  `C:\EasyDiffusion\ComfyUI\venv\Scripts\python.exe`.
- Python changes require a ComfyUI restart; frontend-only changes require refresh.
- Keep the structured video document authoritative. Compile the final MiniMax
  prompt deterministically from that document.
- Preserve dialogue, lyrics, and visible text verbatim.

## Authoritative prompting guide

- The MiniMax H3 video prompting guide is:
  `https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md`
- Always read and adhere to this guide when designing or changing prompt
  building, compilation, generation, refinement, editor fields, examples,
  placeholders, or any other prompt-related behavior.
- Treat the guide as the source of truth for task modes, prompt structure,
  timelines and cuts, camera motion, dialogue and voiceover, visible text,
  soundscape, music, and reference-frame alignment.

## Checks

- Python syntax:
  `C:\EasyDiffusion\ComfyUI\venv\Scripts\python.exe -m compileall -q .`
- Tests:
  `C:\EasyDiffusion\ComfyUI\venv\Scripts\python.exe -m unittest discover -s tests -v`
- Frontend syntax:
  `node --check web/js/promptstudio_video_director.js`
- Patch validation:
  `git diff --check`
