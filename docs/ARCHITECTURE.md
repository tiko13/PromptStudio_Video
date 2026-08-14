# Architecture

## Boundary

This repository owns all video-specific prompt contracts, UI, nodes, stores,
workflow adapters, and future standalone pages. `ComfyUI_PromptStudio` should
only contain companion detection, its enable switch, and navigation.

## Authoritative state

The versioned structured document is authoritative:

```text
document
├── mode, duration, and canvas
├── planning-only whole-video synopsis and compiled visual style
├── references and semantic roles
├── shots
│   ├── composition, subjects, environment, and lighting
│   ├── authoritative chronological action/dialogue steps
│   ├── camera motion, amplitude, speed, and target
│   ├── immutable visible text
│   └── synchronized sound events
├── overall soundscape and non-diegetic music
└── REF2VA definitions, summary, and retention analysis
```

The canvas and future LLM patches edit this document. `video/compiler.py`
produces the queue prompt deterministically. `main_description` remains visible
as a planning synopsis and Grand Director input, but the compiler deliberately
omits it; all generated visual content begins in `[Shot 1]`. Alignment
instructions, section ordering, reference labels, dialogue tags, and timestamps
must not be delegated to unrestricted LLM prose.

The standalone studio wraps the prompt document in a project record containing
the user's production brief, selected `[PSV]` workflow, and immutable generation
snapshots. Manual controls and AI Director patches operate on the same
document; there is no separate simplified prompt state.

Completed generations form an immutable lineage. A native continuation is a
new child generation with its own segment-local document, compiled prompt,
workflow snapshot, and raw segment output. Its parent remains unchanged. The
parent lineage's raw segments are losslessly remuxed into a new cumulative MP4,
allowing repeated continuation and branches while every shorter version stays
playable.

Video Studio instruments queued prompts without changing their durable workflow
snapshots. Every render writes a compact 22-frame H3 video-latent tail together
with the aligned audio-latent tail. An extension uses the FL2VA/base model and a
T2VA prompt, places those parent condition latents on the first 22 frames of the
new target timeline, and trims the repeated audiovisual head after decode. The
new raw segment therefore starts immediately after its parent. A saved MP4 is
decoded only as a compatibility fallback for generations created before latent
capture existed. The runtime patch is guarded against incompatible third-party
H3 patches and is owned entirely by this repository.

The standalone right panel is a compact Shots navigator. Detailed shot setup
and the ordered performance sequence live in a transactional popup editor. The
compiler follows the step list exactly, allowing action, dialogue, subsequent
action, and later dialogue to remain causally ordered without artificial
timestamps. Documents without steps receive a one-way import migration that
places the old action first and old dialogue after it, then discards those old
fields.

## AI Directors

The production card opens a project-scoped Grand Director. It receives every
shot, the production brief, global audiovisual fields, compact production
constraints, canonical MiniMax reference tokens, and a bounded recent-message
tail. Its validated change set supports project-field updates, edits across
existing shots, cut-time changes, adding or removing unprotected shots, and the
structured reference definitions and retention analysis required by REF2VA.

The shot popup editor opens a separate shot-scoped Director. It receives
the selected shot, its immediate neighbors, and the same compact constraints.
Its write contract remains limited to descriptive, sound, camera, and complete
chronological `steps` replacements on that selected shot, plus reference-semantic
fields when an attached reference must be activated for that shot. Neither scope receives
the compiled queue prompt or unlimited conversation history.

Conversational answers do not mutate project state. Requested edits use a
video-specific change set whose base-document hash, scope, target IDs, field
allowlists, camera vocabulary, timing, reference coverage, and normalized
result are validated on the server. Reference assets and visible text remain
outside both write scopes. The Director must preserve existing dialogue text and
speaker IDs when replacing a steps sequence unless the user explicitly requests
that protected dialogue change. Reference analysis may describe and activate already-committed
assets but cannot replace them. A project proposal cannot remove a shot that
contains protected dialogue or visible text. The browser applies the validated
document only after explicit user approval and rejects proposals made against
an older document revision.

Director history is stored separately in browser storage and capped. Approved
document changes remain the durable memory that subsequent bounded-context
requests receive.

Assistant turns retain response variants. Regeneration replays the bounded
conversation through the preceding user turn, stores the new answer alongside
the old one, and keeps proposal, proposal error, context usage, and
apply/discard state per variant. Only the selected variant is exposed to the
proposal preview and apply flow.

Director output length defaults to provider-aware automatic mode rather than a
fixed ceiling. KoboldCpp reports its true context length and tokenizes the
request, allowing the client to allocate the remaining window to the response.
Ollama receives its fill-context `num_predict` sentinel. Explicit positive
limits still override automatic behavior.

The Director chat route supports background jobs. The standalone UI starts a
job, polls its status route, and—while a KoboldCpp job is running—uses
`/api/extra/perf` plus `/api/extra/generate/check` to distinguish active prompt
processing or generation from a completed or failed Director task.

The independent LLM status route checks the selected provider through the same
loopback-host validation as Director generation. KoboldCpp status uses
`/api/extra/perf`; Ollama status uses `/api/tags` and verifies the selected
model. The separate KoboldCpp abort route proxies `/api/extra/abort`. The
compact toolbar control polls without acquiring the Director LLM lock, so it
can report both LLM and ComfyUI health or stop a runaway KoboldCpp generation
while the Director job is still blocked.

Sampling is intent-sensitive. Conversational advice uses the configured
temperature, structured proposal requests cap it at `0.2`, and the single
contract-correction retry uses `0.0` to favor exact document enums.

Director image attachments are uploaded to normal ComfyUI input storage and
loaded only through validated paths beneath that directory. A per-image usage
separates visual description from conditioning: `describe` images are current-
turn vision context only, while explicit first-frame, last-frame, subject,
scene, style, pose, camera, and storyboard usages become normal project
references before the request. Each image goes through its own low-temperature
vision-only grounding request, which has no access to the requested video action
or any other attached image. Validated subject candidates, private matching
aliases, and structured grounded appearance attributes are cached on the project
reference. People in first/last-frame anchors join the same stable `<Subject N>`
registry as ordinary subject references. Before the larger Director request,
deterministic code resolves natural identifiers to tokens and removes private
selectors and raw subject observations from model context. Subject definitions
keep the source binding while excluding the source picture's background, scene,
lighting, composition, and camera framing. Grounded attributes are exposed only
for an explicit appearance edit and are not automatically compiled into prompt
prose. Images are never replayed with older chat history.

## Runtime dispatch

`PSV_MiniMaxH3Director` normalizes and compiles the document, loads media from
ComfyUI input storage, and dispatches to the native ComfyUI node matching the
resolved mode:

- base modes: `MiniMaxH3ImageToVideo`;
- full reference: `MiniMaxH3ReferenceToVideo`.

The node returns the selected lazy model alongside native conditioning and
latent outputs, keeping the rest of the workflow conventional and editable.

## Standalone workflow dispatch

The frontend discovers `[PSV]` workflow files through ComfyUI user data, loads
each into an isolated graph, and caches the executable `graphToPrompt` snapshot.
Queueing clones that snapshot and changes only the Director's `document_json`
input plus explicitly requested seed randomization. The queued snapshot is then
stored with the generation so subsequent project edits cannot change an active
render and completed work can be replayed exactly.
