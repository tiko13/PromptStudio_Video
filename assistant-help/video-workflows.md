---
{"id":"video.workflows","topic":"workflows","studio":"video","summary":"Select video workflows and distinguish diffusion models from the shared assistant LLM."}
---
Current project kind: {project_kind}. For standard projects, use the Video workflow selector in the top bar; refresh it with Refresh [PSV] workflows after saving compatible workflows in ComfyUI. In a structured_extension, workflow selection is disabled because the extension uses its source workflow. Current workflow: {workflow}. The selected workflow controls generation model loading; do not invent an image Studio Model sidebar in Video Studio.
The Director's LLM provider/server is shared with Prompt Studio's services. Manage backend/server configuration in Image Studio: open the right sidebar, click its header gear (Prompt Studio settings), then Backend settings. Changing the assistant LLM does not change the video-generation model.
Bundled workflows retain their generation settings but contain no example scene or media references. Define your production in the Director before generating.
