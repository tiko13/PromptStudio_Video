# PromptStudio_Video repository context

This repository is the standalone video companion for `ComfyUI_PromptStudio`.
It is normally installed at:

`C:\EasyDiffusion\ComfyUI\custom_nodes\PromptStudio_Video`

## Unified Prompt Studio product scope

- Treat this repository and the sibling `ComfyUI_PromptStudio` repository as one product and one implementation scope, even though they are technically separate Git repositories.
- For every feature, fix, provider, endpoint, settings change, or contract change, check both repositories. When a Prompt Studio change applies to Video Studio, implement and verify the required Video Studio integration in the same task; do not leave Video Studio with an accidental capability gap.
- `PromptStudio_Video` depends on `ComfyUI_PromptStudio`. Prefer implementing non-video-specific capabilities once in Prompt Studio, then consume them here. In particular, reuse shared provider/LLM endpoint handling, settings, services, utilities, and contracts rather than creating a second implementation.
- Keep video-specific nodes, prompt logic, workflows, routes, UI, and adapters in this repository. Add only the thin integration needed to expose shared Prompt Studio capabilities to Video Studio.
- A change is complete only after checking both repositories for compatibility, integration, and relevant tests. Do not search, edit, initialize, stage, or commit the parent ComfyUI checkout unless the user explicitly requests parent-runtime work.

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
  `node --check web/js/promptstudio_video_standalone.js`
  `node --check web/js/promptstudio_video_studio.js`
- Patch validation:
  `git diff --check`
