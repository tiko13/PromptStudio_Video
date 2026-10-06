---
{"id":"video.nodes","topic":"video-nodes","studio":"video","summary":"List all Video companion nodes and what they do: MiniMax H3 Director, Turbo Profile, Motion Context, Save H3 Context and Trim H3 Context. Also select nodes for the shared Prompt Studio inventory."}
---
The Video companion adds these nodes in Prompt Studio/Video:
- Prompt Studio MiniMax H3 Director (PSV_MiniMaxH3Director): compiles the structured document, selects the active model, builds native conditioning and latents.
- Prompt Studio MiniMax H3 Turbo Profile (PSV_MiniMaxH3TurboProfile): applies a task/canvas-specific acceleration LoRA with coupled sampling settings.
- Prompt Studio MiniMax H3 RefMod / RefLoRA Loader (PSV_MiniMaxH3ReferenceAdapters): applies saved reference conditioning and optional RefLoRA weights.
- Prompt Studio MiniMax H3 Sampling Profile (PSV_MiniMaxH3SamplingProfile): selects versioned Fast, Balanced, Full quality or experimental sampling settings and attention.
- Prompt Studio H3 Motion Context (PSV_H3MotionContext): injects a previous segment's video/audio context into an extension's conditioning and latent.
- Prompt Studio Save H3 Context (PSV_H3SaveContext): stores a compact sampled latent tail for continuation.
- Prompt Studio Trim H3 Context (PSV_H3TrimContext): removes repeated context from decoded media and preserves overlap-bearing assembly outputs.
Prompt Studio Input and Reference Image come from the shared Prompt Studio suite, not this companion. Ask for the shared node catalog too when listing all product nodes. Native ComfyUI model loaders, samplers, CreateVideo and SaveVideo are graph dependencies, not companion nodes. Ask about a named node for its inputs, outputs and wiring; retrieve its detail topic for follow-ups. Video Studio requires one executable Director and one native SaveVideo, not an Image Studio Prompt Slot.
