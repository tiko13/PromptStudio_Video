---
{"id":"video.workflows","topic":"workflows","studio":"video","summary":"Select video workflows and distinguish diffusion models from the shared assistant LLM."}
---
Project: {project_kind}; workflow: {workflow}. Select Video workflow in the top bar; Refresh [PSV] workflows loads newly saved compatible workflows. A structured_extension locks selection to its source workflow. Workflows choose generation models; Video has no Image Model sidebar.
Manage the Director's shared assistant LLM in Image Studio's sidebar gear (Prompt Studio settings) > Backend settings. This does not change the video-generation model.
Choices persist per project, including unavailable selections. Make default workflow sets the browser default for new projects; otherwise they inherit the current choice. Existing projects keep theirs. Switching refreshes reference controls and the summary while retaining saved references.
Image/Video in the header switches studios. Generate at bottom right queues video; status is to its left.
Switching providers there waits for current Studio LLM work, stops the previous managed Llama.cpp server, or unloads Ollama models used by either Studio. Externally launched servers remain running. Cleanup applies even with Keep models loaded enabled; a cleanup error keeps the previous provider selected.
Both studios preserve backend connections, models and profiles across switches/reloads, including unavailable selections. Inactivity timeout renews during reasoning/output or confirmed server processing; Cancel and token/context limits still apply.
Bundled workflows retain their generation settings but contain no example scene or media references. Define your production in the Director before generating.
Install workflows offers Fast, Balanced, Full quality and optional sparse, TaoMate and FastH3 experiments. Review download size before installing; user workflows and replays are preserved. Sampling Profile help covers policy, mode restrictions and wiring.
Global settings uses compact sections. Image output preferences are shared with Image Studio; forced image-save nodes show an override notice.
