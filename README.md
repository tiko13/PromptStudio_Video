# PromptStudio_Video

Build MiniMax H3 videos in a visual, shot-based studio that keeps timing, references, dialogue, camera, and sound under your control.

`PromptStudio_Video` is the standalone video companion for [`ComfyUI_PromptStudio`](https://github.com/tiko13/ComfyUI_PromptStudio) and works directly with current native ComfyUI MiniMax nodes.

## Direct the video on a real timeline

Plan multiple shots on a proportional, frame-snapped timeline. Reorder and trim them visually, then edit each shot's setup, camera, sound, visible text, and chronological action or dialogue steps.

<!-- Hero screenshot slot: full Video Studio with preview, timeline, and shot navigator.
Suggested file: docs/images/video-studio-timeline.png
![Video Studio timeline and shot editor](docs/images/video-studio-timeline.png)
-->

## Collaborate with two local AI Directors

Ask the **Grand Director** to critique or compose the whole production, or use the shot-level **Director** for a focused change. Both return reviewable structured proposals; nothing changes until you explicitly apply it.

<!-- Screenshot slot: Grand Director proposal with apply/discard review.
Suggested file: docs/images/grand-director.png
![Grand Director reviewing a multi-shot video](docs/images/grand-director.png)
-->

## Use every MiniMax H3 generation mode from one project

Drop in first and last frames, images, video, or audio, assign clear semantic roles, and let the project route T2VA, I2VA, FL2VA, L2VA, or REF2VA automatically. Reference labels, dialogue, lyrics, and visible text remain explicit and protected.

<!-- Screenshot slot: media library with several references and MiniMax roles.
Suggested file: docs/images/reference-modes.png
![MiniMax reference media and automatic generation modes](docs/images/reference-modes.png)
-->

## Preview exact prompts and replay exact generations

The structured project document remains authoritative while a deterministic compiler builds the guide-compliant MiniMax prompt. Every result keeps its prompt, project state, routing, and executable workflow snapshot for inspection and replay.

Completed renders also expose **Continue video**. Video Studio sends the final
22 frames and their synchronized audio latents into the head of a new MiniMax H3
sample, trims that repeated context after decoding, saves the newly generated
segment independently, and losslessly assembles a new cumulative MP4. Every
generation stores its own compact continuation tail. Parent generations are
never overwritten, so any shorter version can still be played or used as the
start of an alternate continuation. This path is implemented in this repository
and does not require a motion-context or looping node pack.

<!-- Screenshot slot: compiled prompt preview beside generation history and replay.
Suggested file: docs/images/prompt-preview-and-replay.png
![Deterministic prompt preview and video replay history](docs/images/prompt-preview-and-replay.png)
-->

## Move seamlessly from still image to video

With the companion image extension installed, send a Prompt Studio result straight into the active video project and assign it as a frame or reference without exporting and re-importing it manually.

<!-- Screenshot slot: imported Prompt Studio image in the Video Studio media library.
Suggested file: docs/images/image-studio-handoff.png
![Prompt Studio image received by Video Studio](docs/images/image-studio-handoff.png)
-->

This repository is under active development. The sections below cover requirements, the Director document, AI behavior, workflows, integration, and development details.

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

## Current capabilities

The current vertical slice contains:

- `Prompt Studio MiniMax H3 Director`, a ComfyUI custom node;
- automatic T2VA, I2VA, FL2VA, L2VA, and REF2VA routing;
- a deterministic prompt compiler following MiniMax's official base and full-reference formats;
- native ComfyUI MiniMax conditioning and latent creation;
- lazy FL2VA/REF2VA model selection;
- first-frame, last-frame, image, video, and audio reference loading;
- dependency-free native H3 audiovisual motion-context continuation with immutable parent/child lineage, separately viewable extension segments, and cumulative outputs;
- a transactional full-screen Director Canvas with frame-snapped shot trimming, drag-to-reorder editing, media drag-and-drop, camera direction, dialogue, visible text, sound, and media roles;
- a standalone Video Studio with durable projects, authoritative manual shot editing, a docked proportional timeline, frame-snapped trimming, per-shot editors, draggable action/dialogue steps, media roles, `[PSV]` workflow discovery, deterministic prompt preview, queueing, progress, video history, and exact workflow replay;
- context-budgeted Grand Director and selected-shot Director workflows for KoboldCpp or Ollama, with conversational advice, validated structured proposals, stale-document protection, and explicit apply/discard review; and
- a versioned capability and document API for Prompt Studio integration.

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

Each shot owns an authoritative chronological `steps` list. Action and dialogue
steps can be interleaved and are compiled in their exact top-to-bottom order;
dialogue text remains verbatim inside MiniMax `<d>` tags. Normalized documents
contain no parallel action or dialogue mirrors. Older saved documents receive
a one-way conversion to `steps` when loaded. Camera movement is stored as
motion type, amplitude, speed, and target, then compiled into natural English.

Director proposals write performance changes only through `steps`; legacy shot
sequence fields are rejected. Synchronized shot sounds remain separate
from the chronological performance sequence, while the overall soundscape and
non-diegetic music keep their guide-defined roles. **Complete silence**
suppresses dialogue, shot sounds, ambience, and music in the compiled prompt
without deleting the editable document content.

The editable **Production brief** is a planning synopsis for the user and Grand
Director. It is not compiled into the MiniMax prompt. The Grand Director uses it
to infer the useful shot count and translates its visual beats into the
timeline-specific shot fields. It is also available as **Production brief
(planning only)** in the workflow node's Director Canvas.

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

Use **Save & ask Director** in the shot popup editor for the
tighter shot-focused workflow. It receives only the selected shot and immediate
neighbors, and its proposals can update descriptive fields, sound, camera, and
append newly requested dialogue on that selected shot.

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

The compact toolbar status icon covers the selected local LLM (KoboldCpp or
Ollama) and ComfyUI. Green means all services are ready, orange means work is
being processed, and red means a service needs attention. Its panel retains
KoboldCpp's supported abort action when KoboldCpp is selected and includes a
ComfyUI restart action when ComfyUI Manager is available.

Existing dialogue, visible text, and references remain protected in both
scopes. The Director may append newly requested dialogue but cannot rewrite or
remove an existing line or speaker ID. The Grand Director cannot remove a shot
containing protected dialogue or visible text. Every proposal is previewed
against a document hash, normalized, compiled, and applied only after
confirmation.

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

Each image is sent to its own short, deterministic grounding pass for the
current turn, without the requested action or the other images. The pass
extracts subject candidates, private matching aliases, and structured grounded
appearance attributes, which are cached on the project reference. Subjects in
first/last-frame anchors receive stable `<Subject N>` tokens too. Before the
larger Director call, natural identifiers are resolved to those tokens and the
private aliases and raw subject observations are removed from its context.
Subject definitions keep a concise source binding and explicitly exclude the
source picture's background, scene, lighting, composition, and camera framing.
Grounded attributes remain private unless the user explicitly requests an
appearance detail or change; they are never pasted automatically into the
compiled prompt. Older turns retain compact metadata and the Director's
textual answer, avoiding repeated vision tokens. KoboldCpp requires an active
vision model with MMProj and Jinja; Ollama must report the `vision` capability
for the selected model.

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

## Separate MiniMax H3 Turbo workflow

Keep the normal full-step workflow unchanged and save Turbo wiring as a second
`[PSV]` workflow. Connect the Director's selected `model`, `mode`, `width`, and
`height` outputs to `Prompt Studio MiniMax H3 Turbo Profile`. Connect the
profile's outputs as follows:

- `model` to any ordinary content `LoraLoaderModelOnly` chain, then through
  model patches and `MiniMaxH3SigmaShift`; connect that shifted model to both
  the guider and `BasicScheduler`;
- `steps` to `BasicScheduler.steps`;
- `shift_video` and `shift_audio` to `MiniMaxH3SigmaShift`.

The `auto_quality` preset selects the FL2VA 768p four-step profile only for its
exact 1344x768 training canvas, the mixed-resolution FL2VA eight-step profile
for other base-mode canvases, and the task-specific REF2VA four-step profile
for reference generation. `fast_4step` selects the mixed-resolution FL2VA
four-step profile away from 1344x768. T2VA, I2VA, FL2VA, L2VA, and continuation
all use the FL2VA family; REF2VA uses its own LoRA.

Install the Kijai rank-reduced Turbo files under `ComfyUI/models/loras` before
queueing. The node accepts an exact relative path and can also resolve a moved
file by basename when there is only one match. Its default strength is 0.75.
Do not enable `EasyCache` or `SpectrumApplyMiniMaxH3` in the initial Turbo
workflow; their behavior on a four- or eight-step distilled schedule has not
been validated. Add content LoRAs after the Turbo Profile so they remain
independent of inference-mode routing and sampling policy.

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
node --check web\js\promptstudio_video_standalone.js
node --check web\js\promptstudio_video_studio.js
git diff --check
```
