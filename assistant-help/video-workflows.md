---
{"id":"video.workflows","topic":"workflows","studio":"video","summary":"Select video workflows and distinguish diffusion models from the shared assistant LLM."}
---
Project: {project_kind}; workflow: {workflow}. Select Video workflow in the top bar; Refresh [PSV] workflows loads newly saved compatible workflows. A structured_extension locks selection to its source workflow. Workflows choose generation models; Video has no Image Model sidebar.
Manage the Director's shared assistant LLM in Image Studio's sidebar gear (Prompt Studio settings) > Backend settings. This does not change the video-generation model.
Choices persist per project, even when unavailable. Make default workflow saves a browser default for new projects, marked Default in the dropdown. Otherwise new projects inherit the current choice. Existing projects keep theirs. Switching refreshes references and the generation summary; saved references return on switching back.
Use Image/Video in the standalone header to switch studios, including after refreshing the page.
Generate at the bottom right queues the video; status and summary appear to its left.
Switching providers there waits for current Studio LLM work, stops the previous managed Llama.cpp server, or unloads Ollama models used by either Studio. Externally launched servers remain running. Cleanup applies even with Keep models loaded enabled; a cleanup error keeps the previous provider selected.
Both studios preserve backend connections, models and profiles across switches/reloads, including unavailable selections. Inactivity timeout renews during reasoning/output or confirmed server processing; Cancel and token/context limits still apply.
Bundled workflows retain their generation settings but contain no example scene or media references. Define your production in the Director before generating.
Install workflows opens a separate installer for versioned Fast, Balanced and Full quality bundles plus optional sparse, TaoMate and FastH3 experiments. Review the missing download size, then install; existing user workflows and replay snapshots are preserved. The sampling-profile display describes the selected graph's policy. See the Sampling Profile node help for mode restrictions and wiring.
