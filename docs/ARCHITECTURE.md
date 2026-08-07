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
│   ├── compatibility action/dialogue mirrors and immutable visible text
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

The standalone right panel is a compact Shots navigator. Detailed shot setup
and the ordered performance sequence live in a transactional popup editor. The
compiler follows the step list exactly, allowing action, dialogue, subsequent
action, and later dialogue to remain causally ordered without artificial
timestamps. Documents without steps migrate deterministically by placing the
legacy action first and existing dialogue after it.

## AI Directors

The production card opens a project-scoped Grand Director. It receives every
shot, the production brief, global audiovisual fields, compact production
constraints, canonical MiniMax reference tokens, and a bounded recent-message
tail. Its validated change set supports project-field updates, edits across
existing shots, cut-time changes, adding or removing unprotected shots, and the
structured reference definitions and retention analysis required by REF2VA.

The shot popup editor opens a separate shot-scoped Director. It receives
the selected shot, its immediate neighbors, and the same compact constraints.
Its write contract remains limited to descriptive, sound, camera, and new
dialogue additions on that selected shot, plus reference-semantic fields when
an attached reference must be activated for that shot. Neither scope receives
the compiled queue prompt or unlimited conversation history.

Conversational answers do not mutate project state. Requested edits use a
video-specific change set whose base-document hash, scope, target IDs, field
allowlists, camera vocabulary, timing, reference coverage, and normalized
result are validated on the server. Reference assets and visible text remain
outside both write scopes. Dialogue proposals are append-only: the Director may
add newly requested lines but cannot rewrite or remove existing dialogue or
speaker IDs. Reference analysis may describe and activate already-committed
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

Independent Kobold status and abort routes proxy `/api/extra/perf` and
`/api/extra/abort` through the same loopback-host validation as Director
generation. The compact toolbar control polls without acquiring the Director
LLM lock, so it can stop a runaway generation while the Director job is still
blocked on KoboldCpp.

Sampling is intent-sensitive. Conversational advice uses the configured
temperature, structured proposal requests cap it at `0.2`, and the single
contract-correction retry uses `0.0` to favor exact document enums.

Director image attachments are uploaded to normal ComfyUI input storage and
loaded only through validated paths beneath that directory. A per-image usage
separates visual description from conditioning: `describe` images are current-
turn vision context only, while explicit first-frame, last-frame, subject,
scene, style, pose, camera, and storyboard usages become normal project
references before the request. Image bytes go first through a low-temperature,
vision-only grounding request that has no access to the requested video action.
Its validated observations are inserted into the production context as
authoritative visual facts, and the larger structured Director request receives
those facts without the image bytes. This keeps schema examples and story text
from contaminating pixel observations. Images are never replayed with older chat
history.

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
