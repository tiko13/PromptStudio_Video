# PromptStudio_Video

`PromptStudio_Video` is the standalone video companion for
[`ComfyUI_PromptStudio`](https://github.com/tiko13/ComfyUI_PromptStudio). It
provides a structured shot, reference, dialogue, camera, and sound editor for
MiniMax H3 without depending on ComfyUI-DaSiWa-Nodes.

This repository is under active development. The current vertical slice contains:

- `Prompt Studio MiniMax H3 Director`, a ComfyUI custom node;
- automatic T2VA, I2VA, FL2VA, L2VA, and REF2VA routing;
- a deterministic prompt compiler following MiniMax's official base and
  full-reference formats;
- native ComfyUI MiniMax conditioning and latent creation;
- lazy FL2VA/REF2VA model selection;
- first-frame, last-frame, image, video, and audio reference loading;
- a transactional full-screen Director Canvas with frame-snapped shot trimming,
  drag-to-reorder editing, media drag-and-drop, camera direction, dialogue,
  visible text, sound, and media roles; and
- a standalone Video Studio with durable projects, authoritative manual shot
  editing, a docked proportional timeline with pointer reordering, frame-snapped
  trimming, zoom and fit controls, screen-wide media drop, draggable reference
  ordering, explicit MiniMax media roles, `[PSV]` workflow discovery,
  deterministic prompt preview, ComfyUI queueing, progress, video history, and
  exact workflow replay;
- a context-budgeted selected-shot AI Director for KoboldCpp or Ollama, with
  conversational advice, validated structured proposals, stale-document
  protection, and explicit apply/discard review; and
- a versioned capability and document API for Prompt Studio integration.

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

Video Studio records the native dimensions of imported images and videos and
offers each aspect ratio in the Canvas selector. First-frame, last-frame, video
editing, and video continuation roles can link the canvas to their source
geometry. The linked ratio is resized to a configurable target megapixel value
(about 1.03 MP by default, matching `768 × 1344`) and both axes are rounded to
MiniMax H3's required multiple of 32.

Dialogue text is stored separately and emitted verbatim inside MiniMax `<d>`
tags. Camera movement is stored as motion type, amplitude, speed, and target,
then compiled into natural English.

The editable **Production brief** is the main whole-video description. It is
compiled before the timeline-specific shot details and is also available as
**Main video description** in the workflow node's Director Canvas.

Reference and shot identifiers use MiniMax's guide-exact grammar:
`<Picture 1>`, `<Video 1>`, `<Audio 1>`, `<Subject 1>`, and `[Shot 1]`. Legacy
compact forms are normalized outside dialogue and visible text, which remain
verbatim.

## AI Directors

Use **Ask Grand Director** in the production card for whole-video critique and
on-demand multi-shot composition. The Grand Director receives every shot plus
the production brief, global audiovisual fields, references, and MiniMax
constraints. Approved proposals may update project-level style and sound,
reference definitions and retention analysis, revise any shot, change cut
times, or add and remove unprotected shots. REF2VA proposals must represent
every source reference, use every defined label in the summary and applicable
shots, and populate all six full-reference sections before generation.

Use **Ask Director about this shot** in the selected-shot inspector for the
tighter shot-focused workflow. It receives only the selected shot and immediate
neighbors, and its proposals can update only descriptive fields, sound, and
camera on that selected shot.

Both buttons open the local LLM consultation panel with separate conversation
histories. Connection settings default to the companion
image Prompt Studio settings when available, but Video owns its KoboldCpp and
Ollama clients and works without importing the image plugin.

Advice-only turns keep the normal conversational sampling temperature. Edit
requests that require a structured proposal cap temperature at `0.2` so camera
types and the document's other enums remain stable; an automatic contract-
correction attempt uses `0.0`.

The latest Director answer has the same response navigation as image Prompt
Studio: move left and right through saved alternatives, or use the right arrow
on the newest answer to regenerate the same user turn. Each alternative keeps
its own validated proposal and apply/discard state, and failed answers remain
regenerable.

The default 8,000-character context budget contains compact production data,
canonical reference labels, and at most ten recent conversation messages. The
Grand Director never silently drops shots: increase the context budget if a
large production does not fit. KoboldCpp's reported token count and context
length further cap the response when available. Chat history is retained
locally but approved document state is the authoritative long-term memory.

The response-token setting defaults to `0` (automatic). KoboldCpp uses all
context tokens remaining after the prompt and safety margin; Ollama uses its
fill-context generation mode. A positive value remains available when a hard
output ceiling is desired. The former stored `700`-token default migrates to
automatic mode.

Director requests run as background jobs so the browser does not mistake a
long generation for a failed HTTP request. While KoboldCpp is active, Video
Studio polls its supported generation-check endpoint and reports whether it is
processing the prompt or how many response characters are available. The
request timeout defaults to 600 seconds and can be adjusted in Director
settings.

The compact Kobold status control in the Video Studio toolbar remains available
outside Director dialogs. It reports idle, busy, or unreachable state and can
send KoboldCpp's supported abort signal to stop a runaway text generation
without unloading the model or restarting KoboldCpp.

Dialogue, visible text, and references remain protected in both scopes. The
Grand Director cannot remove a shot containing protected dialogue or visible
text. Every proposal is previewed against a document hash, normalized,
compiled, and applied only after confirmation.

Explicit requests to create, compose, generate, or apply the full prompt require
a structured proposal. If a local model returns only prose, Video Studio makes
one automatic correction attempt with a larger response allowance; if that also
omits the change set, the response reports the omission instead of implying it
can be applied.

Up to four images may be dragged into a Director turn or selected with **Add
image**. Each attachment has an explicit usage:

- **Describe only** sends the image to the vision model for visible-detail
  extraction without adding it to MiniMax conditioning.
- First frame, last frame, subject, scene, style, pose, camera, and storyboard
  usages add the uploaded image to project references with that semantic role
  and expose its canonical `<Picture N>` token to the Director.

Image bytes are sent only with the current user turn. Older turns retain compact
attachment metadata and the Director's textual answer, avoiding repeated vision
tokens. KoboldCpp requires an active vision model with MMProj and Jinja; Ollama
must report the `vision` capability for the selected model.

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
`[PSV]` prefix, the MiniMax H3 adapter, and the standalone Video Studio page.

Open the standalone page at:

```text
http://127.0.0.1:8188/extensions/PromptStudio_Video/prompt_studio_video.html
```

Video Studio discovers saved ComfyUI workflows whose filenames begin with
`[PSV]`. A compatible workflow currently needs exactly one executable
`PSV_MiniMaxH3Director` and one native `SaveVideo` output.

Projects and cached workflow snapshots are stored transactionally in ignored
runtime files inside this repository. Every queued generation stores its
normalized document, compiled prompt, effective duration, output routing, and
complete executable workflow snapshot for exact replay.

## Development checks

```powershell
C:\EasyDiffusion\ComfyUI\venv\Scripts\python.exe -m unittest discover -s tests -v
node --check web\js\promptstudio_video_director.js
node --check web\js\promptstudio_video_standalone.js
node --check web\js\promptstudio_video_studio.js
git diff --check
```
