# Architecture

## Boundary

This repository owns video-specific prompt contracts, UI, nodes, media handling,
project storage adapters and H3 workflow semantics. `ComfyUI_PromptStudio` owns
shared provider dispatch and scheduling, settings, wire contracts, transactional
storage primitives, asset acquisition, workflow conversion and provenance
utilities. Video consumes those services through thin adapters. The two Git
repositories form one product and are tested together.

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
as a planning synopsis and Video director input, but the compiler deliberately
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
snapshots. Each render captures a compact v3 tail containing exactly **39 frames**
and **65 audio-latent steps**. A continuation uses the FL2VA/base model with a
T2VA prompt. Native nested denoise masks protect the picture prefix and the first
57 audio steps; the last eight audio steps use a half-cosine release. Conflicting
native guides inside the protected prefix are removed. The separately playable
extension trims the repeated head; cumulative assembly uses the full 39-frame
visual blend and gives the incoming segment ownership of overlap audio. Saved
MP4 decoding is a compatibility fallback for older or imported parents. This
uses native H3 latent/mask support and does not require a third-party motion
context node pack or a parent ComfyUI runtime patch.

`CONTINUATION_CONTEXT_FRAMES` and the motion-context default are both 39. Lower
level context utilities retain historical frame-grid options for compatibility,
but the supported Soft AV continuation entry point requires exactly 39 frames.
The capability response advertises `native_h3_soft_av_39`.

Quick continuation authors one new tail shot. Full extension planning uses the
shared cancellable job scheduler, with observable progress, retry and explicit
Apply. Its timeline covers only the delivered new tail; deterministic assembly
adds the 39-frame handoff once and offsets authored cuts and first-shot events.
Applying a successful plan creates one child project; cancellation, failure or
repeated Apply cannot overwrite the parent or create duplicate children.

The standalone right panel is a compact Shots navigator. Detailed shot setup
and the ordered performance sequence live in a transactional popup editor. The
compiler follows the step list exactly, allowing action, dialogue, subsequent
action, and later dialogue to remain causally ordered without artificial
timestamps. Documents without steps receive a one-way import migration that
places the old action first and old dialogue after it, then discards those old
fields.

## AI Directors

The production card opens a project-scoped Video director. It receives every
shot, the production brief, global audiovisual fields, compact production
constraints, canonical MiniMax reference tokens, and a bounded recent-message
tail. Its validated change set supports project-field updates, edits across
existing shots, cut-time changes, adding or removing shots during an explicit
full rewrite, and the
structured reference definitions and retention analysis required by REF2VA.

Update operations are patches by default. An explicit full shot, scene, or
production rewrite is promoted to replacement semantics before validation:
omitted writable fields are reset, every surviving old shot must be replaced,
and a stale manual `prompt_override` is cleared. This prevents deterministic
field merging from carrying obsolete prompt clauses into a rewritten result.

The shot popup editor opens a separate shot-scoped Director. It receives
the selected shot, its immediate neighbors, and the same compact constraints.
Its write contract remains limited to descriptive, sound, camera, and complete
chronological `steps` replacements on that selected shot, plus reference-semantic
fields when an attached reference must be activated for that shot. Neither scope receives
the compiled queue prompt or unlimited conversation history.

Each turn first passes through a constrained, zero-temperature local-LLM router
using Prompt Studio's shared provider dispatch. The router classifies semantic
intent as proposal, discussion, or clarification from bounded conversation and
can resolve context-dependent follow-ups. It also marks strictly reference-only
assignments so their preservation policy does not accidentally capture an
attached-reference motion request. A failed router falls back to automatic mode:
the main Director may answer conversationally, while any proposal it emits is
still validated instead of being silently discarded.

Conversational answers do not mutate project state. Requested edits use a
video-specific change set whose base-document hash, scope, target IDs, field
allowlists, camera vocabulary, timing, reference coverage, and normalized
result are validated on the server. Reference assets cannot be replaced by a
proposal. Existing dialogue, lyrics, visible text and speaker bindings remain
protected unless the current user turn explicitly authorizes the corresponding
content change; protected changes appear in the review. Reference analysis may
describe and activate already-committed assets. The browser applies the validated
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

Sampling is intent-sensitive. The intent-router request uses disabled thinking,
zero temperature, and a small structured response budget. Conversational advice
uses the configured temperature, structured proposal requests cap it at `0.2`,
and contract-correction retries use `0.0` to favor exact document enums.

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
latent outputs, plus the resolved canvas dimensions, keeping the rest of the
workflow conventional and editable.

An optional `PSV_MiniMaxH3TurboProfile` consumes that selected model, resolved
mode, and canvas. It applies exactly one pinned inference LoRA and returns the
model together with the coupled step count and video/audio shifts. Acceleration
LoRAs therefore own inference policy, while ordinary content LoRAs remain a
separate downstream stack. The normal full-step workflow does not require this
node and remains an independent fallback.

## Standalone workflow dispatch

The frontend discovers `[PSV]` workflow files through the shared adapter, loads
each into an isolated graph, and caches the full `workflow`/`output`
`graphToPrompt` envelope. Cache identity includes content and capability hashes
plus adapter, conversion and input-descriptor versions. Image and Video
serialize temporary subgraph registrations and restore the native graph's
registry afterwards. Nested nodes retain their prefixed executable IDs.
Queueing clones that snapshot and changes only the Director's `document_json`
input plus explicitly requested seed randomization. The queued snapshot is then
stored with the generation so subsequent project edits cannot change an active
render and completed executable inputs can be replayed unchanged. This is an
input-replay guarantee, not a guarantee of identical pixels after model, node,
policy or hardware changes. Shared provenance records and drift checks support
that distinction without modifying saved snapshots.

Default workflows are readable JSON under `workflows/`, loaded directly by
`video/default_setup.py`. There is no compressed payload to hand-edit. Native
Director and SaveVideo names/IO remain intact; the Turbo adapter is checked
against the authoritative Python selector. Host-provided dependencies and the
tested Python range are documented in the README. Only Python 3.14 was verified
locally during this audit; broader package installation metadata is not a
minimum-version test result.
