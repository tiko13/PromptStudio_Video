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
├── references and semantic roles
├── shots
│   ├── composition, subjects, environment, lighting, action
│   ├── camera motion, amplitude, speed, and target
│   ├── immutable dialogue and visible text
│   └── synchronized sound events
├── overall soundscape and non-diegetic music
└── REF2VA definitions, summary, and retention analysis
```

The canvas and future LLM patches edit this document. `video/compiler.py`
produces the queue prompt deterministically. Alignment instructions, section
ordering, reference labels, dialogue tags, and timestamps must not be delegated
to unrestricted LLM prose.

## Runtime dispatch

`PSV_MiniMaxH3Director` normalizes and compiles the document, loads media from
ComfyUI input storage, and dispatches to the native ComfyUI node matching the
resolved mode:

- base modes: `MiniMaxH3ImageToVideo`;
- full reference: `MiniMaxH3ReferenceToVideo`.

The node returns the selected lazy model alongside native conditioning and
latent outputs, keeping the rest of the workflow conventional and editable.
