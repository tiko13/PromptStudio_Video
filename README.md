# PromptStudio_Video

`PromptStudio_Video` is the standalone video companion for
[`ComfyUI_PromptStudio`](https://github.com/tiko13/ComfyUI_PromptStudio). It
provides a structured shot, reference, dialogue, camera, and sound editor for
MiniMax H3 without depending on ComfyUI-DaSiWa-Nodes.

This repository is under active development. The first vertical slice contains:

- `Prompt Studio MiniMax H3 Director`, a ComfyUI custom node;
- automatic T2VA, I2VA, FL2VA, L2VA, and REF2VA routing;
- a deterministic prompt compiler following MiniMax's official base and
  full-reference formats;
- native ComfyUI MiniMax conditioning and latent creation;
- lazy FL2VA/REF2VA model selection;
- first-frame, last-frame, image, video, and audio reference loading;
- a transactional full-screen Director Canvas for shots, camera direction,
  dialogue, visible text, sound, and media roles; and
- a versioned capability endpoint for future Prompt Studio integration.

## Requirements

- A current ComfyUI build containing the native MiniMax H3 and video nodes.
- MiniMax H3 model, text encoder, and VAE files.
- KJNodes is recommended for the optimized workflow path. It is not imported by
  the Director itself.

No additional pip package is currently required. ComfyUI supplies PyTorch,
PyAV, torchaudio, Pillow, and its native media loaders.

## Installation

Install the repository as:

```text
ComfyUI/custom_nodes/PromptStudio_Video
```

Restart ComfyUI after installation.

## Director document

The node stores a versioned JSON document rather than treating the compiled
prompt as its source of truth. Its references determine Auto mode:

| Reference roles | Resolved mode |
| --- | --- |
| No references | T2VA |
| One `first_frame` image | I2VA |
| One `first_frame` and one `last_frame` image | FL2VA |
| One `last_frame` image | L2VA |
| Subject/style/scene/video/audio references | REF2VA |

The final prompt is compiled at execution time. Alignment instructions use the
effective MiniMax duration after the frame count is snapped upward to the
model's `17k+5` temporal grid.

Dialogue text is stored separately and emitted verbatim inside MiniMax `<d>`
tags. Camera movement is stored as motion type, amplitude, speed, and target,
then compiled into natural English.

The compiler follows MiniMax's official guides:

- [T2VA / I2VA / FL2VA / L2VA prompt guide](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md)
- [Full-reference prompt guide](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md)

## Minimal workflow

1. Load the MiniMax H3 FL2VA and REF2VA diffusion models.
2. Apply any desired KJ optimization to each model before connecting it.
3. Load the MiniMax CLIP, video VAE, and audio VAE.
4. Connect them to `Prompt Studio MiniMax H3 Director`.
5. Connect the selected `model`, `positive`, and `latent` outputs to the normal
   guider and sampler path.
6. Decode video and audio with their matching VAEs.
7. Use native `CreateVideo` and `SaveVideo` for output.
8. Save the workflow with a `[PSV]` filename prefix for future Video Studio
   discovery.

The Director requests only the active diffusion-model input through ComfyUI's
lazy-input mechanism.

## Capability endpoint

Prompt Studio can detect the companion at:

```text
GET /promptstudio-video/capabilities
```

The current response advertises API version 1, prompt-document version 1, the
`[PSV]` prefix, and the MiniMax H3 adapter. The standalone Video Studio page
will be advertised after its queue/session shell is implemented.

## Development checks

```powershell
C:\EasyDiffusion\ComfyUI\venv\Scripts\python.exe -m unittest discover -s tests -v
node --check web\js\promptstudio_video_director.js
git diff --check
```
