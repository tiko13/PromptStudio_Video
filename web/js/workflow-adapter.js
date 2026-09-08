import { createWorkflowAdapter } from "/extensions/ComfyUI_PromptStudio/js/prompt-studio/generation/workflow-adapter.js";
export { discoverWorkflowFiles, workflowResultOutputs } from "/extensions/ComfyUI_PromptStudio/js/prompt-studio/generation/workflow-adapter.js";

export const TURBO_POLICY_VERSION = 1;

export function createVideoWorkflowTemplateBuilder({ app }) {
  return createWorkflowAdapter({
    app, adapterId: "minimax_h3", capabilities: { outputRuleVersion: 1, turboPolicyVersion: TURBO_POLICY_VERSION, resultFields: ["videos", "gifs", "images"] },
    build: ({ file, snapshot, nodes, additionalInputs }) => {
      const directors = nodes.filter(record => record.type === "PSV_MiniMaxH3Director");
      if (directors.length !== 1) throw new Error(`Workflow needs exactly one executable Prompt Studio MiniMax H3 Director; found ${directors.length}.`);
      const results = nodes.filter(record => record.type === "SaveVideo");
      if (results.length !== 1) throw new Error(`Workflow needs exactly one executable native Save Video node; found ${results.length}.`);
      return {
        id: file.path, path: file.path,
        name: String(file.path).replaceAll("\\", "/").split("/").at(-1).replace(/\.json$/i, ""),
        adapter: "minimax_h3", director_node_id: directors[0].id,
        result_node_ids: [results[0].id], result_fields: ["videos", "gifs", "images"],
        additionalInputs, snapshot, source_modified: Number(file.modified || 0),
        updated_at: Date.now(), stale: false, error: "",
      };
    },
  });
}

/** Mirrors the authoritative Python selection; cross-language fixtures guard parity. */
export function selectTurboProfile(mode, width, height, preset = "auto_quality") {
  mode = String(mode || "").trim().toLowerCase();
  preset = String(preset || "").trim().toLowerCase();
  const dimension = value => {
    if (typeof value === "string" && !/^[+-]?\d+$/.test(value.trim())) throw new Error("Invalid Turbo dimension");
    const result = Math.trunc(Number(value));
    if (value == null || !Number.isFinite(result) || result <= 0) throw new Error("MiniMax H3 Turbo width and height must be positive");
    return result;
  };
  width = dimension(width); height = dimension(height);
  if (!["t2va", "i2va", "fl2va", "l2va", "ref2va"].includes(mode)) throw new Error(`Unsupported MiniMax H3 Turbo mode '${mode}'`);
  if (!["auto_quality", "fast_4step"].includes(preset)) throw new Error(`Unsupported MiniMax H3 Turbo preset '${preset}'`);
  let profile_id, lora_input, steps, shift_video;
  if (mode === "ref2va") [profile_id, lora_input, steps, shift_video] = ["ref2va_4step_v0.1", "ref2va_4step_lora", 4, 12];
  else if (width === 1344 && height === 768) [profile_id, lora_input, steps, shift_video] = ["fl2va_768p_4step_v1.0", "fl2va_768p_4step_lora", 4, 6];
  else if (preset === "fast_4step") [profile_id, lora_input, steps, shift_video] = ["fl2va_mixed_4step_v0.1", "fl2va_mixed_4step_lora", 4, 12];
  else [profile_id, lora_input, steps, shift_video] = ["fl2va_mixed_8step_v1.0", "fl2va_mixed_8step_lora", 8, 12];
  return { profile_id, lora_input, steps, shift_video, shift_audio: 3 };
}

export function turboDisplayProfile(mode, width, height, preset) {
  const profile = selectTurboProfile(mode, width, height, preset);
  const label = profile.profile_id === "ref2va_4step_v0.1" ? "REF2V v0.1"
    : profile.profile_id === "fl2va_768p_4step_v1.0" ? "FL2V 768p v1.0"
      : profile.steps === 4 ? "FL2V mixed v0.1" : "FL2V mixed v1.0";
  return { label, input: profile.lora_input, steps: profile.steps, shiftVideo: profile.shift_video, shiftAudio: profile.shift_audio };
}
