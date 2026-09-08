import { downloadJobDiagnostics, fetchJobActivity, jobActivityText, jobRetryText, recoveredJobError } from "/extensions/ComfyUI_PromptStudio/js/prompt-studio/ui/job-diagnostics.js";
import { createVideoWorkflowTemplateBuilder, turboDisplayProfile, discoverWorkflowFiles, workflowResultOutputs } from "./workflow-adapter.js";
import { app } from "/scripts/app.js";
import {createPollingScope} from "/extensions/ComfyUI_PromptStudio/js/prompt-studio/ui/polling.js";
import {readSharedHealth} from "/extensions/ComfyUI_PromptStudio/js/prompt-studio/ui/shared-health.js";
import { reconcileKeyedHistory } from "/extensions/ComfyUI_PromptStudio/js/prompt-studio/ui/keyed-history.js";
import { createResultComparison, videoComparisonRecord } from "/extensions/ComfyUI_PromptStudio/js/prompt-studio/ui/result-comparison.js";
import { captureRuntimeProvenance, reviewReplay } from "/extensions/ComfyUI_PromptStudio/js/prompt-studio/ui/replay-review.js";
import { createDraftOutbox, createDraftScheduler, draftTabKey, showDraftStorageFailure } from "/extensions/ComfyUI_PromptStudio/js/prompt-studio/chat/draft-outbox.js";
import { createFeatureController, movePanelPreservingFocus } from "/extensions/ComfyUI_PromptStudio/js/prompt-studio/ui/feature-controller.js";
import { createVideoGenerationProgressController } from "./controllers/generation-progress-controller.js";
import { createVideoDocumentInteractionController } from "./controllers/document-interaction-controller.js";
import { requireHistoryIndex, prepareHistoryIndex } from "/extensions/ComfyUI_PromptStudio/js/prompt-studio/ui/history-maintenance.js";
import { installDialogFocus } from "/extensions/ComfyUI_PromptStudio/js/prompt-studio/ui/dialog-focus.js";
import { api } from "/scripts/api.js";
import { loadLlmProfiles } from "/extensions/ComfyUI_PromptStudio/js/prompt-studio/settings/llm-profile-store.js";
import { normalizeLlmProvider as sharedLlmProvider, normalizeProviderSettings, normalizeJobWire, assertObservedJobTransition } from "/extensions/ComfyUI_PromptStudio/js/prompt-studio/core/wire-contracts.js";
import {
  PROMPT_STUDIO_INPUT_PROFILE_VERSION,
  applyPromptStudioInputValues,
  extractPromptStudioInputs,
  normalizePromptStudioInputDescriptors,
  normalizePromptStudioInputSelections,
  promptStudioInputSelectionKey,
  promptStudioInputValue,
  selectedPromptStudioInputValue,
} from "/extensions/ComfyUI_PromptStudio/js/prompt-studio/generation/prompt-studio-input.js";

const EXTENSION_NAME = "PromptStudio.Video.Standalone";
const videoDraftOutbox = createDraftOutbox();
let videoDraftPending = Promise.resolve(true);
const projectDraftScheduler = createDraftScheduler(writeProjectDraft);
window.addEventListener("pagehide", () => projectDraftScheduler.flush());
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") projectDraftScheduler.flush();
});
const CHANNEL_NAME = "promptstudio.video.standalone.v1";
const DIRECTOR_TYPE = "PSV_MiniMaxH3Director";
const TURBO_PROFILE_TYPE = "PSV_MiniMaxH3TurboProfile";
const WORKFLOW_PREFIX = "[PSV]";
const PROJECTS_ENDPOINT = "/promptstudio-video/projects";
const WORKFLOWS_ENDPOINT = "/promptstudio-video/workflows";
const DEFAULT_WORKFLOWS_ENDPOINT = "/promptstudio-video/default-workflows";
const DEFAULT_SETUP_ENDPOINT = "/promptstudio-video/default-setup";
const DIRECTOR_CHAT_ENDPOINT = "/promptstudio-video/director/chat";
const DIRECTOR_PREVIEW_ENDPOINT = "/promptstudio-video/director/preview";
const CONTINUATION_PREPARE_ENDPOINT = "/promptstudio-video/continuations/prepare";
const CONTINUATION_PLAN_ENDPOINT = "/promptstudio-video/continuations/plan";
const CONTINUATION_PLANS_KEY = "promptstudio.video.continuation.plans.v1";
const CONTINUATION_ASSEMBLE_ENDPOINT = "/promptstudio-video/continuations/assemble";
const EXACT_AUDIO_MIX_ENDPOINT = "/promptstudio-video/audio-mix";
const AUDIO_PROBE_ENDPOINT = "/promptstudio-video/media/audio-probe";
const LLM_STATUS_ENDPOINT = "/promptstudio/prompt-studio/llm/status";
const LLM_ABORT_ENDPOINT = "/promptstudio-video/llm/abort";
const COMFY_RESTART_ENDPOINTS = ["/v2/manager/reboot", "/manager/reboot"];
const DIRECTOR_JOB_POLL_MS = 1000;
const DIRECTOR_STATUS_RETRY_LIMIT = 3;
const KOBOLD_STATUS_POLL_MS = 3000;
const DISCONNECTED_CONTROL_SELECTOR = "input, textarea, select, button";
const DISCONNECTED_CONTROL_SCOPE_SELECTOR = ".psvstudio-app, dialog[class*='psvstudio-']";
const DIRECTOR_SETTINGS_KEY = "promptstudio.video.director.settings.v1";
const DIRECTOR_SESSIONS_KEY = "promptstudio.video.director.sessions.v1";
const IMAGE_STUDIO_SETTINGS_KEY = "promptstudio.promptStudio.settings.v1";
const IMAGE_CONSULT_SETTINGS_KEY = "promptstudio.promptStudio.consult.settings.v1";
const CANVAS_MEDIA_ROLES = new Set(["first_frame", "last_frame", "video_edit", "video_continue"]);
const DIRECTOR_MAX_IMAGES = 4;
const DIRECTOR_IMAGE_USAGES = Object.freeze([
  { value: "describe", label: "Describe only · no reference" },
  { value: "first_frame", label: "Use as first frame" },
  { value: "last_frame", label: "Use as last frame" },
  { value: "subject", label: "Subject / identity reference" },
  { value: "scene", label: "Scene / environment reference" },
  { value: "style", label: "Visual style reference" },
  { value: "pose", label: "Pose reference" },
  { value: "camera", label: "Camera / composition reference" },
  { value: "storyboard", label: "Storyboard reference" },
]);
const CONTINUATION_CONTEXT_FRAMES = 39;
const STUDIO_INSTANCE_ID = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
const VIDEO_ICON_URL = new URL("../prompt-studio-video-icon.svg", import.meta.url).href;

const PLACEHOLDERS = Object.freeze({
  projectTitle: "Last Train Letter",
  brief: "Live-action, cinematic: a young woman on a rain-soaked train unfolds a letter, looks toward the passing city lights, and whispers a farewell.",
  durationSeconds: "8",
  directorCommand: "Split this into two shots at 00:04.000, add a slow push-in toward the letter, and keep the dialogue verbatim.",
  cutTime: "3.5",
  action: "She lifts her gaze from the folded letter, watches the city lights pass, then folds the paper along its existing crease.",
  subjects: "A young woman in a navy coat sits beside the window, holding a folded letter in both hands.",
  environment: "Inside a nearly empty commuter train at night; rain streaks the window and blurred city lights pass outside.",
  composition: "A medium-wide shot frames the woman in profile on the right, with the rain-covered window filling the left side.",
  lighting: "Cool blue light from the window outlines her face, balanced by warm carriage lights overhead.",
  transition: "the camera cuts to a close-up of the folded letter",
  cameraTarget: "the folded letter in her hands",
  speaker: "The young woman with a quiet, breathy voice",
  speakerId: "S1",
  language: "English",
  dialogue: "I get off at the next station.",
  delivery: "in a quiet, breathy voice at a restrained pace",
  visibleText: "Next stop: Central Station",
  sounds: "Rain ticks against the window\nPaper rustles softly in her hands",
  referenceSummary: "The target video uses <Subject 1> consistently throughout [Shot 1].",
  subjectDefinition: "is the person sourced from <Picture 1>, preserving their concrete visible appearance and clothing.",
  retentionWhere: "appears in [Shot 1]",
  retentionDetail: "The defined appearance and assigned role remain consistent.",
  style: "Live-action, cinematic",
  soundscape: "The train wheels produce a steady metallic rhythm beneath a low ventilation hum. Rain ticks against the window while paper rustles softly.",
  music: "Sparse piano notes at a slow tempo, joined by sustained low strings that gradually decrease in volume.",
});

const state = {
  panel: null,
  popup: null,
  bridgeChannel: null,
  bridgePresenceTimer: null,
  serverPresenceRequest: null,
  serverPresenceQueued: false,
  latestServerPresence: null,
  lastPresenceSignature: "",
  relayedHandoffs: new Set(),
  studioOpenedAt: 0,
  standaloneAttached: false,
  standaloneVisible: false,
  config: null,
  projects: [],
  activeProjectId: null,
  projectRevision: 0,
  projectMutation: 0,
  projectSavedMutation: 0,
  projectSaveTimer: null,
  projectSaveChain: Promise.resolve(),
  projectBase: [],
  projectConflicts: [],
  projectDraftKey: "",
  projectDraftError: "",
  workflows: [],
  workflowRevision: 0,
  defaultWorkflowSetupPrompted: false,
  defaultSetupJobId: "",
  selectedShotId: null,
  generationProgress: new Map(),
  pendingGenerationProgress: new Map(),
  activeGenerationPromptId: "",
  generationPollers: new Map(),
  generationControllers: new Map(),
  generationActivity: new Map(),
  generationFailures: new Map(),
  loopingGenerations: new Set(),
  promptWorkerSeenAlive: false,
  promptWorkerHealthCheckedAt: 0,
  promptWorkerHealthRequest: null,
  apiConnected: true,
  disconnectedGenerationTimer: null,
  disconnectedControls: new Map(),
  disconnectedControlObserver: null,
  timelineZoom: 80,
  shotDrag: null,
  shotStepDragId: "",
  shotEditorDialog: null,
  shotEditorDraft: null,
  shotEditorOriginal: null,
  shotEditorShotId: "",
  shotEditorExpandedStepIds: new Set(),
  shotEditorRevealStepId: "",
  shotTimelineSelectionId: "",
  mediaDragId: "",
  mediaDropDocuments: new WeakSet(),
  mediaDropDepth: new WeakMap(),
  transientUiDocuments: new WeakSet(),
  mediaDimensionLoads: new Set(),
  mediaDurationLoads: new Set(),
  drawer: "",
  directorDialog: null,
  directorBusy: false,
  directorPendingText: "",
  directorSessions: null,
  directorJobs: new Map(),
  directorScope: "shot",
  koboldStatusRequest: null,
  koboldStatusTimer: null,
  llmStatusSnapshot: null,
  comfyQueueRemaining: 0,
  comfyRestartBusy: false,
  koboldAbortBusy: false,
  ready: false,
};

function clone(value) {
  return structuredClone(value);
}

function makeId(prefix) {
  const value = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `${prefix}-${value}`;
}

function el(tag, className = "", text = "") {
  const element = document.createElement(tag);
  if (tag === "dialog") installDialogFocus(element);
  if (className) element.className = className;
  if (text) element.textContent = text;
  return element;
}

function button(text, handler, className = "psvstudio-button") {
  const control = el("button", className, text);
  control.type = "button";
  control.addEventListener("click", handler);
  return control;
}

function field(label, control, help = "") {
  const wrapper = el("label", "psvstudio-field");
  wrapper.append(el("span", "", label), control);
  if (help) wrapper.append(el("small", "psvstudio-help", help));
  return wrapper;
}

function checkControl(label, checked, onChange) {
  const wrapper = el("label", "psvstudio-inline psvstudio-help");
  const control = document.createElement("input");
  control.type = "checkbox";
  control.checked = Boolean(checked);
  control.addEventListener("change", () => onChange(control.checked, control));
  wrapper.append(control, document.createTextNode(label));
  return wrapper;
}

function textInput(value, onInput, type = "text", placeholder = "") {
  const control = document.createElement("input");
  control.type = type;
  control.value = value ?? "";
  control.placeholder = placeholder;
  control.addEventListener("input", () => onInput(type === "number" ? Number(control.value) : control.value, control));
  return control;
}

function textArea(value, onInput, rows = 3, placeholder = "") {
  const control = document.createElement("textarea");
  control.rows = rows;
  control.value = value ?? "";
  control.placeholder = placeholder;
  control.addEventListener("input", () => onInput(control.value, control));
  return control;
}

function selectInput(options, value, onChange) {
  const control = document.createElement("select");
  for (const item of options) {
    const option = document.createElement("option");
    if (typeof item === "object") {
      option.value = item.value;
      option.textContent = item.label;
    } else {
      option.value = item;
      option.textContent = item || "None";
    }
    control.append(option);
  }
  control.value = value ?? "";
  control.addEventListener("change", () => onChange(control.value, control));
  return control;
}

function storedObject(key) {
  try {
    const value = JSON.parse(localStorage.getItem(key) || "{}");
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  } catch (_) {
    return {};
  }
}

function primaryLlmProfile(studioSettings) {
  const profiles = loadLlmProfiles();
  const selected = String(studioSettings?.llm_profile || "");
  return profiles.find(profile => String(profile?.id || "") === selected) || {};
}

function directorSettings() {
  const studio = storedObject(IMAGE_STUDIO_SETTINGS_KEY);
  const consult = storedObject(IMAGE_CONSULT_SETTINGS_KEY);
  const video = storedObject(DIRECTOR_SETTINGS_KEY);
  const provider = normalizeLlmProvider(studio.llm_provider || video.llm_provider || "koboldcpp");
  const configuredLlamacpp = studio.llamacpp_generation_settings
    && typeof studio.llamacpp_generation_settings === "object"
    && !Array.isArray(studio.llamacpp_generation_settings)
    ? studio.llamacpp_generation_settings
    : null;
  const profile = provider === "llamacpp" && configuredLlamacpp
    ? configuredLlamacpp
    : primaryLlmProfile(studio);
  const thinkingMode = profile.thinking_mode || studio.thinking_mode || video.thinking_mode || consult.thinking_mode || "Disabled";
  const thinkingEnabled = thinkingModeEnablesReasoning(thinkingMode);
  const profileValue = (standardKey, thinkingKey, fallback) => Number(
    (thinkingEnabled ? profile[thinkingKey] : profile[standardKey])
      ?? profile[standardKey]
      ?? video[standardKey]
      ?? consult[standardKey]
      ?? fallback,
  );
  const storedResponseTokens = Number(video.max_response_tokens);
  const storedTimeout = Number(video.request_timeout);
  const responseTokens = !Object.hasOwn(video, "max_response_tokens") || storedResponseTokens === 700
    ? 0
    : Math.max(0, Math.min(131072, Number.isFinite(storedResponseTokens) ? storedResponseTokens : 0));
  const settings = {
    llm_provider: provider,
    kobold_url: studio.kobold_url || video.kobold_url || "http://localhost:5001",
    ollama_url: studio.ollama_url || video.ollama_url || "http://localhost:11434",
    ollama_model: studio.ollama_model || video.ollama_model || "",
    llamacpp_url: studio.llamacpp_url || "http://localhost:8080",
    llamacpp_model: studio.llamacpp_model || "",
    llamacpp_executable: studio.llamacpp_executable || "",
    llamacpp_config_profile: studio.llamacpp_config_profile || "",
    llamacpp_autostart: studio.llamacpp_autostart === true,
    llamacpp_reasoning_budget_tokens: Math.max(0, Math.min(
      262144,
      Number(profile.llamacpp_reasoning_budget_tokens ?? studio.llamacpp_reasoning_budget_tokens ?? 0),
    )),
    thinking_mode: thinkingMode,
    max_response_tokens: responseTokens,
    context_budget_chars: Math.max(4000, Math.min(32000, Number(video.context_budget_chars || 8000))),
    temperature: profileValue("temperature", "thinking_temperature", 0.7),
    top_p: profileValue("top_p", "thinking_top_p", 0.9),
    top_k: profileValue("top_k", "thinking_top_k", 100),
    min_p: profileValue("min_p", "thinking_min_p", 0),
    presence_penalty: profileValue("presence_penalty", "thinking_presence_penalty", 0),
    rep_pen: profileValue("rep_pen", "thinking_rep_pen", 1.05),
    rep_pen_range: profileValue("rep_pen_range", "thinking_rep_pen_range", 360),
    sampler_seed: Number(video.sampler_seed ?? consult.sampler_seed ?? -1),
    request_timeout: !Object.hasOwn(video, "request_timeout") || storedTimeout === 120
      ? 600
      : Math.max(5, Math.min(3600, Number.isFinite(storedTimeout) ? storedTimeout : 600)),
  };
  return { ...normalizeProviderSettings(settings), context_budget_chars: settings.context_budget_chars };
}

function directorControlValue(dialog, id) {
  return dialog?.querySelector(`#${id}`)?.value;
}

function normalizeLlmProvider(value) {
  return sharedLlmProvider(value);
}

function llmProviderDisplayName(provider) {
  return { koboldcpp: "KoboldCpp", ollama: "Ollama", llamacpp: "Llama.cpp" }[
    normalizeLlmProvider(provider)
  ];
}

function thinkingModeEnablesReasoning(mode) {
  return !["disabled", "none"].includes(String(mode || "").trim().toLowerCase());
}

function llmActivityLabel(status = {}, thinkingEnabled = false) {
  if (status.generation_phase === "thinking") return "Thinking";
  if (status.generation_phase === "generating") return "Processing";
  if (status.generation_phase === "thinking_or_generating" || thinkingEnabled) return "Thinking / processing";
  return "Processing";
}

function llmGeneratedTokenCount(status = {}) {
  if (status.generated_tokens == null || status.generated_tokens === "") return null;
  const tokens = Number(status.generated_tokens);
  return Number.isFinite(tokens) && tokens >= 0 ? Math.trunc(tokens) : null;
}

function saveDirectorSettings(dialog = state.directorDialog) {
  if (!dialog) return directorSettings();
  const shared = directorSettings();
  const overrides = {
    max_response_tokens: Math.max(0, Math.min(131072, Number(directorControlValue(dialog, "psvstudio-director-max-tokens") || 0))),
    context_budget_chars: Number(directorControlValue(dialog, "psvstudio-director-context-budget") || 8000),
    request_timeout: Math.max(5, Math.min(3600, Number(directorControlValue(dialog, "psvstudio-director-timeout") || 600))),
  };
  try {
    localStorage.setItem(DIRECTOR_SETTINGS_KEY, JSON.stringify(overrides));
  } catch (_) {
    // The active request can still use these settings when storage is unavailable.
  }
  return { ...shared, ...overrides, sampler_seed: -1 };
}

function comfyUiIsProcessing() {
  return state.comfyQueueRemaining > 0 || activeGenerationPromptIds().length > 0;
}

function renderSystemStatusSummary() {
  const control = state.panel?.querySelector("#psvstudio-kobold-control");
  const label = control?.querySelector("#psvstudio-kobold-status-label");
  const comfyDetail = control?.querySelector("#psvstudio-comfy-status-detail");
  const restart = control?.querySelector("#psvstudio-comfy-restart");
  if (!control || !label || !comfyDetail || !restart) return;

  const llm = state.llmStatusSnapshot;
  const provider = normalizeLlmProvider(llm?.provider || directorSettings().llm_provider);
  const llmUnhealthy = llm?.reachable === false
    || (["ollama", "llamacpp"].includes(provider)
      && llm?.reachable === true && (!llm.model || llm.model_installed === false));
  const checking = !llm || llm.checking === true;
  const comfyProcessing = comfyUiIsProcessing();
  const processing = checking
    || llm?.busy === true
    || comfyProcessing
    || state.directorBusy
    || state.directorJobs.size > 0
    || state.comfyRestartBusy;
  const unhealthy = !state.apiConnected || llmUnhealthy;
  const stateName = unhealthy ? "offline" : (processing ? "busy" : "idle");
  control.dataset.state = stateName;
  control.querySelector("summary").title = stateName === "offline"
    ? "System status: attention needed"
    : (stateName === "busy" ? "System status: processing" : "System status: ready");
  label.textContent = stateName === "offline"
    ? "Status: attention needed"
    : (stateName === "busy" ? "Status: processing" : "Status: ready");

  if (!state.apiConnected) {
    comfyDetail.textContent = "Not responding";
    comfyDetail.dataset.state = "offline";
  } else if (state.comfyRestartBusy) {
    comfyDetail.textContent = "Restart requested";
    comfyDetail.dataset.state = "busy";
  } else if (comfyProcessing) {
    const queued = Math.max(0, Number(state.comfyQueueRemaining) || 0);
    comfyDetail.textContent = queued ? `Processing · ${queued} queued` : "Processing";
    comfyDetail.dataset.state = "busy";
  } else {
    comfyDetail.textContent = "Connected and ready";
    comfyDetail.dataset.state = "idle";
  }
  restart.disabled = state.comfyRestartBusy || !state.apiConnected;
  restart.textContent = state.comfyRestartBusy ? "Restarting…" : "Restart ComfyUI";
}

function renderLlmStatus(status = {}) {
  const control = state.panel?.querySelector("#psvstudio-kobold-control");
  const label = control?.querySelector("#psvstudio-kobold-status-label");
  const detail = control?.querySelector("#psvstudio-kobold-status-detail");
  const model = control?.querySelector("#psvstudio-kobold-model");
  const vision = control?.querySelector("#psvstudio-kobold-vision");
  const heading = control?.querySelector("#psvstudio-llm-status-heading");
  const stop = control?.querySelector("#psvstudio-kobold-stop");
  const stopHelp = control?.querySelector("#psvstudio-kobold-stop-help");
  if (!control || !label || !detail || !model || !vision || !heading || !stop || !stopHelp) return;
  const provider = normalizeLlmProvider(status.provider || directorSettings().llm_provider);
  const isOllama = provider === "ollama";
  const isLlamacpp = provider === "llamacpp";
  const providerName = llmProviderDisplayName(provider);
  const reachable = status.reachable === true;
  const busy = !isOllama && reachable && status.busy === true;
  state.llmStatusSnapshot = { ...status, provider };
  control.dataset.provider = provider;
  heading.textContent = providerName;
  const characters = Number(status.generated_characters);
  const generatedTokens = llmGeneratedTokenCount(status);
  const activity = llmActivityLabel(status, thinkingModeEnablesReasoning(directorSettings().thinking_mode));
  detail.textContent = status.message || (busy
    ? `${activity}${generatedTokens != null
      ? ` · ${generatedTokens.toLocaleString()} tokens`
      : (Number.isFinite(characters) && characters > 0 ? ` · ${characters.toLocaleString()} characters` : "")}`
    : (reachable ? "Ready for local requests." : `${providerName} could not be reached.`));
  const modelName = typeof status.model === "string" ? status.model.trim() : "";
  model.textContent = modelName || (reachable ? "Unavailable" : "—");
  model.title = modelName;
  vision.textContent = status.vision === true ? "Yes" : (status.vision === false ? "No" : (reachable ? "Unknown" : "—"));
  vision.dataset.state = status.vision === true ? "available" : (status.vision === false ? "unavailable" : "unknown");
  stop.hidden = isOllama;
  stopHelp.hidden = isOllama;
  stop.disabled = !busy || state.koboldAbortBusy;
  stop.textContent = state.koboldAbortBusy ? "Stopping…" : "Force stop processing";
  stopHelp.textContent = isLlamacpp
    ? "Stops Video Studio text streams only. Manage the Llama.cpp server in Prompt Studio settings."
    : "Stops LLM processing only. KoboldCpp stays loaded.";
  renderSystemStatusSummary();
}

async function refreshKoboldStatus() {
  if (state.koboldStatusRequest) return state.koboldStatusRequest;
  const settings = directorSettings();
  const control = state.panel?.querySelector("#psvstudio-kobold-control");
  if (!control || control.dataset.provider !== settings.llm_provider || control.dataset.state === "checking") {
    renderLlmStatus({ provider: settings.llm_provider, checking: true });
  }
  state.koboldStatusRequest = (async () => {
    try {
      const data = await readSharedHealth(LLM_STATUS_ENDPOINT,settings);
      renderLlmStatus(data);
      return data;
    } catch (error) {
      renderLlmStatus({ provider: settings.llm_provider, reachable: false, message: error.message || String(error) });
      return null;
    } finally {
      state.koboldStatusRequest = null;
    }
  })();
  return state.koboldStatusRequest;
}

async function stopLlmGeneration() {
  const settings = directorSettings();
  const provider = normalizeLlmProvider(settings.llm_provider);
  if (!["koboldcpp", "llamacpp"].includes(provider) || state.koboldAbortBusy) return;
  state.koboldAbortBusy = true;
  renderLlmStatus({ provider, reachable: true, busy: true, message: "Sending force-stop signal…" });
  try {
    const response = await api.fetchApi(LLM_ABORT_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(settings),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `${llmProviderDisplayName(provider)} stop failed (${response.status}).`);
    renderLlmStatus({
      provider,
      reachable: true,
      busy: data.success !== true,
      message: data.success
        ? "Stop signal accepted."
        : `${llmProviderDisplayName(provider)} reported no Video Studio processing to stop.`,
    });
  } catch (error) {
    renderLlmStatus({ provider, reachable: false, message: error.message || String(error) });
  } finally {
    state.koboldAbortBusy = false;
    window.setTimeout(refreshKoboldStatus, 500);
  }
}

async function restartComfyUIFromStatus() {
  if (state.comfyRestartBusy || !state.apiConnected) return;
  const ownerWindow = state.panel?.ownerDocument?.defaultView || window;
  if (!ownerWindow.confirm("Restart ComfyUI now? Running generations and connected clients will be interrupted.")) return;
  state.comfyRestartBusy = true;
  renderSystemStatusSummary();
  try {
    let response;
    for (const endpoint of COMFY_RESTART_ENDPOINTS) {
      response = await api.fetchApi(endpoint, { method: "POST" });
      if (response.ok || ![404, 405].includes(response.status)) break;
    }
    if (!response.ok) throw new Error(`ComfyUI Manager could not restart the server (${response.status}).`);
    const detail = state.panel?.querySelector("#psvstudio-comfy-status-detail");
    if (detail) detail.textContent = "Restarting; waiting to reconnect…";
  } catch (error) {
    state.comfyRestartBusy = false;
    renderSystemStatusSummary();
    const detail = state.panel?.querySelector("#psvstudio-comfy-status-detail");
    if (detail) {
      detail.textContent = `${error.message || error} Restart ComfyUI manually if Manager is unavailable.`;
      detail.dataset.state = "offline";
    }
  }
}

let videoPollingScope;
function studioPollingScope() {
  return videoPollingScope ||= createPollingScope({visible:() => Boolean(state.panel && !state.panel.hidden
    && !state.panel.closest('.promptstudio-studio-view')?.hidden && !state.panel.ownerDocument.hidden)});
}
function startKoboldStatusMonitor() {
  if (state.koboldStatusTimer) return;
  state.koboldStatusTimer = studioPollingScope().add(refreshKoboldStatus,{interval:KOBOLD_STATUS_POLL_MS,
    background:() => state.directorBusy || state.generationPollers.size > 0});
}

function directorSessions() {
  if (!state.directorSessions) state.directorSessions = storedObject(DIRECTOR_SESSIONS_KEY);
  return state.directorSessions;
}

function directorSessionId(projectId = state.activeProjectId, scope = state.directorScope) {
  const projectKey = String(projectId || "");
  return scope === "project" ? `${projectKey}:project` : projectKey;
}

function directorSession(projectId = state.activeProjectId, scope = state.directorScope) {
  const sessions = directorSessions();
  const normalizedScope = scope === "project" ? "project" : "shot";
  const id = directorSessionId(projectId, normalizedScope);
  if (!sessions[id] || !Array.isArray(sessions[id].messages)) {
    sessions[id] = { scope: normalizedScope, messages: [], draft_attachments: [], pending_plan: null, updated_at: Date.now() };
  }
  sessions[id].scope = normalizedScope;
  if (!Array.isArray(sessions[id].draft_attachments)) sessions[id].draft_attachments = [];
  if (!sessions[id].pending_job || typeof sessions[id].pending_job !== "object") sessions[id].pending_job = null;
  if (sessions[id].pending_plan?.clarification_id === "proposal-validation") {
    sessions[id].pending_plan = null;
  }
  return sessions[id];
}

function persistDirectorSessions() {
  const sessions = directorSessions();
  for (const session of Object.values(sessions)) {
    if (!Array.isArray(session?.messages)) continue;
    session.messages = session.messages.map(message => {
      if (message?.role === "assistant") ensureDirectorVariants(message);
      return {
        ...message,
        text: String(message?.text || ""),
        variants: Array.isArray(message?.variants) ? message.variants.map(variant => ({
          ...variant,
          text: String(variant?.text || ""),
        })) : undefined,
      };
    });
  }
  const retained = Object.fromEntries(
    Object.entries(sessions)
      .sort((a, b) => Number(b[1]?.updated_at || 0) - Number(a[1]?.updated_at || 0))
      .map(([id, session]) => [id, {
        scope: session?.scope === "project" ? "project" : "shot",
        updated_at: Number(session?.updated_at || Date.now()),
        last_context_usage: session?.last_context_usage || null,
        pending_plan: session?.pending_plan || null,
        pending_job: session?.pending_job || null,
        draft_attachments: session?.draft_attachments || [],
        messages: Array.isArray(session?.messages) ? session.messages : [],
      }]),
  );
  const archive = {sessions:retained,mutation:Date.now()};
  videoDraftOutbox.put(draftTabKey("video-director"),archive).catch(error => {
    showDraftStorageFailure(state.panel?.querySelector(".psvstudio-sidebar"),archive,error.message);
  });
  try {
    localStorage.setItem(DIRECTOR_SESSIONS_KEY, JSON.stringify(retained));
  } catch (_) {
    // IndexedDB retains the full archive; failures above expose a draft export.
  }
}

function boundedDirectorMessages(messages, maximumChars = 6500) {
  const normalized = (messages || [])
    .filter(message => ["user", "assistant"].includes(message?.role) && String(message?.text || "").trim())
    .map(message => ({ role: message.role, content: String(message.text).trim() }));
  const turns = [];
  let currentTurn = [];
  for (const message of normalized) {
    if (message.role === "user") {
      if (currentTurn.length) turns.push(currentTurn);
      currentTurn = [message];
    } else if (currentTurn.length) {
      currentTurn.push(message);
    }
  }
  if (currentTurn.length) turns.push(currentTurn);
  const retainedTurns = [];
  let used = 0;
  let retainedMessages = 0;
  for (const turn of turns.slice().reverse()) {
    const cost = turn.reduce((total, message) => total + message.content.length + 32, 0);
    if (retainedTurns.length && (used + cost > maximumChars || retainedMessages + turn.length > 10)) break;
    retainedTurns.push(turn);
    used += cost;
    retainedMessages += turn.length;
  }
  return retainedTurns.reverse().flat();
}

function directorShotLabel(project = activeProject(), shot = selectedShot(project)) {
  const index = project?.document?.shots?.indexOf(shot) ?? -1;
  return index >= 0 ? `Shot ${index + 1}` : "Selected shot";
}

function directorProposalFields(proposal) {
  const rows = [];
  const protectedLabels = {dialogue: "Spoken lines", lyrics: "Lyrics", speaker_ids: "Speaker IDs", visible_text: "Visible text"};
  for (const change of proposal?.protected_content_changes || []) {
    const display = value => Array.isArray(value)
      ? `${value[0]}${value[2] ? ` (${value[2]})` : ""}${value[1] ? ` [${value[1]}]` : ""}`
      : String(value);
    for (const [key, label] of [["removed", "Removed"], ["added", "Added"]]) {
      for (const value of change[key] || []) rows.push({
        name: `${protectedLabels[change.kind] || change.kind} · ${label}`, value: display(value),
      });
    }
  }
  for (const operation of proposal?.operations || []) {
    if (operation.replace === true) {
      const target = operation.op === "update_project"
        ? "Replace project prompt"
        : operation.op === "remove_shot"
          ? "Remove as part of full rewrite"
          : `Replace ${operation.shot_id || "shot"}`;
      rows.push({ name: target, value: "Old omitted fields will be cleared." });
    }
    if (operation.op === "add_shot") {
      const shot = operation.shot || {};
      const steps = Array.isArray(shot.steps) ? shot.steps : [];
      rows.push({ name: `Add shot at ${Number(shot.start || 0).toFixed(3)}s`, value: steps[0]?.text || JSON.stringify(shot) });
      for (const event of steps.filter(step => step?.type === "dialogue")) {
        const speaker = [event.speaker, event.speaker_id ? `(${event.speaker_id})` : ""].filter(Boolean).join(" ");
        rows.push({ name: `New dialogue${speaker ? ` · ${speaker}` : ""}`, value: event.text || "" });
      }
      continue;
    }
    if (operation.op === "remove_shot") {
      rows.push({ name: "Remove shot", value: operation.shot_id || "" });
      continue;
    }
    for (const [name, value] of Object.entries(operation.fields || {})) {
      const display = typeof value === "string" ? value : JSON.stringify(value);
      const prefix = operation.op === "update_project" ? "Project" : (operation.shot_id || "Shot");
      rows.push({ name: `${prefix} · ${name}`, value: display });
    }
  }
  return rows;
}

function ensureDirectorVariants(message) {
  if (message?.role !== "assistant") return [];
  if (!Array.isArray(message.variants)) message.variants = [];
  message.variants = message.variants
    .filter(variant => variant && typeof variant === "object")
    .map((variant, index) => ({
      id: String(variant.id || `${message.id}-response-${index}`),
      text: String(variant.text || ""),
      proposal: variant.proposal || null,
      proposal_error: String(variant.proposal_error || ""),
      proposal_state: String(variant.proposal_state || ""),
      status: String(variant.status || "ready"),
      clarification: variant.clarification || null,
      pending_plan: variant.pending_plan || null,
      context_usage: variant.context_usage || null,
      intent_route: String(variant.intent_route || ""),
      intent_warning: String(variant.intent_warning || ""),
      created_at: Number(variant.created_at || message.created_at || Date.now()),
    }));
  if (!message.variants.length) {
    message.variants.push({
      id: `${message.id}-response-0`,
      text: String(message.text || ""),
      proposal: message.proposal || null,
      proposal_error: String(message.proposal_error || ""),
      proposal_state: String(message.proposal_state || ""),
      status: String(message.status || "ready"),
      clarification: message.clarification || null,
      pending_plan: message.pending_plan || null,
      context_usage: message.context_usage || null,
      intent_route: String(message.intent_route || ""),
      intent_warning: String(message.intent_warning || ""),
      created_at: Number(message.created_at || Date.now()),
    });
  }
  const requestedIndex = Number(message.variant_index);
  const index = Number.isFinite(requestedIndex)
    ? Math.max(0, Math.min(Math.trunc(requestedIndex), message.variants.length - 1))
    : message.variants.length - 1;
  syncDirectorVariant(message, index);
  return message.variants;
}

function syncDirectorVariant(message, index) {
  const variant = message?.variants?.[index];
  if (!variant) return null;
  message.variant_index = index;
  message.text = variant.text;
  message.proposal = variant.proposal || null;
  message.proposal_error = variant.proposal_error || "";
  message.proposal_state = variant.proposal_state || "";
  message.status = variant.status || "ready";
  message.clarification = variant.clarification || null;
  message.pending_plan = variant.pending_plan || null;
  message.context_usage = variant.context_usage || null;
  message.intent_route = variant.intent_route || "";
  message.intent_warning = variant.intent_warning || "";
  return variant;
}

function selectedDirectorVariant(message) {
  const variants = ensureDirectorVariants(message);
  return variants[message.variant_index] || null;
}

function directorAttachmentMetadata(attachments) {
  return (attachments || []).map(attachment => ({
    id: attachment.id,
    path: attachment.path,
    name: attachment.name,
    usage: attachment.usage,
    reference_id: attachment.reference_id || "",
    source_width: Number(attachment.source_width || 0),
    source_height: Number(attachment.source_height || 0),
  }));
}

function directorRequestAttachments(project, draftAttachments) {
  const result = directorAttachmentMetadata(draftAttachments);
  const paths = new Set(result.map(item => item.path));
  for (const reference of project?.document?.references || []) {
    if (result.length >= DIRECTOR_MAX_IMAGES) break;
    if (reference.kind !== "image" || !reference.path || paths.has(reference.path)) continue;
    const usage = (reference.roles || [])[0] || "describe";
    const needsSubjectProfile = ["subject", "first_frame", "last_frame"].includes(usage)
      && (!reference.subject_candidates?.length
        || reference.subject_candidates.some(candidate =>
          !candidate?.grounded_attributes
          || !Object.keys(candidate.grounded_attributes).length));
    if (reference.observed_visual_facts && !needsSubjectProfile) continue;
    result.push({
      id: `project-grounding-${reference.id}`,
      path: reference.path,
      name: reference.name || reference.path,
      usage,
      reference_id: reference.id,
      source_width: Number(reference.source_width || 0),
      source_height: Number(reference.source_height || 0),
    });
    paths.add(reference.path);
  }
  return result;
}

function cacheDirectorVisionObservations(project, attachments, observations) {
  let changed = false;
  for (const observation of observations || []) {
    const attachment = attachments.find(item => item.id === observation.attachment_id)
      || attachments[Number(observation.index || 0) - 1];
    if (!attachment || attachment.usage === "describe") continue;
    const reference = (project.document.references || []).find(item =>
      item.id === attachment.reference_id || (item.kind === "image" && item.path === attachment.path));
    if (!reference) continue;
    const facts = String(observation.observations || "").trim();
    const candidates = Array.isArray(observation.subject_candidates)
      ? observation.subject_candidates.map(item => ({
          name: String(item?.name || "").trim(),
          location: String(item?.location || "").trim(),
          visual_selectors: Array.isArray(item?.visual_selectors)
            ? item.visual_selectors.map(value => String(value || "").trim()).filter(Boolean).slice(0, 16)
            : [],
          grounded_attributes: item?.grounded_attributes && typeof item.grounded_attributes === "object"
            ? Object.fromEntries(Object.entries(item.grounded_attributes)
                .map(([key, value]) => [String(key), String(value || "").trim()])
                .filter(([, value]) => value))
            : {},
        })).filter(item => item.name)
      : [];
    if (facts && reference.observed_visual_facts !== facts) {
      reference.observed_visual_facts = facts;
      changed = true;
    }
    if (JSON.stringify(reference.subject_candidates || []) !== JSON.stringify(candidates)) {
      reference.subject_candidates = candidates;
      changed = true;
    }
  }
  return changed;
}

function renderDirectorAttachments() {
  const dialog = state.directorDialog;
  const project = activeProject();
  const container = dialog?.querySelector("#psvstudio-director-attachments");
  if (!dialog || !project || !container) return;
  const session = directorSession(project.id);
  container.replaceChildren();
  container.classList.toggle("is-empty", !session.draft_attachments.length);
  if (!session.draft_attachments.length) {
    container.append(el("small", "", "Drop or paste images here, or use Add image. Choose whether each image is visual context only or a MiniMax reference."));
    renderDirectorReferenceGuide();
    return;
  }
  for (const attachment of session.draft_attachments) {
    const card = el("div", "psvstudio-director-attachment");
    const image = dialog.ownerDocument.createElement("img");
    image.src = mediaInputUrl({ path: attachment.path });
    image.alt = "";
    const details = el("div", "psvstudio-director-attachment-details");
    const displayLabel = directorAttachmentDisplayLabel(project, attachment, session.draft_attachments);
    details.append(
      el("strong", "", displayLabel),
      el("small", "", attachment.name || attachment.path || "Image"),
    );
    const usage = selectInput(DIRECTOR_IMAGE_USAGES, attachment.usage, value => {
      attachment.usage = value;
      attachment.reference_id = "";
      renderDirectorAttachments();
    });
    usage.setAttribute("aria-label", `Use of ${displayLabel}`);
    details.append(usage);
    const remove = button("×", () => {
      session.draft_attachments = session.draft_attachments.filter(item => item.id !== attachment.id);
      renderDirectorAttachments();
    }, "psvstudio-media-remove");
    remove.ariaLabel = `Remove ${attachment.name} from Director turn`;
    card.append(image, details, remove);
    container.append(card);
  }
  renderDirectorReferenceGuide();
}

function insertDirectorReferenceToken(token) {
  const input = state.directorDialog?.querySelector("#psvstudio-director-input");
  if (!input) return;
  const start = Number.isFinite(input.selectionStart) ? input.selectionStart : input.value.length;
  const end = Number.isFinite(input.selectionEnd) ? input.selectionEnd : start;
  const prefix = start > 0 && !/\s$/.test(input.value.slice(0, start)) ? " " : "";
  const suffix = end < input.value.length && !/^\s/.test(input.value.slice(end)) ? " " : "";
  input.setRangeText(`${prefix}${token}${suffix}`, start, end, "end");
  input.focus();
}

function renderDirectorReferenceGuide() {
  const dialog = state.directorDialog;
  const project = activeProject();
  const container = dialog?.querySelector("#psvstudio-director-reference-list");
  const guidance = dialog?.querySelector("#psvstudio-director-reference-guidance");
  if (!dialog || !project || !container || !guidance) return;
  const references = (project.document.references || []).filter(reference => !(reference.kind === "audio"
    && (reference.roles || []).length === 1 && reference.roles[0] === "exact_audio"));
  const draftAttachments = directorSession(project.id).draft_attachments || [];
  const items = references.map(reference => {
    const draft = draftAttachments.find(attachment =>
      attachment.reference_id === reference.id
      || (reference.kind === "image" && attachment.path === reference.path));
    return {
      token: `<${referenceDisplayLabel(references, reference)}>`,
      path: reference.path,
      kind: reference.kind,
      name: reference.name || reference.path || "Media",
      role: draft?.usage || (reference.roles || [])[0] || "reference",
    };
  });
  for (const attachment of draftAttachments) {
    if (attachment.usage === "describe") continue;
    const committed = references.some(reference =>
      reference.id === attachment.reference_id
      || (reference.kind === "image" && reference.path === attachment.path));
    if (committed) continue;
    items.push({
      token: `<${directorAttachmentDisplayLabel(project, attachment, draftAttachments)}>`,
      path: attachment.path,
      kind: "image",
      name: attachment.name || attachment.path || "Image",
      role: attachment.usage,
    });
  }

  container.replaceChildren();
  if (!items.length) {
    container.append(el("div", "psvstudio-director-reference-empty", "No named references yet. Add an image and choose a reference role."));
    guidance.textContent = "Natural descriptive language is enough when no MiniMax reference is active.";
    return;
  }
  for (const item of items) {
    const card = button("", () => insertDirectorReferenceToken(item.token), "psvstudio-director-reference-card");
    card.title = `Insert ${item.token} into the Director instruction`;
    card.setAttribute("aria-label", `${item.token}, ${item.name}. Insert reference label.`);
    const url = mediaInputUrl({ path: item.path });
    if (item.kind === "image" && url) {
      const thumbnail = dialog.ownerDocument.createElement("img");
      thumbnail.src = url;
      thumbnail.alt = "";
      thumbnail.loading = "lazy";
      card.append(thumbnail);
    } else {
      card.append(el("span", "psvstudio-director-reference-kind", item.kind === "video" ? "VID" : "AUD"));
    }
    const roleOptions = item.kind === "image" ? DIRECTOR_IMAGE_USAGES : referenceRoleOptions(item.kind);
    const roleLabel = roleOptions.find(option => option.value === item.role)?.label || item.role;
    const details = el("span", "psvstudio-director-reference-details");
    details.append(el("strong", "", item.token), el("small", "", item.name), el("em", "", roleLabel));
    card.append(details);
    container.append(card);
  }
  guidance.textContent = items.length === 1
    ? `One reference is unambiguous: natural wording works, or click ${items[0].token} to insert its exact label.`
    : "Multiple references are active. Use the exact labels below so the Director knows which subject, scene, style, or frame you mean.";
}

function directorImageFile(file) {
  if (file?.type?.startsWith("image/")) return true;
  return ["png", "jpg", "jpeg", "webp", "gif", "bmp", "tif", "tiff"].includes(file?.name?.split(".").pop()?.toLowerCase());
}

function clipboardImageFiles(event) {
  const itemFiles = Array.from(event.clipboardData?.items || [])
    .filter(item => item.kind === "file" && item.type?.startsWith("image/"))
    .map(item => item.getAsFile())
    .filter(Boolean);
  if (itemFiles.length) return itemFiles;
  return Array.from(event.clipboardData?.files || []).filter(directorImageFile);
}

async function addDirectorImages(fileList) {
  const project = activeProject();
  const dialog = state.directorDialog;
  if (!project || !dialog) return;
  const session = directorSession(project.id);
  const files = Array.from(fileList || []).filter(directorImageFile);
  const remaining = Math.max(0, DIRECTOR_MAX_IMAGES - session.draft_attachments.length);
  if (!files.length) {
    dialog.querySelector("#psvstudio-director-status").textContent = "Director accepts image files only.";
    return;
  }
  if (!remaining) {
    dialog.querySelector("#psvstudio-director-status").textContent = `Attach at most ${DIRECTOR_MAX_IMAGES} images per turn.`;
    return;
  }
  const errors = [];
  for (const file of files.slice(0, remaining)) {
    try {
      dialog.querySelector("#psvstudio-director-status").textContent = `Uploading ${file.name || "pasted image"} to ComfyUI input storage…`;
      const dimensions = await fileMediaDimensions(file, "image");
      const path = await uploadMediaFile(file);
      session.draft_attachments.push({
        id: makeId("director-image"), path, name: file.name || path.split("/").pop(), usage: "describe", reference_id: "",
        source_width: dimensions?.width || 0, source_height: dimensions?.height || 0,
      });
    } catch (error) {
      errors.push(`${file.name}: ${error.message || error}`);
    }
  }
  if (files.length > remaining) errors.push(`Only the first ${remaining} image${remaining === 1 ? "" : "s"} fit this turn.`);
  dialog.querySelector("#psvstudio-director-status").textContent = errors.length ? errors.join(" ") : "Images are ready for this Director turn.";
  renderDirectorAttachments();
}

function commitDirectorImageReferences(project, attachments) {
  let changed = false;
  for (const attachment of attachments || []) {
    if (attachment.usage === "describe") continue;
    let reference = project.document.references.find(item =>
      item.id === attachment.reference_id || (item.kind === "image" && item.path === attachment.path));
    if (!reference) {
      reference = {
        id: makeId("reference"), kind: "image", path: attachment.path, name: attachment.name,
        roles: [attachment.usage], prompt: "", label: "", trim_start: 0, trim_end: null,
        use_embedded_audio: false, source_width: attachment.source_width || 0,
        source_height: attachment.source_height || 0,
      };
      project.document.references.push(reference);
      changed = true;
    }
    if (["first_frame", "last_frame"].includes(attachment.usage)) {
      for (const item of project.document.references) {
        if (item.id === reference.id || !(item.roles || []).includes(attachment.usage)) continue;
        item.roles = item.roles.filter(role => role !== attachment.usage);
        if (!item.roles.length) item.roles = [item.kind === "audio" ? "audio_reference" : "subject"];
        changed = true;
      }
    }
    if (reference.roles?.length !== 1 || reference.roles[0] !== attachment.usage) {
      reference.observed_visual_facts = "";
      reference.subject_candidates = [];
      reference.roles = [attachment.usage];
      changed = true;
    }
    if (!reference.source_width && attachment.source_width) {
      reference.source_width = attachment.source_width;
      changed = true;
    }
    if (!reference.source_height && attachment.source_height) {
      reference.source_height = attachment.source_height;
      changed = true;
    }
    attachment.reference_id = reference.id;
  }
  if (!changed) return false;
  project.document.references.forEach(item => { item.label = ""; });
  invalidateReferenceSemantics(project);
  synchronizeGeometryCanvas(project);
  markProjectChanged({ render: true });
  return true;
}

function invalidateReferenceSemantics(project) {
  project.document.task_types = [];
  project.document.subject_definitions = [];
  project.document.summary = "";
  project.document.retention_analysis = [];
}

function renderDirectorDialog() {
  const dialog = state.directorDialog;
  const project = activeProject();
  const shot = selectedShot(project);
  const projectScope = state.directorScope === "project";
  if (!dialog || !project || (!projectScope && !shot)) return;
  dialog.querySelector("#psvstudio-director-title").textContent = projectScope
    ? "Video director · Entire video"
    : `Shot director · ${directorShotLabel(project, shot)}`;
  dialog.querySelector("#psvstudio-director-subtitle").textContent = projectScope
    ? "Full-production consultation and multi-shot composition"
    : "Context-efficient selected-shot consultation";
  const input = dialog.querySelector("#psvstudio-director-input");
  input.placeholder = projectScope
    ? "Ask about the whole video or request a multi-shot composition…"
    : "Ask about this shot or request a concrete revision…";
  if ((project.document.references || []).length > 1) {
    input.placeholder = projectScope
      ? "Describe the whole video using exact labels such as <Picture 1> and <Picture 2>."
      : "Revise this shot using exact labels such as <Picture 1> and <Picture 2>.";
  }
  const history = dialog.querySelector("#psvstudio-director-history");
  const namespace = directorSessionId(project.id);
  const stickToEnd = history.dataset.historySession !== namespace
    || history.scrollHeight - history.scrollTop - history.clientHeight < 80;
  history.dataset.historySession = namespace;
  const rows = [];
  const addRow = (id, signature, create) => rows.push({id, signature, create});
  const session = directorSession(project.id);
  const directorBusy = Boolean(session.pending_job) || state.directorBusy;
  if (session.pending_plan) {
    input.placeholder = "Answer the Director's clarification to continue the pending plan…";
  }
  if (!session.messages.length) {
    addRow("empty", String(projectScope), () => el("div", "psvstudio-director-empty", projectScope
      ? "Ask for a full-video critique, alternatives, or a multi-shot composition. Only approved proposals can change the project."
      : "Ask for advice, alternatives, or a concrete revision. Only approved proposals can change the selected shot."));
  }
  for (const [messageIndex, message] of session.messages.entries()) {
    if (message.role === "assistant") ensureDirectorVariants(message);
    const last = messageIndex === session.messages.length - 1;
    addRow(`message:${message.id}`, JSON.stringify([message, last,
      last || message.proposal || message.clarification ? directorBusy : null,
      message.attachments?.length ? project.document.references : null]), () => {
    const card = el("article", `psvstudio-director-message is-${message.role}`);
    card.dataset.messageId = message.id;
    card.append(el("small", "", message.role === "user" ? "You" : "Director"), el("div", "psvstudio-director-message-text", message.text));
    if (message.attachments?.length) {
      const attached = el("div", "psvstudio-director-message-attachments");
      for (const attachment of message.attachments) {
        const usage = DIRECTOR_IMAGE_USAGES.find(item => item.value === attachment.usage)?.label || attachment.usage;
        const displayLabel = directorAttachmentDisplayLabel(project, attachment, message.attachments);
        const chip = el("span", "", `${displayLabel} · ${usage}`);
        chip.title = attachment.name || attachment.path || "Image";
        attached.append(chip);
      }
      card.append(attached);
    }
    if (message.proposal) {
      const proposal = el("section", "psvstudio-director-proposal");
      proposal.append(el("strong", "", message.proposal.summary || "Proposed shot update"));
      for (const row of directorProposalFields(message.proposal)) {
        const item = el("div", "psvstudio-director-change");
        item.append(el("span", "", row.name), el("p", "", row.value || "(clear field)"));
        proposal.append(item);
      }
      const actions = el("div", "psvstudio-inline");
      if (message.proposal_state === "applied") {
        actions.append(el("small", "psvstudio-director-applied", "Applied"));
      } else if (message.proposal_state === "discarded") {
        actions.append(el("small", "psvstudio-help", "Discarded"));
      } else {
        const apply = button("Apply proposal", () => applyDirectorProposal(message.id), "psvstudio-button psvstudio-button-primary");
        const discard = button("Discard", () => discardDirectorProposal(message.id));
        apply.disabled = directorBusy;
        discard.disabled = directorBusy;
        actions.append(apply, discard);
      }
      proposal.append(actions);
      card.append(proposal);
    }
    if (message.clarification) {
      const clarification = el("section", "psvstudio-director-clarification");
      clarification.append(el("strong", "", "Clarification needed"));
      const reason = String(message.clarification.reason || "").trim();
      if (reason) clarification.append(el("div", "psvstudio-help", reason));
      const choices = el("div", "psvstudio-inline");
      for (const choice of message.clarification.choices || []) {
        const choose = button(choice, () => {
          input.value = choice;
          input.focus();
        }, "psvstudio-button");
        choose.disabled = directorBusy;
        choices.append(choose);
      }
      if (choices.childElementCount) clarification.append(choices);
      clarification.append(el("small", "psvstudio-help", "Your answer continues the existing request; no proposal has been discarded or applied."));
      card.append(clarification);
    } else if (message.proposal_error) {
      card.append(el("small", "psvstudio-director-error", `Proposal not applied: ${message.proposal_error}`));
    }
    if (message.intent_warning) {
      card.append(el("small", "psvstudio-director-error", `Intent routing fell back to automatic mode: ${message.intent_warning}`));
    }
    const canNavigateResponses = message.role === "assistant"
      && messageIndex === session.messages.length - 1
      && session.messages[messageIndex - 1]?.role === "user";
    if (canNavigateResponses) {
      const variants = message.variants || [];
      const selectedIndex = Math.max(0, Math.min(Number(message.variant_index) || 0, variants.length - 1));
      const controls = el("div", "psvstudio-director-response-controls");
      if (selectedIndex > 0) {
        const previous = button("", () => selectDirectorResponse(message.id, selectedIndex - 1), "psvstudio-director-response-arrow");
        previous.setAttribute("aria-label", "Show previous answer");
        previous.title = "Show previous answer";
        previous.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m14.5 18-6-6 6-6"/></svg>';
        previous.disabled = directorBusy;
        controls.append(previous);
      } else {
        controls.append(el("span"));
      }
      const position = el("span", "psvstudio-director-response-position", variants.length > 1 ? `${selectedIndex + 1} / ${variants.length}` : "");
      controls.append(position);
      const hasNewer = selectedIndex < variants.length - 1;
      const next = button("", () => {
        if (hasNewer) selectDirectorResponse(message.id, selectedIndex + 1);
        else regenerateDirectorResponse(message.id);
      }, "psvstudio-director-response-arrow");
      next.setAttribute("aria-label", hasNewer ? "Show next answer" : "Regenerate answer");
      next.title = hasNewer ? "Show next answer" : "Regenerate answer";
      next.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9.5 6 6 6-6 6"/></svg>';
      next.disabled = directorBusy;
      controls.append(next);
      card.append(controls);
    }
    return card;
    });
  }
  if (directorBusy && session.pending_job?.progress) {
    addRow("pending", session.pending_job.progress, () => {
    const pending = el("article", "psvstudio-director-message is-assistant is-pending");
    const pendingText = el("div", "psvstudio-director-message-text", session.pending_job.progress);
    pendingText.dataset.directorPending = "true";
    pendingText.setAttribute("role", "status");
    pendingText.setAttribute("aria-live", "polite");
    const cancel = button("Cancel", () => cancelDirectorSessionJob(project.id, state.directorScope));
    cancel.classList.add("psvstudio-director-cancel");
    pending.append(el("small", "", "Director"), pendingText, cancel);
    return pending;
    });
  }
  reconcileKeyedHistory(history, rows, {
    namespace, signature: row => row.signature, create: row => row.create(),
  });
  const usage = session.last_context_usage;
  const status = dialog.querySelector("#psvstudio-director-status");
  if (!directorBusy && usage) {
    const omitted = Number(usage.omitted_messages || 0);
      status.textContent = `Sent ${usage.history_messages} recent messages · ${usage.context_chars + usage.history_chars} context characters${omitted ? ` · omitted ${omitted} older messages` : ""}`;
  } else if (!directorBusy) {
    status.textContent = projectScope
      ? "The full video is in context; project changes require approval."
      : "Only the selected shot is in write scope.";
  }
  const send = dialog.querySelector("#psvstudio-director-send");
  if (send) send.disabled = directorBusy;
  const clear = dialog.querySelector("#psvstudio-director-clear");
  if (clear) clear.disabled = directorBusy;
  renderDirectorAttachments();
  if (stickToEnd) history.scrollTop = history.scrollHeight;
}

function clearDirectorSession() {
  const dialog = state.directorDialog;
  const project = activeProject();
  if (!dialog || !project || state.directorBusy || directorSessionBusy(project.id, state.directorScope)) return;
  const input = dialog.querySelector("#psvstudio-director-input");
  const sessionId = directorSessionId(project.id, state.directorScope);
  const session = directorSessions()[sessionId];
  const hasConversation = Array.isArray(session?.messages) && session.messages.length > 0;
  const hasPendingContext = Array.isArray(session?.draft_attachments) && session.draft_attachments.length > 0;
  const hasCachedContext = Boolean(session?.last_context_usage);
  if (!hasConversation && !hasPendingContext && !hasCachedContext && !input?.value.trim()) {
    const status = dialog.querySelector("#psvstudio-director-status");
    if (status) status.textContent = "Conversation is already clear.";
    return;
  }
  const view = dialog.ownerDocument?.defaultView;
  if (!view?.confirm("Clear this Director conversation, draft, and all pending image context?")) return;
  delete directorSessions()[sessionId];
  if (input) input.value = "";
  const imageInput = dialog.querySelector("#psvstudio-director-image-input");
  if (imageInput) imageInput.value = "";
  state.directorPendingText = "";
  dialog.classList.remove("is-image-dragover");
  persistDirectorSessions();
  renderDirectorDialog();
  const status = dialog.querySelector("#psvstudio-director-status");
  if (status) status.textContent = "Conversation cleared.";
  input?.focus();
}

function ensureDirectorDialog() {
  const owner = state.panel?.ownerDocument || document;
  if (state.directorDialog?.ownerDocument === owner) return state.directorDialog;
  state.directorDialog?.remove();
  const settings = directorSettings();
  const dialog = owner.createElement("dialog");
  dialog.className = "psvstudio-director-dialog";
  dialog.setAttribute("aria-labelledby", "psvstudio-director-title");
  dialog.innerHTML = `
    <header><div class="psvstudio-director-heading"><h2 id="psvstudio-director-title">Director</h2><small id="psvstudio-director-subtitle">Context-efficient selected-shot consultation</small></div><div class="psvstudio-director-header-actions"><button id="psvstudio-director-clear" class="psvstudio-button" type="button">Clear</button><button id="psvstudio-director-close" class="psvstudio-button psvstudio-icon-button" type="button" aria-label="Close Director">×</button></div></header>
    <div class="psvstudio-director-workspace">
      <aside class="psvstudio-director-reference-guide" aria-label="Named Director references">
        <div><strong>Named media</strong><small id="psvstudio-director-reference-guidance"></small></div>
        <div id="psvstudio-director-reference-list" class="psvstudio-director-reference-list"></div>
      </aside>
      <div class="psvstudio-director-conversation">
        <div id="psvstudio-director-history" class="psvstudio-director-history"></div>
    <div class="psvstudio-director-composer">
      <div id="psvstudio-director-attachments" class="psvstudio-director-attachments is-empty"></div>
      <textarea id="psvstudio-director-input" rows="3" aria-label="Director message" placeholder="Ask about this shot or request a concrete revision…"></textarea>
      <div class="psvstudio-director-composer-actions"><small id="psvstudio-director-status">Only the selected shot is in write scope.</small><div class="psvstudio-inline"><button id="psvstudio-director-add-image" class="psvstudio-button" type="button">Add image</button><button id="psvstudio-director-send" class="psvstudio-button psvstudio-button-primary" type="button">Ask Director</button></div></div>
      <input id="psvstudio-director-image-input" class="psvstudio-sr-only" type="file" aria-label="Attach images to the Director" accept="image/*" multiple />
      </div>
    </div>
    </div>
    <details class="psvstudio-director-settings"><summary>Local LLM and context settings</summary><div class="psvstudio-director-settings-grid">
      <div class="psvstudio-director-shared-llm"><span>Shared Prompt Studio LLM</span><strong id="psvstudio-director-shared-llm"></strong><small>Provider, endpoint, model, LLM configuration, thinking, and samplers are managed in the primary Prompt Studio settings.</small></div>
      <label><span>Response tokens · 0 = full available context</span><input id="psvstudio-director-max-tokens" type="number" min="0" max="131072" step="128" /></label>
      <label><span>Context characters</span><input id="psvstudio-director-context-budget" type="number" min="4000" max="32000" step="1000" /></label>
      <label><span>Request timeout seconds</span><input id="psvstudio-director-timeout" type="number" min="5" max="3600" step="30" /></label>
    </div><small>Llama.cpp endpoint, model, reasoning cap, and server management come from the primary Prompt Studio settings. Approved document state plus at most ten recent messages are sent.</small></details>`;
  const sharedModel = settings.llm_provider === "ollama"
    ? settings.ollama_model
    : (settings.llm_provider === "llamacpp" ? settings.llamacpp_model : "active model");
  dialog.querySelector("#psvstudio-director-shared-llm").textContent = [
    llmProviderDisplayName(settings.llm_provider),
    sharedModel,
    settings.llm_provider === "llamacpp" ? settings.llamacpp_config_profile : "",
    settings.thinking_mode,
  ].filter(Boolean).join(" · ");
  dialog.querySelector("#psvstudio-director-max-tokens").value = String(settings.max_response_tokens);
  dialog.querySelector("#psvstudio-director-context-budget").value = String(settings.context_budget_chars);
  dialog.querySelector("#psvstudio-director-timeout").value = String(settings.request_timeout);
  dialog.querySelector("#psvstudio-director-clear").addEventListener("click", clearDirectorSession);
  dialog.querySelector("#psvstudio-director-close").addEventListener("click", () => dialog.close());
  dialog.querySelector("#psvstudio-director-send").addEventListener("click", sendDirectorMessage);
  dialog.querySelector("#psvstudio-director-add-image").addEventListener("click", () => dialog.querySelector("#psvstudio-director-image-input").click());
  dialog.querySelector("#psvstudio-director-image-input").addEventListener("change", event => {
    addDirectorImages(event.target.files);
    event.target.value = "";
  });
  dialog.addEventListener("dragover", event => {
    if (!Array.from(event.dataTransfer?.types || []).includes("Files")) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    clearMediaDrag(owner);
    dialog.classList.add("is-image-dragover");
  });
  dialog.addEventListener("dragleave", event => {
    if (!event.relatedTarget || !dialog.contains(event.relatedTarget)) dialog.classList.remove("is-image-dragover");
  });
  dialog.addEventListener("drop", event => {
    if (!Array.from(event.dataTransfer?.types || []).includes("Files")) return;
    event.preventDefault();
    event.stopPropagation();
    dialog.classList.remove("is-image-dragover");
    addDirectorImages(event.dataTransfer.files);
  });
  dialog.addEventListener("paste", event => {
    const files = clipboardImageFiles(event);
    if (!files.length) return;
    event.preventDefault();
    event.stopPropagation();
    addDirectorImages(files);
  });
  dialog.addEventListener("close", () => dialog.classList.remove("is-image-dragover"));
  dialog.querySelector("#psvstudio-director-input").addEventListener("keydown", event => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      sendDirectorMessage();
    }
  });
  owner.body.append(dialog);
  state.directorDialog = dialog;
  return dialog;
}

function openDirector(scope = "shot", prefill = "") {
  const project = activeProject();
  const shot = selectedShot(project);
  if (!project || (scope !== "project" && !shot)) return;
  const previousScope = state.directorScope;
  state.directorScope = scope === "project" ? "project" : "shot";
  const dialog = ensureDirectorDialog();
  const input = dialog.querySelector("#psvstudio-director-input");
  if (prefill || previousScope !== state.directorScope) input.value = prefill;
  renderDirectorDialog();
  if (!dialog.open) dialog.showModal();
  input.focus();
}

function directorRequestPayload(project, shot, scope, attachments, messages, jobId) {
  const normalizedScope = scope === "project" ? "project" : "shot";
  return {
    ...saveDirectorSettings(state.directorDialog),
    async: true,
    job_id: jobId,
    origin: { project_id: project.id, message_id: messages.at(-1)?.id || "" },
    project_name: project.name,
    brief: project.brief,
    document: clone(project.document),
    scope: normalizedScope,
    selected_shot_id: normalizedScope === "project" ? "" : (shot?.id || ""),
    attachments: clone(attachments),
    pending_plan: clone(directorSession(project.id, normalizedScope).pending_plan || null),
    continuation_context: clone(project.extension_source?.director_context || null),
    messages: boundedDirectorMessages(messages),
  };
}

function directorSessionBusy(projectId = state.activeProjectId, scope = state.directorScope) {
  return Boolean(directorSessions()[directorSessionId(projectId, scope)]?.pending_job);
}

function projectHasPendingDirectorJob(projectId) {
  return directorSessionBusy(projectId, "shot") || directorSessionBusy(projectId, "project");
}

function waitForDirectorRetry() {
  return new Promise(resolve => setTimeout(resolve, 1500));
}

async function requestDirectorResponse(pending, sessionId) {
  while (true) {
    let response;
    try {
      response = await api.fetchApi(DIRECTOR_CHAT_ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(pending.request),
      });
    } catch (_) {
      setDirectorProgress("Director connection was interrupted; reconnecting...", sessionId);
      await waitForDirectorRetry();
      continue;
    }
    let data = await response.json().catch(() => ({}));
    const recovered = recoveredJobError(data);
    if (recovered) throw recovered;
    if (!response.ok) {
      if (data.retryable === false) throw new Error(data.error || "Director request failed.");
      if (response.status >= 500) {
        setDirectorProgress("Director service is temporarily unavailable; retrying...", sessionId);
        await waitForDirectorRetry();
        continue;
      }
      throw new Error(data.error || `Director request failed (${response.status}).`);
    }
    if (response.status === 202 && data.job_id) {
      data = await pollDirectorJob(data.job_id, sessionId);
      if (data === null) continue;
    }
    if (data.scope && data.scope !== pending.scope) {
      throw new Error(`Director scope mismatch: requested ${pending.scope}, received ${data.scope}.`);
    }
    return data;
  }
}

function directorJobStatusText(job) {
  if (job.status === "queued") return "Director request is queued…";
  const progress = job.director_progress || {};
  if (progress.phase === "intent_classification") {
    return "Classifying request…";
  }
  if (progress.phase === "proposal_correction") {
    const attempt = Math.max(1, Number(progress.attempt) || 1);
    const maximum = Math.max(attempt, Number(progress.maximum_attempts) || attempt);
    const activity = job.provider_status?.busy ? "processing" : "queued";
    return `Proposal omitted or invalid · automatic correction ${attempt} of ${maximum} is ${activity}…`;
  }
  if (progress.phase === "vision_grounding") {
    const imageIndex = Math.max(1, Number(progress.image_index) || 1);
    const totalImages = Math.max(imageIndex, Number(progress.total_images) || imageIndex);
    const attempt = Math.max(1, Number(progress.attempt) || 1);
    const maximum = Math.max(attempt, Number(progress.maximum_attempts) || attempt);
    const imageLabel = totalImages > 1 ? `image ${imageIndex} of ${totalImages}` : "the reference image";
    return `Grounding ${imageLabel} independently · attempt ${attempt} of ${maximum}…`;
  }
  const provider = job.provider_status || {};
  if (progress.phase === "director_generation") {
    const groundedImages = Math.max(0, Number(progress.grounded_images) || 0);
    const prefix = groundedImages
      ? `Grounding complete for ${groundedImages} image${groundedImages === 1 ? "" : "s"} · `
      : "";
    const characters = Number(provider.generated_characters);
    const tokens = llmGeneratedTokenCount(provider);
    const activity = llmActivityLabel(provider, thinkingModeEnablesReasoning(directorSettings().thinking_mode));
    if (provider.busy && tokens != null) {
      return `${prefix}${activity} · ${tokens.toLocaleString()} tokens received…`;
    }
    if (provider.busy && Number.isFinite(characters) && characters > 0) {
      return `${prefix}${activity} · ${characters.toLocaleString()} characters received…`;
    }
    return `${prefix}Director is processing the response…`;
  }
  const providerName = llmProviderDisplayName(provider.provider || directorSettings().llm_provider);
  if (provider.provider === "ollama") return "The local Director is still working…";
  if (provider.reachable === false) return `Director is running; ${providerName} status is temporarily unavailable…`;
  if (provider.busy) {
    const tokens = llmGeneratedTokenCount(provider);
    const activity = llmActivityLabel(provider, thinkingModeEnablesReasoning(directorSettings().thinking_mode));
    if (tokens != null) return `${providerName} · ${activity.toLowerCase()} · ${tokens.toLocaleString()} tokens received…`;
    const characters = Number(provider.generated_characters);
    return Number.isFinite(characters) && characters > 0
      ? `${providerName} is still processing · ${characters.toLocaleString()} characters received…`
      : `${providerName} is still processing the prompt…`;
  }
  return `Director is validating ${providerName}'s response…`;
}

async function pollDirectorJob(jobId, sessionId) {
  let statusFailures = 0;
  let previousStatus = null;
  while (true) {
    await new Promise(resolve => setTimeout(resolve, DIRECTOR_JOB_POLL_MS));
    let response;
    try {
      response = await api.fetchApi(`${DIRECTOR_CHAT_ENDPOINT}/${encodeURIComponent(jobId)}`);
    } catch (_) {
      setDirectorProgress("Director is still running; reconnecting to its status...", sessionId);
      await waitForDirectorRetry();
      continue;
    }
    const job = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (response.status === 404) return null;
      if (response.status >= 500 && statusFailures < DIRECTOR_STATUS_RETRY_LIMIT) {
        statusFailures += 1;
        setDirectorProgress("Director status was temporarily unavailable; retrying…", sessionId);
        continue;
      }
      throw new Error(job.error || `Director status check failed (${response.status}).`);
    }
    statusFailures = 0;
    const checkedJob = normalizeJobWire({ ...job, job_id: jobId });
    if (previousStatus) assertObservedJobTransition(previousStatus, checkedJob.status);
    previousStatus = checkedJob.status;
    if (checkedJob.status === "complete") return checkedJob.result || {};
    if (checkedJob.status === "failed") throw recoveredJobError(job)
      || new Error(`${checkedJob.error || "Director request failed."} ${jobRetryText(job.job)}`);
    if (checkedJob.status === "cancelled") throw new DOMException("Director request was cancelled.", "AbortError");
    setDirectorProgress(directorJobStatusText(job), sessionId);
  }
}

function setDirectorProgress(text, sessionId = directorSessionId()) {
  state.directorPendingText = String(text || "");
  const session = directorSessions()[sessionId];
  if (session?.pending_job) session.pending_job.progress = state.directorPendingText;
  if (sessionId !== directorSessionId()) return;
  const dialog = state.directorDialog;
  const pending = dialog?.querySelector("[data-director-pending]");
  if (pending) pending.textContent = state.directorPendingText;
  else renderDirectorDialog();
}

function renderDirectorJobState(sessionId) {
  if (sessionId === directorSessionId()) renderDirectorDialog();
  renderProjectList();
  renderHeader();
}

function appendDirectorJobFailure(session, pending, failure) {
  if (pending.kind === "regenerate") {
    const message = session.messages.find(item => item.id === pending.message_id);
    if (!message) return;
    const variants = ensureDirectorVariants(message);
    variants.push({
      id: makeId("director-response"), text: `Director request failed: ${failure}`,
      proposal: null, proposal_error: "", proposal_state: "", status: "error",
      clarification: null, pending_plan: null, context_usage: null, created_at: Date.now(),
    });
    syncDirectorVariant(message, variants.length - 1);
    return;
  }
  const assistant = {
    id: makeId("director-message"), role: "assistant",
    text: `Director request failed: ${failure}`, proposal_error: "", created_at: Date.now(),
  };
  ensureDirectorVariants(assistant);
  session.messages.push(assistant);
}

function applyDirectorJobResult(session, pending, data) {
  if (pending.kind === "regenerate") {
    const message = session.messages.find(item => item.id === pending.message_id);
    if (!message) return;
    const variants = ensureDirectorVariants(message);
    variants.push({
      id: makeId("director-response"), text: String(data.message || "").trim() || "No response.",
      proposal: data.proposal || null, proposal_error: String(data.proposal_error || ""),
      proposal_state: "", status: String(data.status || "ready"),
      clarification: data.clarification || null, pending_plan: data.pending_plan || null,
      context_usage: data.context_usage || null, intent_route: String(data.intent_route || ""),
      intent_warning: String(data.intent_warning || ""), created_at: Date.now(),
    });
    syncDirectorVariant(message, variants.length - 1);
  } else {
    const assistant = {
      id: makeId("director-message"), role: "assistant", text: String(data.message || "").trim() || "No response.",
      proposal: data.proposal || null, proposal_error: String(data.proposal_error || ""),
      status: String(data.status || "ready"), clarification: data.clarification || null,
      pending_plan: data.pending_plan || null, context_usage: data.context_usage || null,
      intent_route: String(data.intent_route || ""), intent_warning: String(data.intent_warning || ""),
      created_at: Date.now(),
    };
    ensureDirectorVariants(assistant);
    session.messages.push(assistant);
  }
  session.last_context_usage = data.context_usage || null;
  session.pending_plan = data.pending_plan || null;
  if (data.status !== "needs_clarification") session.draft_attachments = [];
}

function runDirectorSessionJob(sessionId) {
  if (state.directorJobs.has(sessionId)) return state.directorJobs.get(sessionId);
  const session = directorSessions()[sessionId];
  const pending = session?.pending_job;
  if (!pending?.job_id || !pending.request) return null;
  const operation = (async () => {
    try {
      const data = await requestDirectorResponse(pending, sessionId);
      const current = directorSessions()[sessionId];
      if (!current || current.pending_job?.job_id !== pending.job_id) return;
      const project = state.projects.find(item => item.id === pending.project_id);
      const groundingChanged = project
        ? cacheDirectorVisionObservations(project, pending.attachments || [], data.vision_observations)
        : false;
      applyDirectorJobResult(current, pending, data);
      if (groundingChanged) markProjectChanged({ project });
    } catch (error) {
      const current = directorSessions()[sessionId];
      if (!current || current.pending_job?.job_id !== pending.job_id) return;
      appendDirectorJobFailure(current, pending, error.message || String(error));
    } finally {
      const current = directorSessions()[sessionId];
      if (current?.pending_job?.job_id === pending.job_id) {
        current.pending_job = null;
        current.updated_at = Date.now();
        persistDirectorSessions();
      }
      state.directorJobs.delete(sessionId);
      renderDirectorJobState(sessionId);
    }
  })();
  state.directorJobs.set(sessionId, operation);
  renderDirectorJobState(sessionId);
  return operation;
}

async function cancelDirectorSessionJob(projectId, scope) {
  const sessionId = directorSessionId(projectId, scope);
  const session = directorSessions()[sessionId];
  const pending = session?.pending_job;
  if (!session || !pending?.job_id) return;
  const cancelledAt = Date.now();
  if (pending.kind === "regenerate") {
    const message = session.messages.find(item => item.id === pending.message_id);
    if (message) {
      const variants = ensureDirectorVariants(message);
      variants.push({
        id: makeId("director-response"), text: "Cancelled.", proposal: null,
        proposal_error: "", proposal_state: "", status: "cancelled",
        clarification: null, pending_plan: null, context_usage: null, created_at: cancelledAt,
      });
      syncDirectorVariant(message, variants.length - 1);
    }
  } else {
    const assistant = {
      id: makeId("director-message"), role: "assistant", text: "Cancelled.",
      proposal: null, proposal_error: "", status: "cancelled", created_at: cancelledAt,
    };
    ensureDirectorVariants(assistant);
    session.messages.push(assistant);
  }
  session.pending_job = null;
  session.updated_at = cancelledAt;
  persistDirectorSessions();
  renderDirectorJobState(sessionId);
  try {
    await api.fetchApi(`${DIRECTOR_CHAT_ENDPOINT}/${encodeURIComponent(pending.job_id)}/cancel`, { method: "POST" });
  } catch (_) {
    // The local cancelled state remains authoritative while the server reconnects.
  }
}

function resumeDirectorJobs() {
  for (const [sessionId, session] of Object.entries(directorSessions())) {
    if (session?.pending_job?.job_id && session.pending_job.request) runDirectorSessionJob(sessionId);
  }
}

function sendDirectorMessage() {
  if (!state.apiConnected) {
    setStatus("ComfyUI disconnected — Video Studio is frozen.", "error");
    return;
  }
  const dialog = state.directorDialog;
  const project = activeProject();
  const shot = selectedShot(project);
  const input = dialog?.querySelector("#psvstudio-director-input");
  let messageText = String(input?.value || "").trim();
  const scope = state.directorScope === "project" ? "project" : "shot";
  const projectScope = scope === "project";
  if (!dialog || !project || (!projectScope && !shot) || directorSessionBusy(project.id, scope)) return;
  const session = directorSession(project.id, scope);
  const sessionId = directorSessionId(project.id, scope);
  const attachments = directorRequestAttachments(project, session.draft_attachments);
  if (!messageText && !attachments.length) return;
  if (!messageText) messageText = projectScope
    ? "Inspect the attached image in the context of the entire video. Suggest concrete production improvements and propose multi-shot changes when useful."
    : "Inspect the attached image and suggest concrete improvements for the selected shot. Propose descriptive shot changes when useful.";
  commitDirectorImageReferences(project, attachments);
  session.messages.push({
    id: makeId("director-message"), role: "user", text: messageText,
    attachments, created_at: Date.now(),
  });
  const jobId = globalThis.crypto?.randomUUID?.() || makeId("director-job");
  session.pending_job = {
    job_id: jobId, kind: "send", project_id: project.id, scope,
    attachments: clone(attachments), progress: "Consulting the local Director...", created_at: Date.now(),
  };
  session.pending_job.request = directorRequestPayload(
    project, shot, scope, attachments, session.messages, jobId,
  );
  session.updated_at = Date.now();
  input.value = "";
  persistDirectorSessions();
  runDirectorSessionJob(sessionId);
}

function selectDirectorResponse(messageId, variantIndex) {
  if (state.directorBusy || directorSessionBusy()) return;
  const session = directorSession();
  const message = session.messages.find(item => item.id === messageId);
  const variants = ensureDirectorVariants(message);
  if (!variants[variantIndex]) return;
  const variant = syncDirectorVariant(message, variantIndex);
  session.last_context_usage = variant.context_usage || null;
  session.updated_at = Date.now();
  persistDirectorSessions();
  renderDirectorDialog();
  const status = state.directorDialog?.querySelector("#psvstudio-director-status");
  if (status) status.textContent = `Showing answer ${variantIndex + 1} of ${variants.length}.`;
}

function regenerateDirectorResponse(messageId) {
  const dialog = state.directorDialog;
  const project = activeProject();
  const shot = selectedShot(project);
  const scope = state.directorScope === "project" ? "project" : "shot";
  const projectScope = scope === "project";
  const session = directorSession(project?.id, scope);
  const sessionId = directorSessionId(project?.id, scope);
  const messageIndex = session?.messages?.findIndex(message => message.id === messageId) ?? -1;
  const message = session?.messages?.[messageIndex];
  const userMessage = session?.messages?.[messageIndex - 1];
  if (
    !dialog || !project || (!projectScope && !shot) || directorSessionBusy(project.id, scope)
    || messageIndex !== session.messages.length - 1
    || message?.role !== "assistant" || userMessage?.role !== "user"
  ) return;
  const attachments = directorAttachmentMetadata(userMessage.attachments).filter(attachment => attachment.path);
  const requestMessages = session.messages.slice(0, messageIndex);
  const jobId = globalThis.crypto?.randomUUID?.() || makeId("director-job");
  session.pending_job = {
    job_id: jobId, kind: "regenerate", message_id: message.id,
    project_id: project.id, scope, attachments: clone(attachments),
    progress: "Processing the Director answer again...", created_at: Date.now(),
  };
  session.pending_job.request = directorRequestPayload(
    project, shot, scope, attachments, requestMessages, jobId,
  );
  session.updated_at = Date.now();
  persistDirectorSessions();
  runDirectorSessionJob(sessionId);
}

async function applyDirectorProposal(messageId) {
  const project = activeProject();
  const session = directorSession(project?.id);
  const message = session.messages.find(item => item.id === messageId);
  if (!project || !message?.proposal || state.directorBusy || directorSessionBusy()) return;
  state.directorBusy = true;
  renderDirectorDialog();
  try {
    const response = await api.fetchApi(DIRECTOR_PREVIEW_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ document: project.document, proposal: message.proposal }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "The Director proposal could not be applied.");
    closeShotEditor({ force: true });
    const selectedId = state.selectedShotId;
    project.document = data.document;
    project.brief = project.document.main_description || "";
    state.selectedShotId = project.document.shots.some(item => item.id === selectedId) ? selectedId : project.document.shots[0]?.id;
    const variant = selectedDirectorVariant(message);
    message.proposal_state = "applied";
    if (variant) variant.proposal_state = "applied";
    session.updated_at = Date.now();
    persistDirectorSessions();
    markProjectChanged({ render: true });
    setStatus(`${message.proposal.summary} Applied after document validation.`, "ready");
    if (state.directorDialog?.open) state.directorDialog.close();
  } catch (error) {
    const variant = selectedDirectorVariant(message);
    message.proposal_error = error.message || String(error);
    if (variant) variant.proposal_error = message.proposal_error;
    session.updated_at = Date.now();
    persistDirectorSessions();
    setStatus(message.proposal_error, "error");
  } finally {
    state.directorBusy = false;
    renderDirectorDialog();
  }
}

function discardDirectorProposal(messageId) {
  const session = directorSession();
  const message = session.messages.find(item => item.id === messageId);
  if (!message) return;
  const variant = selectedDirectorVariant(message);
  message.proposal_state = "discarded";
  if (variant) variant.proposal_state = "discarded";
  session.updated_at = Date.now();
  persistDirectorSessions();
  renderDirectorDialog();
}

function mediaKind(file) {
  if (file.type?.startsWith("image/")) return "image";
  if (file.type?.startsWith("video/")) return "video";
  if (file.type?.startsWith("audio/")) return "audio";
  const extension = file.name?.split(".").pop()?.toLowerCase();
  if (["png", "jpg", "jpeg", "webp", "gif", "bmp", "tif", "tiff"].includes(extension)) return "image";
  if (["mp4", "webm", "mov", "mkv", "avi", "m4v"].includes(extension)) return "video";
  if (["wav", "wave", "mp3", "flac", "ogg", "oga", "opus", "m4a", "aac", "aif", "aiff", "wma", "caf", "au"].includes(extension)) return "audio";
  return "";
}

function roundHalfEven(value) {
  const lower = Math.floor(value);
  const fraction = value - lower;
  if (Math.abs(fraction - 0.5) < 1e-10) return lower % 2 ? lower + 1 : lower;
  return Math.round(value);
}

function minimaxCanvasDimensions(sourceWidth, sourceHeight, targetMegapixels = null) {
  const width = Number(sourceWidth);
  const height = Number(sourceHeight);
  if (!(width > 0) || !(height > 0)) return null;
  const rules = state.config?.canvas || {};
  const multiple = Number(rules.multiple) || 32;
  const minimumMegapixels = Number(rules.minimum_megapixels) || 0.1;
  const maximumMegapixels = Number(rules.maximum_megapixels) || 4;
  const defaultMegapixels = Number(rules.default_megapixels) || (768 * 1344) / 1_000_000;
  const megapixels = Math.min(maximumMegapixels, Math.max(
    minimumMegapixels,
    Number(targetMegapixels) || defaultMegapixels,
  ));
  const scale = Math.sqrt((megapixels * 1_000_000) / (width * height));
  const targetWidth = width * scale;
  const targetHeight = height * scale;
  return {
    width: Math.max(multiple, roundHalfEven(targetWidth / multiple) * multiple),
    height: Math.max(multiple, roundHalfEven(targetHeight / multiple) * multiple),
  };
}

function visualMediaDimensions(url, kind) {
  if (!url || !["image", "video"].includes(kind)) return Promise.resolve(null);
  return new Promise(resolve => {
    const media = kind === "image" ? new Image() : document.createElement("video");
    let settled = false;
    const finish = dimensions => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      media.onload = null;
      media.onerror = null;
      media.onloadedmetadata = null;
      resolve(dimensions);
    };
    const timer = setTimeout(() => finish(null), 5000);
    media.onerror = () => finish(null);
    if (kind === "image") {
      media.onload = () => finish({ width: media.naturalWidth, height: media.naturalHeight });
    } else {
      media.preload = "metadata";
      media.muted = true;
      media.onloadedmetadata = () => finish({ width: media.videoWidth, height: media.videoHeight });
    }
    media.src = url;
  });
}

async function fileMediaDimensions(file, kind) {
  if (!["image", "video"].includes(kind)) return null;
  const url = URL.createObjectURL(file);
  try {
    return await visualMediaDimensions(url, kind);
  } finally {
    URL.revokeObjectURL(url);
  }
}

function preferredCanvasReference(project) {
  const references = project?.document?.references || [];
  for (const role of ["first_frame", "video_edit", "video_continue", "last_frame"]) {
    const reference = references.find(item =>
      (item.roles || []).includes(role) && minimaxCanvasDimensions(
        item.source_width, item.source_height, project.document.target_megapixels));
    if (reference) return reference;
  }
  return null;
}

function useReferenceCanvas(project, reference) {
  const canvas = minimaxCanvasDimensions(
    reference?.source_width,
    reference?.source_height,
    project?.document?.target_megapixels,
  );
  if (!project || !reference || !canvas) return false;
  project.document.width = canvas.width;
  project.document.height = canvas.height;
  project.document.canvas_reference_id = reference.id;
  return true;
}

function synchronizeGeometryCanvas(project) {
  const reference = preferredCanvasReference(project);
  if (reference) return useReferenceCanvas(project, reference);
  project.document.canvas_reference_id = "";
  return false;
}

async function ensureReferenceDimensions(project, reference) {
  if (!["image", "video"].includes(reference?.kind) ||
      minimaxCanvasDimensions(reference.source_width, reference.source_height) ||
      state.mediaDimensionLoads.has(reference.id)) return;
  state.mediaDimensionLoads.add(reference.id);
  const dimensions = await visualMediaDimensions(mediaInputUrl(reference), reference.kind);
  if (!dimensions?.width || !dimensions?.height) return;
  reference.source_width = dimensions.width;
  reference.source_height = dimensions.height;
  if (project.document.canvas_reference_id === reference.id) useReferenceCanvas(project, reference);
  markProjectChanged({ render: true });
}

function isFileDrag(event) {
  return Array.from(event.dataTransfer?.types || []).includes("Files");
}

async function uploadMediaFile(file) {
  const subtype = String(file?.type || "").split("/")[1]?.split(/[;+]/)[0];
  const extension = subtype === "jpeg" ? "jpg" : subtype || "bin";
  const filename = file?.name || `pasted-media-${makeId("upload")}.${extension}`;
  const form = new FormData();
  form.append("image", file, filename);
  form.append("type", "input");
  form.append("overwrite", "false");
  const response = await api.fetchApi("/upload/image", { method: "POST", body: form });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.error || `Upload failed (${response.status}).`);
  return result.subfolder ? `${result.subfolder}/${result.name}` : result.name;
}

async function probeAudioPath(path) {
  const response = await api.fetchApi(AUDIO_PROBE_ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !Number(data.duration_seconds)) {
    throw new Error(data.error || "The audio duration could not be read.");
  }
  return data;
}

async function ensureAudioReferenceDuration(project, reference) {
  if (reference?.kind !== "audio" || Number(reference.duration_seconds) > 0
      || state.mediaDurationLoads.has(reference.id)) return;
  state.mediaDurationLoads.add(reference.id);
  try {
    const metadata = await probeAudioPath(reference.path);
    reference.duration_seconds = Number(metadata.duration_seconds) || 0;
    reference.audio_codec = metadata.codec || "";
    reference.audio_sample_rate = Number(metadata.sample_rate) || 0;
    markProjectChanged({ project, render: project.id === state.activeProjectId });
    if (state.shotEditorDialog?.open) renderShotEditorDialog();
  } catch (_) {
    // Unsupported or temporarily unavailable media remains placeable; the
    // final mixer reports a precise decode error if generation is requested.
  }
}

function mediaInputUrl(reference) {
  const parts = String(reference?.path || "").replaceAll("\\", "/").split("/").filter(Boolean);
  const filename = parts.pop();
  if (!filename) return "";
  const params = new URLSearchParams({ filename, subfolder: parts.join("/"), type: "input" });
  return `/view?${params}`;
}

function defaultReferenceRoles(project, kind) {
  if (kind === "audio") return ["audio_reference"];
  if (kind === "image" && !(project.document.references || []).some(reference => (reference.roles || []).includes("first_frame"))) {
    return ["first_frame"];
  }
  return ["subject"];
}

function referenceRoleOptions(kind) {
  const shared = [
    { value: "subject", label: "Subject / identity" },
    { value: "scene", label: "Scene / environment" },
    { value: "style", label: "Visual style" },
    { value: "action", label: "Action / motion" },
    { value: "pose", label: "Pose" },
    { value: "camera", label: "Camera / composition" },
    { value: "storyboard", label: "Storyboard" },
  ];
  if (kind === "image") return [
    { value: "first_frame", label: "First frame" },
    { value: "last_frame", label: "Last frame" },
    ...shared,
  ];
  if (kind === "video") return [
    ...shared,
    { value: "video_edit", label: "Video editing source" },
    { value: "video_continue", label: "Video continuation" },
  ];
  return [
    { value: "audio_reference", label: "Audio reference" },
    { value: "audio_copy", label: "Copy audio" },
    { value: "exact_audio", label: "Exact timeline audio" },
  ];
}

function setReferenceRole(project, reference, role) {
  const roleChanged = reference.roles?.length !== 1 || reference.roles[0] !== role;
  const leavingExactAudio = (reference.roles || []).length === 1
    && reference.roles[0] === "exact_audio" && role !== "exact_audio";
  const placedCount = (project.document.shots || []).reduce(
    (count, shot) => count + (shot.audio_clips || []).filter(clip => clip.reference_id === reference.id).length,
    0,
  );
  if (leavingExactAudio && placedCount) {
    state.panel.ownerDocument.defaultView?.alert(
      `This audio has ${placedCount} exact timeline placement${placedCount === 1 ? "" : "s"}. Remove those clips before changing its media role.`,
    );
    markProjectChanged({ render: true });
    return;
  }
  if (["first_frame", "last_frame"].includes(role)) {
    for (const item of project.document.references) {
      if (item.id === reference.id || !(item.roles || []).includes(role)) continue;
      item.roles = item.roles.filter(value => value !== role);
      if (!item.roles.length) item.roles = [item.kind === "audio" ? "audio_reference" : "subject"];
    }
  }
  if (roleChanged) {
    reference.observed_visual_facts = "";
    reference.subject_candidates = [];
  }
  reference.roles = [role];
  invalidateReferenceSemantics(project);
  if (CANVAS_MEDIA_ROLES.has(role)) synchronizeGeometryCanvas(project);
  else if (project.document.canvas_reference_id === reference.id) synchronizeGeometryCanvas(project);
  markProjectChanged({ render: true });
  const displayLabel = referenceDisplayLabel(project.document.references || [], reference);
  setStatus(`${displayLabel} now provides: ${referenceRoleOptions(reference.kind).find(item => item.value === role)?.label || role}.`, "ready");
}

function referenceDisplayLabel(references, reference) {
  const isExact = item => item.kind === "audio"
    && (item.roles || []).length === 1 && item.roles[0] === "exact_audio";
  if (isExact(reference)) {
    const ordinal = references.filter(isExact).findIndex(item => item.id === reference.id) + 1;
    return `Exact audio ${Math.max(1, ordinal)}`;
  }
  const typeName = reference.kind === "image" ? "Picture" : reference.kind === "video" ? "Video" : "Audio";
  const ordinal = references.filter(item => item.kind === reference.kind && !isExact(item)).findIndex(item => item.id === reference.id) + 1;
  return `${typeName} ${Math.max(1, ordinal)}`;
}

function directorAttachmentDisplayLabel(project, attachment, attachments = []) {
  if (attachment.usage === "describe") return "Visual context";
  const references = project?.document?.references || [];
  const existing = references.find(item =>
    item.id === attachment.reference_id || (item.kind === "image" && item.path === attachment.path));
  if (existing) return referenceDisplayLabel(references, existing);
  const pendingPaths = [];
  for (const item of attachments || []) {
    if (item.usage === "describe") continue;
    const committed = references.some(reference =>
      reference.id === item.reference_id || (reference.kind === "image" && reference.path === item.path));
    if (!committed && !pendingPaths.includes(item.path)) pendingPaths.push(item.path);
    if (item === attachment) break;
  }
  const pendingIndex = Math.max(0, pendingPaths.indexOf(attachment.path));
  const imageCount = references.filter(item => item.kind === "image").length;
  return `Picture ${imageCount + pendingIndex + 1}`;
}

function moveReference(project, referenceId, destinationIndex) {
  const references = project.document.references || [];
  const sourceIndex = references.findIndex(item => item.id === referenceId);
  if (sourceIndex < 0) return;
  const [reference] = references.splice(sourceIndex, 1);
  const index = Math.max(0, Math.min(references.length, destinationIndex > sourceIndex ? destinationIndex - 1 : destinationIndex));
  references.splice(index, 0, reference);
  references.forEach(item => { item.label = ""; });
  invalidateReferenceSemantics(project);
  state.mediaDragId = "";
  markProjectChanged({ render: true });
  setStatus("Reference order updated; Picture, Video, and Audio numbering follows the new order.", "ready");
}

function clearMediaDropMarkers(lane) {
  lane?.querySelectorAll(".is-drop-before,.is-drop-after").forEach(card => card.classList.remove("is-drop-before", "is-drop-after"));
}

function mediaDropDestination(lane, clientY) {
  const cards = [...lane.querySelectorAll(".psvstudio-media-chip:not(.is-dragging)")];
  clearMediaDropMarkers(lane);
  for (const card of cards) {
    const bounds = card.getBoundingClientRect();
    if (clientY < bounds.top + bounds.height / 2) {
      card.classList.add("is-drop-before");
      return Number(card.dataset.referenceIndex);
    }
  }
  cards.at(-1)?.classList.add("is-drop-after");
  return (activeProject()?.document?.references || []).length;
}

function removeProjectReference(project, reference, displayLabel = "Media") {
  const dependentClips = (project.document.shots || []).reduce((count, shot) => (
    count + (shot.audio_clips || []).filter(clip => clip.reference_id === reference.id).length
  ), 0);
  const canvasSource = project.document.canvas_reference_id === reference.id;
  const consequences = [
    dependentClips ? `${dependentClips} placed audio clip${dependentClips === 1 ? "" : "s"}` : "",
    canvasSource ? "the current canvas source" : "",
  ].filter(Boolean);
  const detail = consequences.length ? ` This also removes ${consequences.join(" and ")}.` : "";
  const view = state.panel?.ownerDocument.defaultView;
  if (!view?.confirm(`Remove ${displayLabel} from this project?${detail}`)) return false;
  project.document.references = (project.document.references || []).filter(item => item.id !== reference.id);
  for (const shot of project.document.shots || []) {
    shot.audio_clips = (shot.audio_clips || []).filter(clip => clip.reference_id !== reference.id);
  }
  project.document.references.forEach(item => { item.label = ""; });
  invalidateReferenceSemantics(project);
  if (project.document.canvas_reference_id === reference.id) synchronizeGeometryCanvas(project);
  markProjectChanged({ render: true });
  setStatus(`${displayLabel} removed from project references.`, "ready");
  return true;
}

function mediaLimit(project, kind) {
  const limits = state.config?.reference_limits || {};
  const key = kind === "image" ? "images" : kind === "video" ? "videos" : "audio_tracks";
  const references = (project.document.references || []).filter(reference => !(reference.kind === "audio"
    && (reference.roles || []).length === 1 && reference.roles[0] === "exact_audio"));
  if (references.length >= Number(limits.active_items || 12)) return "The project already has the maximum number of active model references.";
  if (references.filter(reference => reference.kind === kind).length >= Number(limits[key] || 12)) {
    return `The project already has the maximum number of ${kind} references.`;
  }
  return "";
}

async function addMediaFiles(fileList) {
  const files = Array.from(fileList || []);
  const supported = files.filter(mediaKind);
  if (!supported.length) {
    setStatus(files.length ? "Drop image, video, or audio files." : "No media files were dropped.", "warning");
    return;
  }
  if (!activeProject()) newProject();
  const project = activeProject();
  if (!project) return;
  if (isStructuredExtensionProject(project)) {
    setStatus("New media references are not yet supported inside native structured extensions.", "warning");
    return;
  }
  let added = 0;
  const errors = [];
  for (const file of supported) {
    const kind = mediaKind(file);
    const limitError = mediaLimit(project, kind);
    if (limitError) {
      errors.push(`${file.name}: ${limitError}`);
      continue;
    }
    try {
      setStatus(`Uploading ${file.name}…`, "working");
      const dimensions = await fileMediaDimensions(file, kind);
      const path = await uploadMediaFile(file);
      project.document.references ||= [];
      const reference = {
        id: makeId("reference"), kind, path, name: file.name || path.split("/").pop(),
        roles: defaultReferenceRoles(project, kind), prompt: "", label: "",
        trim_start: 0, trim_end: null, use_embedded_audio: false,
        source_width: dimensions?.width || 0, source_height: dimensions?.height || 0,
        duration_seconds: 0,
      };
      project.document.references.push(reference);
      if (kind === "audio") {
        try {
          const metadata = await probeAudioPath(path);
          reference.duration_seconds = Number(metadata.duration_seconds) || 0;
          reference.audio_codec = metadata.codec || "";
          reference.audio_sample_rate = Number(metadata.sample_rate) || 0;
        } catch (_) {
          // Model references can still be loaded by ComfyUI at queue time.
        }
      }
      if ((reference.roles || []).some(role => CANVAS_MEDIA_ROLES.has(role))) {
        synchronizeGeometryCanvas(project);
      }
      invalidateReferenceSemantics(project);
      added += 1;
    } catch (error) {
      errors.push(`${file.name}: ${error.message || error}`);
    }
  }
  if (added) markProjectChanged({ render: true });
  const summary = `${added} media file${added === 1 ? "" : "s"} added to project references.`;
  setStatus(errors.length ? `${summary} ${errors.join(" ")}` : summary, errors.length ? "warning" : "ready");
}

function projectDocumentMatchesDefault(project) {
  if (!project?.document || !state.config?.default_document) return false;
  const comparable = value => {
    const document = clone(value);
    for (const shot of document.shots || []) shot.id = "";
    return JSON.stringify(document);
  };
  return comparable(project.document) === comparable(state.config.default_document);
}

function directorProjectSessionHasContent(projectId) {
  return [projectId, `${projectId}:project`].some(sessionId => {
    const session = directorSessions()[sessionId];
    return Boolean(
      session?.messages?.length
      || session?.draft_attachments?.length
      || session?.pending_plan
      || session?.pending_job
    );
  });
}

function projectIsEmpty(project) {
  if (!project) return true;
  return (project.name || "Untitled video") === "Untitled video"
    && !String(project.brief || "").trim()
    && !(project.generations || []).length
    && (project.workflow_id || "") === (state.workflows[0]?.id || "")
    && !directorProjectSessionHasContent(project.id)
    && projectDocumentMatchesDefault(project);
}

function videoStudioStatus() {
  const project = activeProject();
  const open = Boolean(state.ready && state.panel && state.standaloneAttached);
  return {
    instanceId: STUDIO_INSTANCE_ID,
    open,
    openedAt: state.studioOpenedAt,
    activeProjectId: open ? project?.id || "" : "",
    projectName: open && project ? project.name || "Untitled video" : "",
    projectEmpty: open && project ? projectIsEmpty(project) : true,
  };
}

async function completeRelayedHandoff(requestId, result, error = "") {
  await api.fetchApi(`/promptstudio-video/studio-handoff/${encodeURIComponent(requestId)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ result: result || {}, error }),
  });
}

async function processRelayedHandoff(handoff) {
  const requestId = String(handoff?.requestId || "");
  if (!requestId || state.relayedHandoffs.has(requestId)) return;
  state.relayedHandoffs.add(requestId);
  try {
    const result = await handoffPromptStudioImage(handoff.image);
    await completeRelayedHandoff(requestId, result);
  } catch (error) {
    const message = error.message || String(error);
    setStatus(message, "error");
    await completeRelayedHandoff(requestId, {}, message).catch(() => {});
  }
}

function flushServerPresence() {
  if (state.serverPresenceRequest || !state.latestServerPresence) {
    if (state.serverPresenceRequest) state.serverPresenceQueued = true;
    return;
  }
  const presence = state.latestServerPresence;
  state.latestServerPresence = null;
  state.serverPresenceRequest = api.fetchApi("/promptstudio-video/studio-presence", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(presence),
    keepalive: true,
  }).then(async response => {
    if (!response.ok) return;
    const data = await response.json().catch(() => ({}));
    for (const handoff of data.handoffs || []) processRelayedHandoff(handoff);
  }).catch(() => {}).finally(() => {
    state.serverPresenceRequest = null;
    if (state.serverPresenceQueued || state.latestServerPresence) {
      state.serverPresenceQueued = false;
      flushServerPresence();
    }
  });
}

function postVideoStudioPresence() {
  const presence = videoStudioStatus();
  state.lastPresenceSignature = JSON.stringify([
    presence.open,
    presence.activeProjectId,
    presence.projectEmpty,
  ]);
  state.bridgeChannel?.postMessage({ type: "studio-presence", ...presence });
  state.latestServerPresence = presence;
  flushServerPresence();
}

function safeHandoffFilename(value, mimeType) {
  const fallbackExtension = mimeType === "image/jpeg" ? "jpg" : mimeType?.split("/")[1]?.split("+")[0] || "png";
  const filename = String(value || "prompt-studio-image").split(/[\\/]/).pop().replace(/[<>:"|?*\u0000-\u001f]/g, "_").trim();
  return filename && filename.includes(".") ? filename : `${filename || "prompt-studio-image"}.${fallbackExtension}`;
}

async function handoffPromptStudioImage(value) {
  const status = videoStudioStatus();
  const originalProject = activeProject();
  if (!status.open || !originalProject) throw new Error("Video Studio no longer has an open project.");
  if (value?.targetProjectId && value.targetProjectId !== originalProject.id) {
    throw new Error("The open Video Studio project changed before the image handoff started.");
  }
  const createNewProject = value?.targetMode === "new";
  const sourceUrl = new URL(String(value?.url || ""), window.location.href);
  if (sourceUrl.origin !== window.location.origin) throw new Error("Video Studio only accepts same-origin Prompt Studio images.");
  const limitError = createNewProject ? "" : mediaLimit(originalProject, "image");
  if (limitError) throw new Error(limitError);

  setStatus(`Importing ${value?.filename || "Prompt Studio image"}…`, "working");
  const response = await fetch(sourceUrl.href, { credentials: "same-origin" });
  if (!response.ok) throw new Error(`Prompt Studio image could not be read (${response.status}).`);
  const blob = await response.blob();
  if (!blob.type.startsWith("image/")) throw new Error("The Prompt Studio handoff did not contain an image.");
  const file = new File([blob], safeHandoffFilename(value?.filename, blob.type), { type: blob.type });
  const suppliedWidth = Number(value?.width);
  const suppliedHeight = Number(value?.height);
  const dimensions = suppliedWidth > 0 && suppliedHeight > 0
    ? { width: suppliedWidth, height: suppliedHeight }
    : await fileMediaDimensions(file, "image");
  const path = await uploadMediaFile(file);
  if (activeProject()?.id !== originalProject.id) {
    throw new Error("The open Video Studio project changed during the image handoff. Try again in the intended project.");
  }
  const project = createNewProject ? createProjectRecord() : originalProject;
  project.document.references ||= [];
  const reference = {
    id: makeId("reference"), kind: "image", path, name: file.name,
    roles: defaultReferenceRoles(project, "image"), prompt: "", label: "",
    trim_start: 0, trim_end: null, use_embedded_audio: false,
    source_width: dimensions?.width || 0, source_height: dimensions?.height || 0,
  };
  project.document.references.push(reference);
  if (reference.roles.some(role => CANVAS_MEDIA_ROLES.has(role))) synchronizeGeometryCanvas(project);
  invalidateReferenceSemantics(project);
  markProjectChanged({ render: true });
  const displayLabel = referenceDisplayLabel(project.document.references, reference);
  setStatus(`${displayLabel} was added from Prompt Studio.`, "ready");
  return { projectId: project.id, projectName: project.name || "Untitled video", referenceId: reference.id };
}

function clearMediaDrag(doc) {
  state.mediaDropDepth.set(doc, 0);
  doc.body?.classList.remove("psvstudio-media-drag-active");
}

let videoDocumentInteractionController = null;
function installMediaDrop(doc) {
  videoDocumentInteractionController ||= createVideoDocumentInteractionController({ state, isFileDrag,
    clearMediaDrag, addMediaFiles, clipboardImageFiles, closeSystemStatus, closeVideoDrawer });
  videoDocumentInteractionController.mount(doc);
}

function activeProject() {
  return state.projects.find(project => project.id === state.activeProjectId) || null;
}

function isStructuredExtensionProject(project = activeProject()) {
  return Boolean(project?.extension_source?.parent_project_id && project?.extension_source?.parent_generation_id);
}

function selectedShot(project = activeProject()) {
  return project?.document?.shots?.find(shot => shot.id === state.selectedShotId) || project?.document?.shots?.[0] || null;
}

function newActionStep(text = "") {
  return { id: makeId("step"), type: "action", text };
}

function newDialogueStep(shot = null) {
  const existing = ensureShotSteps(shot).filter(step => step.type === "dialogue");
  const previous = existing.at(-1);
  return {
    id: makeId("dialogue"), type: "dialogue", speaker: previous?.speaker || "The speaker",
    speaker_id: previous?.speaker_id || "S1", language: previous?.language || "English",
    performance: previous?.performance || "speech", text: "", delivery: "",
    voiceover: false, offscreen: false, crosses_cut: false, cutoff: false,
  };
}

function ensureShotSteps(shot) {
  if (!shot) return [];
  if (!Array.isArray(shot.steps)) {
    // One-way browser migration for projects cached before ordered steps.
    shot.steps = [];
    if (String(shot.action || "").trim()) shot.steps.push(newActionStep(String(shot.action).trim()));
    for (const event of shot.dialogue || []) shot.steps.push({ ...clone(event), id: event.id || makeId("dialogue"), type: "dialogue" });
  }
  delete shot.action;
  delete shot.dialogue;
  return shot.steps;
}

function syncShotSoundCues(shot, sounds = shot?.sounds || []) {
  if (!shot) return [];
  const pool = Array.isArray(shot.sound_cues) ? shot.sound_cues : [];
  const used = new Set();
  shot.sounds = Array.from(sounds || []).map(value => String(value || "").trim()).filter(Boolean);
  shot.sound_cues = shot.sounds.map((text, index) => {
    const match = pool.find((cue, cueIndex) => !used.has(cueIndex) && String(cue?.text || "").trim() === text);
    if (match) used.add(pool.indexOf(match));
    return { ...(match || {}), id: match?.id || makeId("sound"), text };
  });
  return shot.sound_cues;
}

function shotLocalDuration(project, shotId) {
  const index = project?.document?.shots?.findIndex(shot => shot.id === shotId) ?? -1;
  if (index < 0) return 0;
  return Math.max(1 / 24, Number(
    project.document.shots[index + 1]?.start ?? project.document.duration_seconds,
  ) - Number(project.document.shots[index].start || 0));
}

function ensureShotTimeline(shot, duration) {
  const steps = ensureShotSteps(shot);
  const cues = syncShotSoundCues(shot);
  shot.audio_clips = Array.isArray(shot.audio_clips) ? shot.audio_clips : [];
  const semantic = [...steps, ...cues];
  const legacyOrdered = [...semantic].sort((a, b) => Number(a.start || 0) - Number(b.start || 0));
  const legacyFirstStart = Number(legacyOrdered[0]?.start || 0);
  const legacyCoverage = Number(legacyOrdered.at(-1)?.end || 0) - legacyFirstStart;
  const legacySlot = legacyCoverage / Math.max(legacyOrdered.length, 1);
  const legacyTolerance = Math.max(.002, Math.min(.02, legacySlot / 20));
  const legacyAutoTiming = legacyOrdered.length >= 2
    && Math.abs(legacyFirstStart) <= .02
    && legacyCoverage >= duration * .8
    && legacyOrdered.every(item => !item.timing_explicit
      && Number.isFinite(Number(item.start)) && Number.isFinite(Number(item.end)))
    && legacyOrdered.every((item, index) => (
      Math.abs(Number(item.start) - (legacyFirstStart + index * legacySlot)) <= legacyTolerance
      && Math.abs(Number(item.end) - (legacyFirstStart + (index + 1) * legacySlot)) <= legacyTolerance
    ));
  if (legacyAutoTiming) {
    for (const item of semantic) {
      delete item.start;
      delete item.end;
      delete item.timing_explicit;
    }
  }
  semantic.forEach(item => {
    if (Number.isFinite(Number(item.start)) && Number.isFinite(Number(item.end)) && Number(item.end) > Number(item.start)) {
      item.start = Math.max(0, Math.min(duration - 1 / 24, Number(item.start)));
      item.end = Math.max(item.start + 1 / 24, Math.min(duration, Number(item.end)));
      return;
    }
    delete item.start;
    delete item.end;
    delete item.timing_explicit;
  });
  for (const clip of shot.audio_clips) {
    clip.start = Math.max(0, Math.min(duration - 1 / 1000, Number(clip.start) || 0));
    clip.end = Math.max(clip.start + 1 / 1000, Math.min(duration, Number(clip.end) || Math.min(duration, clip.start + 1)));
    clip.source_start = Math.max(0, Number(clip.source_start) || 0);
    clip.source_end = clip.source_end == null || clip.source_end === "" ? null : Math.max(clip.source_start, Number(clip.source_end));
    clip.gain_db = Math.max(-60, Math.min(24, Number(clip.gain_db) || 0));
    clip.fade_in = Math.max(0, Number(clip.fade_in) || 0);
    clip.fade_out = Math.max(0, Number(clip.fade_out) || 0);
    clip.mix_mode = clip.mix_mode === "replace" ? "replace" : "overlay";
  }
  return { steps, cues, clips: shot.audio_clips };
}

function clampExistingShotTimeline(shot, duration) {
  const clamp = (item, minimum) => {
    if (!Number.isFinite(Number(item?.start)) || !Number.isFinite(Number(item?.end))) return;
    item.start = Math.max(0, Math.min(duration - minimum, Number(item.start)));
    item.end = Math.max(item.start + minimum, Math.min(duration, Number(item.end)));
  };
  ensureShotSteps(shot).forEach(item => clamp(item, 1 / 24));
  (shot.sound_cues || []).forEach(item => clamp(item, 1 / 24));
  (shot.audio_clips || []).forEach(item => clamp(item, .001));
}

function shotStepSummary(shot) {
  const steps = ensureShotSteps(shot);
  const first = steps.find(step => String(step.text || "").trim());
  if (!first) return shot?.composition || "Empty shot";
  return first.type === "dialogue"
    ? `${first.speaker_id || "S1"}: ${first.text}`
    : first.text;
}

function localResolvedMode(document) {
  if (document?.mode && document.mode !== "auto") return document.mode;
  const references = (document?.references || []).filter(reference => !(reference.kind === "audio"
    && (reference.roles || []).length === 1 && reference.roles[0] === "exact_audio"));
  const roles = new Set(references.flatMap(reference => reference.roles || []));
  if (references.some(reference => ["video", "audio"].includes(reference.kind))) return "ref2va";
  if ([...roles].some(role => !["first_frame", "last_frame"].includes(role))) return "ref2va";
  if (roles.has("first_frame") && roles.has("last_frame")) return "fl2va";
  if (roles.has("first_frame")) return "i2va";
  if (roles.has("last_frame")) return "l2va";
  return "t2va";
}

function selectedWorkflow(project) {
  return state.workflows.find(workflow => workflow.id === project?.workflow_id) || null;
}

function workflowTurboNode(workflow) {
  return Object.values(workflow?.snapshot?.output || {})
    .find(node => node?.class_type === TURBO_PROFILE_TYPE) || null;
}

function resolvedTurboProfile(project) {
  const workflow = selectedWorkflow(project);
  if (!workflow) {
    return {
      kind: "missing",
      badge: "NO WORKFLOW",
      heading: "No generation workflow selected",
      detail: "Choose a workflow to see its sampling profile.",
      loraName: "",
    };
  }

  const turboNode = workflowTurboNode(workflow);
  if (!turboNode) {
    return {
      kind: "standard",
      badge: "STANDARD 20-STEP",
      heading: "Standard MiniMax H3 profile",
      detail: "20 steps | shifts V11 / A4 | no Turbo LoRA",
      loraName: "",
    };
  }

  const inputs = turboNode.inputs || {};
  if (inputs.enabled === false || inputs.enabled === "false") {
    return {
      kind: "standard",
      badge: "TURBO DISABLED",
      heading: "Turbo profile disabled",
      detail: "20 steps | shifts V11 / A4 | no Turbo LoRA",
      loraName: "",
    };
  }

  const mode = localResolvedMode(project.document);
  const width = Number(project.document.width);
  const height = Number(project.document.height);
  const preset = String(inputs.preset || "auto_quality");
  const profile = turboDisplayProfile(mode, width, height, preset);

  const loraPath = String(inputs[profile.input] || "");
  return {
    kind: "turbo",
    badge: `${profile.steps}-STEP TURBO`,
    heading: `${profile.label} - ${profile.steps} steps`,
    detail: `${mode.toUpperCase()} | ${width}x${height} | shifts V${profile.shiftVideo} / A${profile.shiftAudio} | ${preset}`,
    loraName: loraPath.replaceAll("\\", "/").split("/").pop() || "Configured Turbo LoRA",
  };
}

function renderTurboProfileIndicator(project, compact = false) {
  const profile = resolvedTurboProfile(project);
  if (compact) {
    const badge = el("span", `psvstudio-turbo-badge is-${profile.kind}`, profile.badge);
    badge.title = `${profile.heading}. ${profile.detail}${profile.loraName ? `. ${profile.loraName}` : ""}`;
    return badge;
  }

  const indicator = el("div", `psvstudio-turbo-profile is-${profile.kind}`);
  const copy = el("div", "psvstudio-turbo-profile-copy");
  copy.append(
    el("strong", "", profile.heading),
    el("span", "", profile.detail),
  );
  indicator.append(copy);
  if (profile.loraName) {
    const filename = el("small", "", profile.loraName);
    filename.title = profile.loraName;
    indicator.append(filename);
  }
  return indicator;
}

function setStatus(message, kind = "") {
  const status = state.panel?.querySelector("#psvstudio-status");
  if (!status) return;
  status.textContent = message;
  status.dataset.kind = kind;
}

function closeSystemStatus({ restoreFocus = false } = {}) {
  const control = state.panel?.querySelector("#psvstudio-kobold-control");
  if (!control?.open) return false;
  control.open = false;
  if (restoreFocus) control.querySelector("summary")?.focus({ preventScroll: true });
  return true;
}

function closeVideoDrawer({ restoreFocus = false } = {}) {
  if (!state.drawer) return false;
  const drawer = state.drawer;
  state.drawer = "";
  if (state.panel) {
    state.panel.dataset.drawer = "";
    state.panel.querySelector("#psvstudio-mobile-projects")?.setAttribute("aria-expanded", "false");
    state.panel.querySelector("#psvstudio-mobile-inspector")?.setAttribute("aria-expanded", "false");
  }
  if (restoreFocus) {
    const trigger = drawer === "projects" ? "#psvstudio-mobile-projects" : "#psvstudio-mobile-inspector";
    state.panel?.querySelector(trigger)?.focus({ preventScroll: true });
  }
  return true;
}

function installTransientUiDismissal(ownerDocument) {
  installMediaDrop(ownerDocument);
}

function isVideoStudioControl(control) {
  return Boolean(
    control?.matches?.(DISCONNECTED_CONTROL_SELECTOR)
    && control.closest?.(DISCONNECTED_CONTROL_SCOPE_SELECTOR),
  );
}

function isDisconnectedAllowedControl(control) {
  if (control?.dataset?.psvstudioAllowDisconnected === "true") return true;
  if (!control?.closest?.("dialog")) return false;
  const label = String(control.getAttribute?.("aria-label") || control.title || control.textContent || "").trim();
  return /^(close|cancel|done)\b/i.test(label);
}

function freezeDisconnectedControls(root = state.panel) {
  if (state.apiConnected || !root) return;
  const controls = [
    ...(isVideoStudioControl(root) ? [root] : []),
    ...root.querySelectorAll(DISCONNECTED_CONTROL_SELECTOR),
  ];
  controls.forEach(control => {
    if (!isVideoStudioControl(control) || isDisconnectedAllowedControl(control)) return;
    if (!state.disconnectedControls.has(control)) {
      state.disconnectedControls.set(control, control.disabled);
    } else if (!control.disabled) {
      state.disconnectedControls.set(control, false);
    }
    control.disabled = true;
  });
}

function setSaveState(message) {
  const status = state.panel?.querySelector("#psvstudio-save-state");
  if (status) status.textContent = message;
}

function markProjectChanged({ render = false, project = activeProject() } = {}) {
  if (project?.pending_generation_restore && !pendingGenerationRestore(project)) delete project.pending_generation_restore;
  if (project) project.updated_at = Date.now();
  state.projectMutation += 1;
  projectDraftScheduler.schedule();
  const presence = videoStudioStatus();
  const presenceSignature = JSON.stringify([
    presence.open,
    presence.activeProjectId,
    presence.projectEmpty,
  ]);
  if (presenceSignature !== state.lastPresenceSignature) postVideoStudioPresence();
  setSaveState("Saving…");
  if (state.projectSaveTimer) clearTimeout(state.projectSaveTimer);
  state.projectSaveTimer = setTimeout(() => persistProjects(), 450);
  if (render) renderAll();
}

// Arrays with IDs represent records. Shots and existing generations are atomic:
// a conflict must never assemble a new shot or historical snapshot from two edits.
function mergeProjectVersions(base, local, remote, conflicts = [], path = "projects", choices = null) {
  if (choices?.has(path)) return clone(choices.get(path) === "local" ? local : remote);
  const equal = (left, right) => JSON.stringify(left) === JSON.stringify(right);
  if (equal(local, remote) || equal(remote, base)) return clone(local);
  if (equal(local, base)) return clone(remote);
  if (path.endsWith(".updated_at")) return Math.max(Number(local) || 0, Number(remote) || 0);
  const objects = [base, local, remote].every(value => value && typeof value === "object" && !Array.isArray(value));
  const atomic = /\.(shots|generations)\[[^\]]+\]$/.test(path);
  if (objects && !atomic) {
    const result = {};
    for (const key of new Set([...Object.keys(base), ...Object.keys(local), ...Object.keys(remote)])) {
      const value = mergeProjectVersions(base[key], local[key], remote[key], conflicts, `${path}.${key}`, choices);
      if (value !== undefined) result[key] = value;
    }
    return result;
  }
  const records = [base, local, remote].every(value => Array.isArray(value)
    && value.every(item => item && typeof item.id === "string")
    && new Set(value.map(item => item.id)).size === value.length);
  if (records) {
    const maps = [base, local, remote].map(items => new Map(items.map(item => [item.id, item])));
    const ids = [...new Set([...local, ...remote, ...base].map(item => item.id))];
    // Shot order carries timing semantics; divergent reordering needs review.
    const order = values => values.filter(item => maps[0].has(item.id)).map(item => item.id);
    if (path.endsWith(".shots") && !equal(order(local), order(base))
      && !equal(order(remote), order(base)) && !equal(order(local), order(remote))) {
      conflicts.push({ path, local: clone(local), remote: clone(remote) });
      return clone(local);
    }
    const orderedIds = path.endsWith(".shots") && equal(order(local), order(base))
      ? [...new Set([...remote, ...local, ...base].map(item => item.id))] : ids;
    return orderedIds.map(id => mergeProjectVersions(...maps.map(map => map.get(id)), conflicts, `${path}[${id}]`, choices))
      .filter(value => value !== undefined);
  }
  conflicts.push({ path, local: clone(local), remote: clone(remote) });
  return clone(local);
}

function projectDraftKey() {
  if (!state.projectDraftKey) {
    const key = "promptstudio.video.projects.draft-tab.v1";
    const id = sessionStorage.getItem(key) || makeId("draft");
    sessionStorage.setItem(key, id);
    state.projectDraftKey = `promptstudio.video.projects.draft.v1.${id}`;
  }
  return state.projectDraftKey;
}

function writeProjectDraft() {
  projectDraftScheduler.cancel();
  const record = {version:1, mutation:state.projectMutation,
    base:clone(state.projectBase), projects:clone(state.projects), revision:state.projectRevision,
    active_project_id:state.activeProjectId, saved_at:Date.now()};
  videoDraftPending = videoDraftOutbox.put(draftTabKey("video"),record).then(() => {
    state.projectDraftError = "";
    return true;
  }).catch(error => {
    state.projectDraftError = `Browser draft could not be stored: ${error.message || error}`;
    showDraftStorageFailure(state.panel?.querySelector(".psvstudio-sidebar"),record,state.projectDraftError);
    return false;
  });
  try {
    localStorage.setItem(projectDraftKey(), JSON.stringify(record));
    state.projectDraftError = "";
    return true;
  } catch (error) {
    state.projectDraftError = `Browser draft could not be stored: ${error.message || error}`;
    return false;
  }
}

// Keep objects held by running preparations/pollers attached to the live store.
function applyProjectMerge(projects) {
  const reconcile = (current, next) => {
    if (Array.isArray(next)) {
      const previous = new Map((Array.isArray(current) ? current : []).filter(item => item?.id).map(item => [item.id, item]));
      return next.map(item => item?.id ? reconcile(previous.get(item.id), item) : clone(item));
    }
    if (next && typeof next === "object") {
      const target = current && typeof current === "object" && !Array.isArray(current) ? current : {};
      for (const key of Object.keys(target)) if (!(key in next)) delete target[key];
      for (const [key, value] of Object.entries(next)) target[key] = reconcile(target[key], value);
      return target;
    }
    return next;
  };
  state.projects = reconcile(state.projects, projects);
  if (!state.projects.some(project => project.id === state.activeProjectId)) state.activeProjectId = state.projects[0]?.id || null;
}

function showProjectSaveFailure(error) {
  const durable = writeProjectDraft();
  setSaveState(state.projectConflicts.length ? "Save conflict" : "Save failed");
  setStatus(`${error.message || error} ${durable ? "Local draft retained." : state.projectDraftError}`, "error");
  const status = state.panel?.querySelector("#psvstudio-save-state");
  if (!status) return;
  const button = status.ownerDocument.createElement("button");
  button.textContent = state.projectConflicts.length ? "Review conflicts" : "Retry save";
  button.addEventListener("click", () => state.projectConflicts.length ? reviewProjectConflicts() : persistProjects());
  status.append(" ", button);
}

async function reviewProjectConflicts() {
  const view = state.panel?.ownerDocument.defaultView;
  if (!view || !state.projectConflicts.length) return;
  // Review against a fresh server version. Cancellation leaves the draft/base intact.
  try {
    const remote = await fetchProjectStore();
    const conflicts = [];
    const reconciled = mergeProjectVersions(state.projectBase, state.projects, remote.projects, conflicts);
    if (!conflicts.length) {
      applyProjectMerge(reconciled);
      state.projectBase = clone(remote.projects);
      state.projectRevision = Number(remote.revision || 0);
      state.projectConflicts = [];
      writeProjectDraft();
      renderAll();
      await persistProjects();
      return;
    }
    const choices = new Map();
    for (const conflict of conflicts) {
      const display = value => value === undefined ? "(deleted)" : JSON.stringify(value, null, 2);
      const doc = state.panel.ownerDocument;
      const dialog = doc.createElement("dialog");
      dialog.className = "psvstudio-save-conflict";
      dialog.setAttribute("aria-label", `Conflicting edits: ${conflict.path}`);
      dialog.style.cssText = "max-width:min(900px,90vw);max-height:90vh;overflow:auto";
      const heading = doc.createElement("h2");
      heading.textContent = conflict.path.endsWith(".name") ? "Conflicting project names"
        : conflict.path.includes(".shots[") ? "Conflicting shot edits"
          : conflict.path.includes(".generations[") ? "Conflicting generation records" : "Conflicting project edits";
      dialog.append(heading);
      for (const [label, value] of [["Your draft", conflict.local], ["Server version", conflict.remote]]) {
        const details = doc.createElement("details");
        details.open = true;
        const summary = doc.createElement("summary");
        summary.textContent = label;
        const pre = doc.createElement("pre");
        pre.textContent = display(value);
        pre.style.cssText = "white-space:pre-wrap;max-height:35vh;overflow:auto";
        details.append(summary, pre);
        dialog.append(details);
      }
      const choice = await new Promise(resolve => {
        for (const [label, value] of [["Keep my version", "local"], ["Use server version", "remote"], ["Cancel", "cancel"]]) {
          const button = doc.createElement("button");
          button.textContent = label;
          button.addEventListener("click", () => { resolve(value); dialog.close(); });
          dialog.append(button);
        }
        dialog.addEventListener("cancel", () => resolve("cancel"), { once: true });
        doc.body.append(dialog);
        dialog.showModal();
      });
      dialog.remove();
      if (choice === "cancel") return;
      choices.set(conflict.path, choice);
    }
    // Only resolve the exact values reviewed; editing during review requires a fresh review.
    const current = [];
    mergeProjectVersions(state.projectBase, state.projects, remote.projects, current);
    if (JSON.stringify(current) !== JSON.stringify(conflicts)) throw new Error("Projects changed during review. Review conflicts again.");
    applyProjectMerge(mergeProjectVersions(state.projectBase, state.projects, remote.projects, [], "projects", choices));
    state.projectBase = clone(remote.projects);
    state.projectRevision = Number(remote.revision || 0);
    state.projectConflicts = [];
    state.projectMutation += 1;
    writeProjectDraft();
    renderAll();
    await persistProjects();
  } catch (error) { showProjectSaveFailure(error); }
}

async function fetchProjectStore({ page = false, cursor = null } = {}) {
  const params = new URLSearchParams();
  if (page) {
    params.set("limit", "20");
    params.set("include_active", "1");
    params.set("include_pending", "1");
    if (cursor) {
      params.set("before_updated", String(cursor.updated_at));
      params.set("before_created", String(cursor.created_at));
      params.set("before_id", cursor.id);
    }
  }
  const url = `${PROJECTS_ENDPOINT}${params.size ? `?${params}` : ""}`;
  let response = await api.fetchApi(url);
  let data = await response.json().catch(() => ({}));
  if (!response.ok || !Array.isArray(data.projects)) throw new Error(data.error || "Video projects could not be loaded.");
  if (await prepareHistoryIndex(data, state.panel?.querySelector('.psvstudio-sidebar'), PROJECTS_ENDPOINT,
      async () => { await loadProjects(); renderAll(); resumeGenerationPolling(); })) {
    response = await api.fetchApi(url);
    data = await response.json().catch(() => ({}));
    if (!response.ok || !Array.isArray(data.projects)) throw new Error(data.error || "Video projects could not be loaded.");
  }
  requireHistoryIndex(data, state.panel?.querySelector('.psvstudio-sidebar'), PROJECTS_ENDPOINT, async () => { await loadProjects(); renderAll(); resumeGenerationPolling(); });
  return data;
}

async function persistProjects({ immediate = false } = {}) {
  if (state.projectSaveTimer) clearTimeout(state.projectSaveTimer);
  state.projectSaveTimer = null;
  if (!immediate && state.projectMutation === state.projectSavedMutation) return { ok: true };
  writeProjectDraft();
  const operation = state.projectSaveChain.catch(() => {}).then(async () => {
    if (state.projectConflicts.length) throw new Error("Review competing project edits before saving.");
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const mutation = state.projectMutation;
      const savedSnapshot = clone(state.projects);
      const baseline = new Map(state.projectBase.map(project => [project.id, JSON.stringify(project)]));
      const payload = {
        version: 2,
        revision: state.projectRevision,
        active_project_id: state.activeProjectId,
        partial: true,
        projects: savedSnapshot.filter(project => baseline.get(project.id) !== JSON.stringify(project)),
        deletedProjectIds: state.projectBase.filter(project => !savedSnapshot.some(item => item.id === project.id)).map(project => project.id),
      };
      const response = await api.fetchApi(PROJECTS_ENDPOINT, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await response.json().catch(() => ({}));
      if (response.status === 409) {
        const remote = await fetchProjectStore();
        const conflicts = [];
        const merged = mergeProjectVersions(state.projectBase, state.projects, remote.projects, conflicts);
        state.projectConflicts = conflicts;
        if (conflicts.length) throw new Error(`Competing edits at ${conflicts.map(item => item.path).join(", ")}. Review conflicts before saving.`);
        applyProjectMerge(merged);
        state.projectBase = clone(remote.projects);
        state.projectRevision = Number(remote.revision || 0);
        writeProjectDraft();
        renderAll();
        continue;
      }
      if (!response.ok) throw new Error(data.error || `Projects could not be saved (${response.status}).`);
      state.projectRevision = Number(data.revision || state.projectRevision);
      state.projectBase = savedSnapshot;
      state.projectConflicts = [];
      state.projectSavedMutation = Math.max(state.projectSavedMutation, mutation);
      await videoDraftPending;
      await videoDraftOutbox.acknowledge(draftTabKey("video"),mutation).catch(() => {});
      setSaveState(state.projectSavedMutation === state.projectMutation ? "Saved" : "Saving…");
      if (state.projectSavedMutation !== state.projectMutation) { writeProjectDraft(); persistProjects(); }
      else { try { localStorage.removeItem(projectDraftKey()); } catch (_) { /* Saving succeeded; an old draft is safe to reconcile. */ } }
      return { ok: true, revision: state.projectRevision };
    }
    throw new Error("Projects kept changing in another browser. Retry save.");
  });
  state.projectSaveChain = operation;
  try {
    return await operation;
  } catch (error) {
    showProjectSaveFailure(error);
    if (immediate) throw error;
    return { ok: false, error };
  }
}

async function loadConfig() {
  const response = await api.fetchApi("/promptstudio-video/config");
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "Video Studio configuration could not be loaded.");
  state.config = data;
}

async function loadProjects() {
  try {
    const archive = await videoDraftOutbox.get(draftTabKey("video-director"));
    if (archive?.sessions) state.directorSessions = archive.sessions;
  } catch (_) { /* The existing local archive remains available. */ }
  const data = await fetchProjectStore({page: true});
  state.projectPageCursor = data.nextCursor || null;
  state.projectHasMore = Boolean(data.hasMore);
  state.projects = Array.isArray(data.projects) ? data.projects : [];
  state.projectBase = clone(state.projects);
  state.projectRevision = Number(data.revision || 0);
  state.activeProjectId = data.active_project_id || state.projects[0]?.id || null;
  state.selectedShotId = activeProject()?.document?.shots?.[0]?.id || null;
  state.projectMutation = state.projectSavedMutation = 0;
  let draft;
  try { draft = await videoDraftOutbox.get(draftTabKey("video")); }
  catch (error) { showDraftStorageFailure(state.panel?.querySelector(".psvstudio-sidebar"),{},error.message); }
  try { draft ||= JSON.parse(localStorage.getItem(projectDraftKey()) || "null"); } catch (_) { /* Keep unreadable drafts intact. */ }
  if (draft?.version === 1 && Array.isArray(draft.base) && Array.isArray(draft.projects)) {
    const conflicts = [];
    const remote = await fetchProjectStore();
    state.projectBase = clone(remote.projects);
    state.projectRevision = Number(remote.revision || 0);
    const merged = mergeProjectVersions(draft.base, draft.projects, remote.projects, conflicts);
    applyProjectMerge(merged);
    state.activeProjectId = state.projects.some(project => project.id === draft.active_project_id) ? draft.active_project_id : state.activeProjectId;
    state.selectedShotId = activeProject()?.document?.shots?.[0]?.id || null;
    state.projectConflicts = conflicts;
    if (conflicts.length) state.projectBase = clone(draft.base);
    state.projectMutation = 1;
    // A draft may have been saved immediately before a queue response was lost.
    // Never resubmit recovered preparations automatically.
    for (const project of state.projects) for (const generation of project.generations || []) {
      if (!generation.prompt_id && ["validating", "compiling", "queueing"].includes(generation.status)) {
        generation.status = "error";
        generation.error = "Recovered unsaved preparation. Check the ComfyUI queue before generating again.";
      }
    }
    showProjectSaveFailure(new Error(conflicts.length ? "Recovered draft has competing server edits." : "Recovered unsaved video draft. Retry save to store it."));
  }
}

async function loadOlderProjects() {
  if (state.projectPageLoading || !state.projectHasMore) return;
  state.projectPageLoading = true;
  renderProjectList();
  try {
    const data = await fetchProjectStore({page: true, cursor: state.projectPageCursor});
    for (const project of data.projects) {
      if (state.projects.some(item => item.id === project.id) || state.projectBase.some(item => item.id === project.id)) continue;
      state.projects.push(project);
      state.projectBase.push(clone(project));
    }
    state.projectPageCursor = data.nextCursor || null;
    state.projectHasMore = Boolean(data.hasMore);
    resumeGenerationPolling();
  } catch (error) { setStatus(error.message, "warning"); }
  finally { state.projectPageLoading = false; renderProjectList(); }
}

function createProjectRecord() {
  closeShotEditor({ force: true });
  const now = Date.now();
  const document = clone(state.config.default_document);
  document.shots[0].id = makeId("shot");
  const project = {
    id: makeId("project"),
    name: "Untitled video",
    brief: "",
    document,
    workflow_id: state.workflows[0]?.id || "",
    additional_input_selections: {},
    generations: [],
    created_at: now,
    updated_at: now,
  };
  state.projects.unshift(project);
  state.activeProjectId = project.id;
  state.selectedShotId = project.document.shots[0].id;
  return project;
}

function newProject() {
  const project = createProjectRecord();
  closeVideoDrawer();
  markProjectChanged({ render: true });
  setStatus("New video project created.", "ready");
  return project;
}

function selectProject(projectId) {
  closeShotEditor({ force: true });
  state.activeProjectId = projectId;
  state.selectedShotId = activeProject()?.document?.shots?.[0]?.id || null;
  closeVideoDrawer();
  state.projectMutation += 1;
  persistProjects();
  renderAll();
  postVideoStudioPresence();
}

function projectPendingGenerationCount(project) {
  return (project?.generations || []).filter(generation => (
    ["validating", "compiling", "queueing", "queued", "generating"].includes(generation.status)
  )).length;
}

function deleteProject(projectId) {
  const index = state.projects.findIndex(project => project.id === projectId);
  if (index < 0) return;
  const project = state.projects[index];
  const pendingCount = projectPendingGenerationCount(project);
  if (pendingCount || projectHasPendingDirectorJob(projectId) || (state.directorBusy && projectId === state.activeProjectId)) return;
  const view = state.panel?.ownerDocument.defaultView;
  if (!view?.confirm(`Delete the video session "${project.name || "Untitled video"}"? This cannot be undone.`)) return;

  const wasActive = projectId === state.activeProjectId;
  if (wasActive) closeShotEditor({ force: true });
  state.projects.splice(index, 1);
  delete directorSessions()[projectId];
  delete directorSessions()[`${projectId}:project`];
  persistDirectorSessions();
  if (wasActive) {
    const replacement = [...state.projects].sort((left, right) => (
      Number(right.updated_at) - Number(left.updated_at)
    ))[0] || null;
    state.activeProjectId = replacement?.id || null;
    state.selectedShotId = replacement?.document?.shots?.[0]?.id || null;
  }
  state.projectMutation += 1;
  setSaveState("Saving...");
  renderAll();
  persistProjects();
  setStatus(`Deleted video session "${project.name || "Untitled video"}".`, "ready");
}

function duplicateProject() {
  const source = activeProject();
  if (!source) return;
  const copy = clone(source);
  copy.id = makeId("project");
  copy.name = `${source.name} copy`;
  copy.generations = [];
  copy.created_at = copy.updated_at = Date.now();
  copy.document.shots.forEach(shot => { shot.id = makeId("shot"); });
  state.projects.unshift(copy);
  state.activeProjectId = copy.id;
  state.selectedShotId = copy.document.shots[0]?.id || null;
  markProjectChanged({ render: true });
}

function resetProjectReference(project, reference) {
  return {
    id: reference.id,
    kind: reference.kind,
    path: reference.path,
    name: reference.name,
    roles: defaultReferenceRoles(project, reference.kind),
    prompt: "",
    label: "",
    trim_start: 0,
    trim_end: null,
    use_embedded_audio: false,
    source_width: Number(reference.source_width) || 0,
    source_height: Number(reference.source_height) || 0,
    duration_seconds: Number(reference.duration_seconds) || 0,
    audio_codec: reference.audio_codec || "",
    audio_sample_rate: Number(reference.audio_sample_rate) || 0,
    observed_visual_facts: "",
    subject_candidates: [],
  };
}

function resetProject() {
  const project = activeProject();
  if (!project || projectPendingGenerationCount(project) || projectHasPendingDirectorJob(project.id) || state.directorBusy) return;
  const view = state.panel?.ownerDocument.defaultView;
  const referenceCount = project.document?.references?.length || 0;
  const mediaSummary = referenceCount
    ? `${referenceCount} reference media item${referenceCount === 1 ? "" : "s"} will remain, with fresh default roles.`
    : "The project has no reference media to retain.";
  if (!view?.confirm(
    `Reset "${project.name || "Untitled video"}"?\n\nThis clears its brief, shots, generation settings, render history, and Director conversations. ${mediaSummary}`,
  )) return;

  closeShotEditor({ force: true });
  if (state.directorDialog?.open) state.directorDialog.close();

  const references = clone(project.document?.references || []);
  for (const reference of references) {
    state.mediaDimensionLoads.delete(reference.id);
    state.mediaDurationLoads.delete(reference.id);
  }
  for (const generation of project.generations || []) {
    stopGenerationPolling(generation.prompt_id);
    state.generationFailures.delete(String(generation.prompt_id || ""));
  }
  for (const key of state.loopingGenerations) {
    if (key.startsWith(`${project.id}:`)) state.loopingGenerations.delete(key);
  }

  const document = clone(state.config.default_document);
  document.shots[0].id = makeId("shot");
  document.references = [];
  project.document = document;
  for (const reference of references) {
    project.document.references.push(resetProjectReference(project, reference));
  }
  synchronizeGeometryCanvas(project);
  project.brief = "";
  project.workflow_id = state.workflows[0]?.id || "";
  project.additional_input_selections = {};
  project.generations = [];
  state.selectedShotId = document.shots[0].id;

  delete directorSessions()[project.id];
  delete directorSessions()[`${project.id}:project`];
  persistDirectorSessions();
  markProjectChanged({ render: true });
  setStatus(`Project reset. Kept ${referenceCount} reference media item${referenceCount === 1 ? "" : "s"}.`, "ready");
}

function addShot() {
  const project = activeProject();
  if (!project) return;
  const shots = project.document.shots;
  const duration = Number(project.document.duration_seconds || 5);
  const lastStart = Number(shots.at(-1)?.start || 0);
  let start = Math.round((lastStart + Math.max(lastStart + 0.25, duration)) * 12) / 24;
  if (start >= duration) {
    const maximumDuration = isStructuredExtensionProject(project) ? 15 : 150;
    if (duration >= maximumDuration) {
      setStatus(`This ${maximumDuration}s extension has no room for another shot. Move an existing cut earlier first.`, "warning");
      return;
    }
    project.document.duration_seconds = Math.min(maximumDuration, Math.ceil((duration + 1) * 4) / 4);
    start = duration;
  }
  const shot = {
    id: makeId("shot"), start, transition: "the camera cuts to", composition: "", subjects: "",
    environment: "", lighting: "", camera: { type: "Static Shot", amplitude: "default", speed: "default", target: "" },
    steps: [], visible_text: [], sounds: [], sound_cues: [], audio_clips: [], notes: "",
  };
  shots.push(shot);
  state.selectedShotId = shot.id;
  markProjectChanged({ render: true });
  return shot;
}

function effectiveDurationHint(seconds) {
  const requestedFrames = Math.max(5, Math.round(Number(seconds || 5) * 24));
  let frames = requestedFrames;
  while (frames % 17 !== 5) frames += 1;
  return `${frames} frames · ${(frames / 24).toFixed(2)}s effective`;
}

function workflowNameFromPath(path) {
  return String(path || "").replaceAll("\\", "/").split("/").pop()?.replace(/\.json$/i, "") || "Workflow";
}

function isVideoWorkflowPath(path) {
  const filename = String(path || "").replaceAll("\\", "/").split("/").pop() || "";
  return filename.startsWith(WORKFLOW_PREFIX) && filename.toLowerCase().endsWith(".json");
}

const buildWorkflowTemplate = createVideoWorkflowTemplateBuilder({ app });

async function loadWorkflowCache() {
  const response = await api.fetchApi(WORKFLOWS_ENDPOINT);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "Video workflow cache could not be loaded.");
  state.workflows = Array.isArray(data.templates)
    ? data.templates.map(workflow => ({
      ...workflow,
      additionalInputs: normalizePromptStudioInputDescriptors(workflow.additionalInputs),
    }))
    : [];
  state.workflowRevision = Number(data.revision || 0);
}

async function saveWorkflowCache() {
  const response = await api.fetchApi(WORKFLOWS_ENDPOINT, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ version: 1, revision: state.workflowRevision, templates: state.workflows }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Video workflow cache could not be saved (${response.status}).`);
  state.workflowRevision = Number(data.revision || state.workflowRevision);
}

function formatSetupBytes(value) {
  const bytes = Math.max(0, Number(value || 0));
  if (bytes < 1024) return `${Math.round(bytes)} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let amount = bytes;
  let unit = -1;
  do {
    amount /= 1024;
    unit += 1;
  } while (amount >= 1024 && unit < units.length - 1);
  return `${amount.toFixed(amount >= 10 ? 1 : 2)} ${units[unit]}`;
}

async function fetchDefaultWorkflowPlan() {
  const response = await api.fetchApi(DEFAULT_WORKFLOWS_ENDPOINT);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "Default Video Studio workflows could not be prepared.");
  if (!Array.isArray(data.workflows) || data.workflows.length !== 2) {
    throw new Error("Default Video Studio workflow package is incomplete.");
  }
  return data;
}

async function storeDefaultWorkflow(workflow) {
  const userDataPath = `workflows/${workflow.path}`;
  const response = typeof api.storeUserData === "function"
    ? await api.storeUserData(userDataPath, workflow.data, {
      overwrite: false, stringify: true, throwOnError: false, full_info: true,
    })
    : await api.fetchApi(`/userdata/${encodeURIComponent(userDataPath)}?overwrite=false&full_info=true`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(workflow.data),
    });
  if (!response.ok && response.status !== 409) {
    throw new Error(`ComfyUI could not save ${workflow.path} (${response.status}).`);
  }
}

function defaultSetupWarning(plan) {
  const models = (plan.models || []).map(model => `• ${model.name}`).join("\n");
  const missing = Number(plan.missing_bytes || 0);
  const download = missing
    ? `${formatSetupBytes(missing)} still needs to be downloaded.`
    : "All required files are already installed and will be reused.";
  return [
    "Creating the Normal and Turbo workflows will also install every model and LoRA they currently use.",
    "",
    models,
    "",
    `The complete asset set is ${formatSetupBytes(plan.total_bytes)}. ${download}`,
    "Existing matching files are reused. Missing downloads are resumable.",
    "",
    "Continue and create both ready-to-use workflows?",
  ].join("\n");
}

async function pollDefaultSetup(jobId) {
  state.defaultSetupJobId = String(jobId || "");
  if (!state.defaultSetupJobId) return;
  try {
    while (state.defaultSetupJobId === String(jobId)) {
      const response = await api.fetchApi(`${DEFAULT_SETUP_ENDPOINT}?job_id=${encodeURIComponent(jobId)}`);
      const job = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(job.error || "The default model download status could not be read.");
      if (job.status === "complete") {
        state.defaultSetupJobId = "";
        if (typeof app.refreshComboInNodes === "function") await app.refreshComboInNodes();
        setStatus("Default Normal and Turbo workflows are ready. All required models and LoRAs are installed.", "ready");
        return;
      }
      if (job.status === "error") {
        state.defaultSetupJobId = "";
        setStatus(`Default workflow setup stopped: ${job.error || "model download failed"}. Reload Video Studio to retry the resumable downloads.`, "error");
        return;
      }
      if (job.status === "idle") {
        state.defaultSetupJobId = "";
        return;
      }
      const completed = formatSetupBytes(job.downloaded_bytes);
      const total = formatSetupBytes(job.total_bytes);
      const current = job.current_model ? ` · ${job.current_model}` : "";
      setStatus(job.stage === "verifying"
        ? `Verifying downloaded model integrity${current}…`
        : `Installing default workflow models: ${completed} / ${total}${current}`, "busy");
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
  } catch (error) {
    state.defaultSetupJobId = "";
    setStatus(`${error.message || error} The model downloads remain resumable.`, "warning");
  }
}

async function startDefaultWorkflowSetup(plan) {
  setStatus("Creating the default Normal and Turbo workflows…", "busy");
  for (const workflow of plan.workflows) await storeDefaultWorkflow(workflow);
  const response = await api.fetchApi(DEFAULT_SETUP_ENDPOINT, { method: "POST" });
  const job = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(job.error || "Default model downloads could not be started.");
  await refreshWorkflows({ announce: false });
  if (job.status === "complete") {
    if (typeof app.refreshComboInNodes === "function") await app.refreshComboInNodes();
    setStatus("Default Normal and Turbo workflows are ready. All required models and LoRAs were already installed.", "ready");
  } else {
    void pollDefaultSetup(job.id);
  }
}

async function offerDefaultWorkflowSetup() {
  if (state.workflows.length || state.defaultWorkflowSetupPrompted) return false;
  state.defaultWorkflowSetupPrompted = true;
  const view = state.panel?.ownerDocument.defaultView;
  if (!view?.confirm("No compatible Prompt Studio Video workflow was found. Create the default Normal and Turbo workflows?")) {
    return false;
  }
  const plan = await fetchDefaultWorkflowPlan();
  if (!view.confirm(defaultSetupWarning(plan))) return false;
  await startDefaultWorkflowSetup(plan);
  return true;
}

async function resumeDefaultSetupMonitor() {
  const response = await api.fetchApi(DEFAULT_SETUP_ENDPOINT);
  if (!response.ok) return;
  const job = await response.json().catch(() => ({}));
  if (["starting", "downloading"].includes(job.status) && job.id) {
    void pollDefaultSetup(job.id);
    return;
  }
  const defaultPaths = new Set(["[PSV] MiniMax H3.json", "[PSV] MiniMax H3 Turbo.json"]);
  if (![...defaultPaths].every(path => state.workflows.some(workflow => workflow.path === path))) return;
  const plan = await fetchDefaultWorkflowPlan();
  if (!Number(plan.missing_bytes || 0)) return;
  const view = state.panel?.ownerDocument.defaultView;
  if (!view?.confirm(`The default Video Studio workflows still need ${formatSetupBytes(plan.missing_bytes)} of models and LoRAs. Resume the downloads now?`)) return;
  const startResponse = await api.fetchApi(DEFAULT_SETUP_ENDPOINT, { method: "POST" });
  const started = await startResponse.json().catch(() => ({}));
  if (!startResponse.ok) throw new Error(started.error || "Default model downloads could not be resumed.");
  if (started.status === "complete") setStatus("Default Video Studio models and LoRAs are installed.", "ready");
  else void pollDefaultSetup(started.id);
}

async function refreshWorkflows({ announce = true } = {}) {
  const previous = state.workflows;
  const cached = new Map(previous.map(workflow => [workflow.path, workflow]));
  const next = [];
  const issues = [];
  try {
    const response = await api.fetchApi("/userdata?dir=workflows&recurse=true&full_info=true");
    if (!response.ok) throw new Error(`ComfyUI workflows could not be listed (${response.status}).`);
    const files = discoverWorkflowFiles(await response.json(), "[PSV]");
    for (const file of files) {
      const old = cached.get(file.path);
try {
        const userDataPath = `workflows/${file.path}`;
        const workflowResponse = typeof api.getUserData === "function"
          ? await api.getUserData(userDataPath)
          : await api.fetchApi(`/userdata/${encodeURIComponent(userDataPath)}`);
        if (!workflowResponse.ok) throw new Error(`ComfyUI could not read the workflow (${workflowResponse.status}).`);
        next.push(await buildWorkflowTemplate(file, await workflowResponse.json(), old));
      } catch (error) {
        const message = error.message || String(error);
        issues.push(`${workflowNameFromPath(file.path)}: ${message}`);
        if (old?.snapshot?.output) next.push({ ...old, stale: true, error: message });
      }
    }
    state.workflows = next;
    if (JSON.stringify(previous) !== JSON.stringify(next)) await saveWorkflowCache();
    for (const project of state.projects) {
      if (!pendingGenerationRestore(project) && !state.workflows.some(workflow => workflow.id === project.workflow_id)) {
        project.workflow_id = state.workflows[0]?.id || "";
      }
    }
    if (announce) {
      if (issues.length) setStatus(`${issues.length} [PSV] workflow update${issues.length === 1 ? "" : "s"} failed. Cached copies remain available.`, "warning");
      else if (next.length) setStatus(`Loaded ${next.length} compatible [PSV] workflow${next.length === 1 ? "" : "s"}.`, "ready");
      else setStatus("No [PSV] workflow found. Save the working workflow with a [PSV] filename prefix, then refresh.", "warning");
    }
    renderAll();
  } catch (error) {
    state.workflows = previous.map(workflow => ({ ...workflow, stale: true, error: error.message || String(error) }));
    setStatus(`${error.message || error} The last working workflow cache remains available.`, "warning");
    renderAll();
  }
}

function randomizeSnapshotSeeds(snapshot) {
  for (const node of Object.values(snapshot?.output || {})) {
    for (const name of Object.keys(node?.inputs || {})) {
      if (/^(seed|noise_seed)$/i.test(name)) node.inputs[name] = Math.floor(Math.random() * 0x100000000);
    }
  }
}

function outputUrl(reference) {
  if (!reference?.filename) return "";
  const params = new URLSearchParams({
    filename: reference.filename,
    subfolder: reference.subfolder || "",
    type: reference.type || "output",
  });
  return `/view?${params}`;
}

function outputDescriptor(reference) {
  if (!reference?.filename) return null;
  return {
    filename: reference.filename,
    subfolder: reference.subfolder || "",
    type: reference.type || "output",
  };
}

function continuationSourceOutput(generation) {
  return outputDescriptor(generation?.segment_outputs?.[0] || generation?.outputs?.[0]);
}

function continuationLineageOutputs(generation) {
  const current = continuationSourceOutput(generation);
  if (!current) throw new Error("The selected generation has no saved video output to continue.");
  if (generation?.kind !== "extension" && !generation?.parent_generation_id) return [current];
  const savedSources = generation?.continuation?.source_segments;
  if (!Array.isArray(savedSources) || !savedSources.length) {
    throw new Error("This extension has no saved source-segment lineage.");
  }
  const lineage = savedSources.map(outputDescriptor);
  if (lineage.some(output => !output)) {
    throw new Error("A source segment in this continuation lineage is no longer available.");
  }
  return [...lineage, current];
}

function continuationAssemblyLineageOutputs(generation) {
  const currentPublic = continuationSourceOutput(generation);
  if (!currentPublic) throw new Error("The selected generation has no saved video output to assemble.");
  if (generation?.kind !== "extension" && !generation?.parent_generation_id) {
    return [{ ...currentPublic, overlap_frames: 0 }];
  }
  const savedSources = generation?.continuation?.source_assembly_segments;
  const fallbackSources = generation?.continuation?.source_segments;
  const sourceValues = Array.isArray(savedSources) && savedSources.length
    ? savedSources
    : (fallbackSources || []).map(value => ({ ...value, overlap_frames: 0 }));
  const lineage = sourceValues.map(value => {
    const output = outputDescriptor(value);
    return output ? { ...output, overlap_frames: Number(value?.overlap_frames || 0) } : null;
  });
  if (lineage.some(output => !output)) {
    throw new Error("An assembly source in this continuation lineage is no longer available.");
  }
  const overlapOutput = outputDescriptor(generation?.assembly_outputs?.[0]);
  return [...lineage, {
    ...(overlapOutput || currentPublic),
    overlap_frames: overlapOutput ? Number(generation?.continuation?.context_frames || CONTINUATION_CONTEXT_FRAMES) : 0,
  }];
}

function workflowSupportsMotionContext(snapshot, directorNodeId) {
  const inputs = snapshot?.output?.[directorNodeId]?.inputs || {};
  return Boolean(inputs.fl2va_model && inputs.video_vae && inputs.audio_vae);
}

function continuationLatentPath(projectId, generationId) {
  const safe = value => String(value || "").replace(/[^A-Za-z0-9_-]/g, "_").slice(0, 100);
  return `video/PromptStudio_Video/latents/${safe(projectId)}/${safe(generationId)}.safetensors`;
}

function nextPromptNodeId(output) {
  return String(Math.max(0, ...Object.keys(output || {}).map(value => Number(value) || 0)) + 1);
}

function linkedPromptNode(output, reference, label) {
  const id = Array.isArray(reference) ? String(reference[0]) : "";
  const node = id ? output?.[id] : null;
  if (!node) throw new Error(`The saved workflow has no connected ${label}.`);
  return [id, node];
}

function instrumentGenerationSnapshot(snapshot, workflow, project, generationId, metadata) {
  const output = snapshot?.output;
  if (!output) throw new Error("The saved workflow has no executable prompt graph.");
  const directorId = String(workflow.director_node_id || "");
  const director = output[directorId];
  if (!director || director.class_type !== DIRECTOR_TYPE) {
    throw new Error("The saved workflow no longer contains its Prompt Studio Video Director.");
  }
  const isExtension = (metadata.kind || "base") === "extension";
  if (!workflowSupportsMotionContext(snapshot, directorId)) {
    if (isExtension) {
      throw new Error("Video continuation requires connected MiniMax H3 FL2VA, video VAE, and audio VAE inputs.");
    }
    return { contextPath: "", assemblyResultNodeId: "" };
  }

  const samplerEntry = Object.entries(output).find(([, node]) => (
    node?.class_type === "SamplerCustomAdvanced"
    && Array.isArray(node.inputs?.latent_image)
    && String(node.inputs.latent_image[0]) === directorId
  ));
  if (!samplerEntry) {
    if (isExtension) {
      throw new Error("The workflow must connect the Director latent directly to SamplerCustomAdvanced.");
    }
    return { contextPath: "", assemblyResultNodeId: "" };
  }
  const [samplerId, sampler] = samplerEntry;
  let nextId = Number(nextPromptNodeId(output));
  const allocate = () => {
    while (output[String(nextId)]) nextId += 1;
    return String(nextId++);
  };
  const contextPath = continuationLatentPath(project.id, generationId);
  const saveContextId = allocate();
  output[saveContextId] = {
    inputs: {
      latent: [String(samplerId), 0],
      project_id: project.id,
      generation_id: generationId,
      context_frames: CONTINUATION_CONTEXT_FRAMES,
    },
    class_type: "PSV_H3SaveContext",
    _meta: { title: "Prompt Studio Save H3 Context" },
  };
  if (!isExtension) return { contextPath, assemblyResultNodeId: "" };

  const [, guider] = linkedPromptNode(output, sampler.inputs?.guider, "sampler guider");
  if (!Array.isArray(guider.inputs?.conditioning) || String(guider.inputs.conditioning[0]) !== directorId) {
    throw new Error("The workflow must connect the Director conditioning directly to the sampler guider.");
  }
  const videoDecoderEntry = Object.entries(output).find(([, node]) => (
    node?.class_type === "VAEDecode" && String(node.inputs?.samples?.[0]) === samplerId
  ));
  const audioDecoderEntry = Object.entries(output).find(([, node]) => (
    node?.class_type === "VAEDecodeAudio" && String(node.inputs?.samples?.[0]) === samplerId
  ));
  if (!videoDecoderEntry || !audioDecoderEntry) {
    throw new Error("The workflow must decode both video and audio directly from the H3 sampler output.");
  }
  const [videoDecoderId] = videoDecoderEntry;
  const [audioDecoderId] = audioDecoderEntry;
  const createVideoEntry = Object.entries(output).find(([, node]) => (
    node?.class_type === "CreateVideo"
    && String(node.inputs?.images?.[0]) === videoDecoderId
    && String(node.inputs?.audio?.[0]) === audioDecoderId
  ));
  if (!createVideoEntry) throw new Error("The workflow must combine the decoded H3 video and audio in CreateVideo.");
  const [createVideoId, createVideo] = createVideoEntry;
  const saveVideoId = (workflow.result_node_ids || []).map(String).find(id => (
    output[id]?.class_type === "SaveVideo"
  ));
  const saveVideo = saveVideoId ? output[saveVideoId] : null;
  if (!saveVideo || !Array.isArray(saveVideo.inputs?.video) || String(saveVideo.inputs.video[0]) !== createVideoId) {
    throw new Error("The workflow must save its native CreateVideo output directly.");
  }

  let assemblyResultNodeId = "";
  if (isExtension) {
    const continuation = metadata.continuation || {};
    const contextId = allocate();
    output[contextId] = {
      inputs: {
        conditioning: [directorId, 1],
        latent: [directorId, 2],
        video_vae: clone(director.inputs.video_vae),
        audio_vae: clone(director.inputs.audio_vae),
        context_latent_path: continuation.parent_context_latent_path || "",
        context_video: continuation.source_video || "",
        context_frames: CONTINUATION_CONTEXT_FRAMES,
      },
      class_type: "PSV_H3MotionContext",
      _meta: { title: "Prompt Studio H3 Motion Context" },
    };
    guider.inputs.conditioning = [contextId, 0];
    sampler.inputs.latent_image = [contextId, 1];

    const trimId = allocate();
    output[trimId] = {
      inputs: {
        images: [videoDecoderId, 0],
        audio: [audioDecoderId, 0],
        trim_frames: [contextId, 2],
        fps: Number(createVideo.inputs.fps || 24),
      },
      class_type: "PSV_H3TrimContext",
      _meta: { title: "Prompt Studio Trim H3 Context" },
    };
    createVideo.inputs.images = [trimId, 0];
    createVideo.inputs.audio = [trimId, 1];
    const overlapCreateId = allocate();
    output[overlapCreateId] = {
      ...clone(createVideo),
      inputs: {
        ...clone(createVideo.inputs),
        images: [trimId, 2],
        audio: [trimId, 3],
      },
      _meta: { title: "Prompt Studio Soft AV Assembly Video" },
    };
    assemblyResultNodeId = allocate();
    const originalPrefix = String(saveVideo.inputs.filename_prefix || "video/PromptStudio_Video");
    output[assemblyResultNodeId] = {
      ...clone(saveVideo),
      inputs: {
        ...clone(saveVideo.inputs),
        video: [overlapCreateId, 0],
        filename_prefix: `${originalPrefix}_soft_av_overlap`,
      },
      _meta: { title: "Prompt Studio Save Soft AV Assembly Overlap" },
    };
    for (const node of Object.values(output)) {
      if (node?.class_type === "SpectrumApplyMiniMaxH3" && "enabled" in (node.inputs || {})) {
        node.inputs.enabled = false;
      }
    }
  }
  return { contextPath, assemblyResultNodeId };
}

function collectHistoryOutputs(historyItem, resultNodeIds = [], resultFields = []) {
  return workflowResultOutputs(historyItem, resultNodeIds, resultFields);
}

function historyError(historyItem) {
  if (String(historyItem?.status?.status_str || "").toLowerCase() !== "error") return "";
  const messages = Array.isArray(historyItem.status.messages) ? historyItem.status.messages : [];
  const entry = [...messages].reverse().find(item => (
    Array.isArray(item) && ["execution_error", "execution_interrupted"].includes(item[0])
  ));
  const detail = entry?.[1] || {};
  return executionFailureMessage(entry?.[0] || "execution_error", detail);
}

function executionFailureMessage(eventName, detail = {}) {
  const reason = String(
    detail?.exception_message || detail?.error || detail?.message || detail?.exception_type || "",
  ).replace(/\s+/g, " ").trim().slice(0, 1000);
  const nodeType = String(detail?.node_type || "").trim();
  const nodeId = String(detail?.node_id || "").trim();
  const node = nodeType && nodeId ? `${nodeType}, node ${nodeId}` : nodeType || (nodeId ? `node ${nodeId}` : "");
  const fallback = eventName === "execution_interrupted"
    ? "ComfyUI interrupted execution."
    : "ComfyUI reported an execution error without further details.";
  return `Generation failed: ${reason || fallback}${node ? ` (${node})` : ""}`;
}

function generationByPromptId(promptId) {
  for (const project of state.projects) {
    const generation = project.generations.find(item => item.prompt_id === String(promptId));
    if (generation) return { project, generation };
  }
  return null;
}

function updateGeneration(promptId, changes) {
  const record = generationByPromptId(promptId);
  if (!record) return;
  Object.assign(record.generation, changes, { updated_at: Date.now() });
  record.project.updated_at = Date.now();
  markProjectChanged({ project: record.project });
  if (record.project.id === state.activeProjectId) renderGenerations();
}

function stopGenerationPolling(promptId) {
  const id = String(promptId || "");
  const timer = state.generationPollers.get(id);
  if (timer && timer !== true) clearTimeout(timer);
  state.generationPollers.delete(id);
  state.generationActivity.delete(id);
  state.generationProgress.delete(id);
  if (state.activeGenerationPromptId === id) state.activeGenerationPromptId = "";
}

async function cancelVideoGeneration(projectId, generationId) {
  const project = state.projects.find(item => item.id === projectId);
  const generation = project?.generations.find(item => String(item.id) === String(generationId));
  if (!project || !generation || ["complete", "error", "cancelled"].includes(generation.status)) return;
  state.generationControllers.get(generation.id)?.abort();
  state.generationControllers.delete(generation.id);
  const promptId = String(generation.prompt_id || "");
  if (promptId) {
    stopGenerationPolling(promptId);
    try {
      await api.fetchApi("/queue", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ delete: [promptId] }),
      });
    } catch (_) {
      // Persisted cancellation prevents this result from being reattached after reconnect.
    }
  }
  Object.assign(generation, { status: "cancelled", error: "Cancelled.", updated_at: Date.now() });
  project.updated_at = Date.now();
  markProjectChanged({ project, render: true });
  await persistProjects({ immediate: true });
}

function touchGeneration(promptId) {
  const id = String(promptId || "");
  if (id) state.generationActivity.set(id, Date.now());
}

function markGenerationExecuting(promptId) {
  const id = String(promptId || "");
  const record = generationByPromptId(id);
  if (!record) return false;
  state.activeGenerationPromptId = id;
  touchGeneration(id);
  if (record.generation.status === "queued") {
    updateGeneration(id, { status: "generating" });
  }
  return true;
}

function failGeneration(promptId, message) {
  const id = String(promptId || "");
  const record = generationByPromptId(id);
  const error = String(message || "Generation failed because ComfyUI stopped processing it.");
  if (!record || !["queued", "generating"].includes(record.generation.status)) {
    if (id) {
      state.generationFailures.set(id, error);
      while (state.generationFailures.size > 50) {
        state.generationFailures.delete(state.generationFailures.keys().next().value);
      }
    }
    return false;
  }
  stopGenerationPolling(id);
  updateGeneration(id, { status: "error", error });
  return true;
}

function activeGenerationPromptIds() {
  const ids = [];
  for (const project of state.projects) {
    for (const generation of project.generations || []) {
      if (["queued", "generating"].includes(generation.status) && generation.prompt_id) {
        ids.push(String(generation.prompt_id));
      }
    }
  }
  return ids;
}

function setApiConnected(connected) {
  connected = Boolean(connected);
  if (state.apiConnected === connected && state.panel?.dataset.apiConnected) return;
  const reconnected = connected && !state.apiConnected;
  const reconnectedAfterRestart = connected && !state.apiConnected && state.comfyRestartBusy;
  state.apiConnected = connected;
  if (reconnectedAfterRestart) state.comfyRestartBusy = false;
  const panel = state.panel;
  if (!panel) return;
  const banner = panel.querySelector("#psvstudio-api-connection");
  panel.dataset.apiConnected = connected ? "true" : "false";
  if (banner) banner.hidden = connected;
  renderSystemStatusSummary();
  if (connected) {
    if (state.disconnectedGenerationTimer) clearTimeout(state.disconnectedGenerationTimer);
    state.disconnectedGenerationTimer = null;
    for (const [control, wasDisabled] of state.disconnectedControls) {
      if (control.isConnected) control.disabled = wasDisabled;
    }
    state.disconnectedControls.clear();
    if (reconnected) {
      renderAll();
      setStatus("Video Studio reconnected to ComfyUI.", "ready");
    }
    return;
  }
  freezeDisconnectedControls(panel.ownerDocument?.body || panel);
  const activeElement = panel.ownerDocument?.activeElement;
  if (
    activeElement
    && isVideoStudioControl(activeElement)
    && !isDisconnectedAllowedControl(activeElement)
  ) {
    activeElement.blur();
  }
  setStatus("ComfyUI disconnected — Video Studio is frozen.", "error");
}

async function promptWorkerStopped() {
  const now = Date.now();
  if (state.promptWorkerHealthRequest) return state.promptWorkerHealthRequest;
  if (now - state.promptWorkerHealthCheckedAt < 3000) return false;
  state.promptWorkerHealthCheckedAt = now;
  state.promptWorkerHealthRequest = (async () => {
    try {
      const response = await api.fetchApi("/promptstudio-video/runtime-health", { cache: "no-store" });
      if (!response.ok) return false;
      const health = await response.json();
      if (health?.prompt_worker_alive === true) {
        state.promptWorkerSeenAlive = true;
        return false;
      }
      return state.promptWorkerSeenAlive && health?.prompt_worker_alive === false;
    } catch (_) {
      return false;
    } finally {
      state.promptWorkerHealthRequest = null;
    }
  })();
  return state.promptWorkerHealthRequest;
}

async function assembleContinuation(record, segmentOutputs, assemblyOutputs) {
  const { project, generation } = record;
  const segment = outputDescriptor(segmentOutputs?.[0]);
  const assemblySegment = outputDescriptor(assemblyOutputs?.[0]);
  const sources = generation.continuation?.source_assembly_segments || [];
  if (!segment || !assemblySegment || !sources.length) {
    throw new Error("Continuation assembly is missing its public segment or private Soft AV overlap.");
  }
  state.generationProgress.set(String(generation.prompt_id), { phase: "assembling" });
  updateGeneration(generation.prompt_id, { segment_outputs: clone(segmentOutputs), assembly_outputs: clone(assemblyOutputs) });
  const response = await api.fetchApi(CONTINUATION_ASSEMBLE_ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      project_id: project.id,
      generation_id: generation.id,
      sources: [...sources, { ...assemblySegment, overlap_frames: CONTINUATION_CONTEXT_FRAMES }],
    }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.output) throw new Error(data.error || "The cumulative continuation could not be assembled.");
  updateGeneration(generation.prompt_id, {
    status: "complete",
    outputs: [data.output],
    segment_outputs: clone(segmentOutputs),
    assembly_outputs: clone(assemblyOutputs),
    error: "",
  });
}

function documentHasExactAudio(document) {
  return (document?.shots || []).some(shot => (shot.audio_clips || []).length);
}

async function assembleExactAudio(record, rawOutputs) {
  const { project, generation } = record;
  const source = outputDescriptor(rawOutputs?.[0]);
  if (!source) throw new Error("Exact audio mixing is missing the rendered video output.");
  state.generationProgress.set(String(generation.prompt_id), { phase: "assembling" });
  updateGeneration(generation.prompt_id, { raw_outputs: clone(rawOutputs) });
  const response = await api.fetchApi(EXACT_AUDIO_MIX_ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      project_id: project.id,
      generation_id: generation.id,
      source,
      document: generation.document,
    }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.output) throw new Error(data.error || "Exact audio could not be mixed into the render.");
  updateGeneration(generation.prompt_id, {
    status: "complete",
    outputs: [data.output],
    raw_outputs: clone(rawOutputs),
    exact_audio_mixed: true,
    error: "",
  });
}

function pollGeneration(promptId) {
  const id = String(promptId || "");
  if (!id || state.generationPollers.has(id)) return;
  if (generationByPromptId(id)?.generation.status === "generating") touchGeneration(id);
  const tick = async () => {
    const record = generationByPromptId(id);
    if (!record || !["queued", "generating"].includes(record.generation.status)) {
      stopGenerationPolling(id);
      return;
    }
    try {
      const response = await api.fetchApi(`/history/${encodeURIComponent(id)}`);
      if (response.ok) {
        const history = await response.json();
        const item = history?.[id];
        if (item) {
          const error = historyError(item);
          const outputs = collectHistoryOutputs(item, record.generation.result_node_ids, record.generation.result_fields);
          if (error) {
            failGeneration(id, error);
            return;
          }
          if (item.status?.completed) {
            if (outputs.length && record.generation.kind === "extension") {
              const assemblyOutputs = collectHistoryOutputs(
                item,
                [record.generation.assembly_result_node_id],
                ["videos", "gifs", "images"],
              );
              try {
                await assembleContinuation(record, outputs, assemblyOutputs);
              } catch (error) {
                updateGeneration(id, {
                  status: "error",
                  outputs,
                  segment_outputs: outputs,
                  assembly_outputs: assemblyOutputs,
                  error: `The extension rendered, but cumulative assembly failed: ${error.message || error}`,
                });
              }
            } else if (outputs.length && documentHasExactAudio(record.generation.document)) {
              try {
                await assembleExactAudio(record, outputs);
              } catch (error) {
                updateGeneration(id, {
                  status: "error",
                  outputs,
                  raw_outputs: outputs,
                  error: `The video rendered, but exact audio mixing failed: ${error.message || error}`,
                });
              }
            } else {
              updateGeneration(id, {
                status: outputs.length ? "complete" : "error",
                outputs,
                error: outputs.length ? "" : "Generation completed without a saved video output.",
              });
            }
            stopGenerationPolling(id);
            renderAll();
            return;
          }
        }
      }
    } catch (_) {
      // A transient history failure is retried; ComfyUI may still be reconnecting.
    }
    if (await promptWorkerStopped()) {
      failGeneration(id, "Generation failed: ComfyUI's prompt worker stopped during processing, usually after an unrecovered execution or CUDA out-of-memory error.");
      return;
    }
    const timer = setTimeout(tick, 1100);
    state.generationPollers.set(id, timer);
  };
  state.generationPollers.set(id, true);
  tick();
}

async function queueSnapshot(project, workflow, snapshot, metadata, existingGeneration = null) {
  const generationId = existingGeneration?.id || makeId("generation");
  if (existingGeneration?.preparation_kind !== "replay") {
    applyPromptStudioInputValues(
      snapshot,
      workflow,
      normalizePromptStudioInputSelections(project?.additional_input_selections),
    );
  }
  const savedSnapshot = clone(snapshot);
  const provenance = await captureRuntimeProvenance(savedSnapshot, "video", {
    projectId:project.id, generationId, parentGenerationId:metadata.parent_generation_id || "",
  });
  if (existingGeneration?.status === "cancelled") return;
  const queuedSnapshot = clone(snapshot);
  const instrumentation = instrumentGenerationSnapshot(
    queuedSnapshot, workflow, project, generationId, metadata,
  );
  const { contextPath: contextLatentPath, assemblyResultNodeId } = instrumentation;
  await persistProjects({ immediate: true });
  // Retain queue intent even after its prerequisite save. If the queue response
  // is lost, reloading must not automatically submit this preparation again.
  writeProjectDraft();
  if (!(await videoDraftPending)) throw new Error(state.projectDraftError);
  if (existingGeneration?.status === "cancelled") return;
  const queued = await api.queuePrompt(-1, queuedSnapshot);
  const promptId = queued?.prompt_id;
  if (!promptId) throw new Error("ComfyUI did not return a prompt ID.");
  if (existingGeneration?.status === "cancelled") {
    try {
      await api.fetchApi("/queue", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ delete: [String(promptId)] }),
      });
    } catch (_) {
      // The cancelled record remains detached even if ComfyUI already started it.
    }
    return;
  }
  const generation = existingGeneration || {};
  Object.assign(generation, {
    id: generationId,
    prompt_id: String(promptId),
    status: "queued",
    error: "",
    document: clone(metadata.document),
    compiled_prompt: metadata.compiled_prompt,
    resolved_mode: metadata.resolved_mode,
    frame_count: metadata.frame_count,
    effective_duration: metadata.effective_duration,
    workflow_id: workflow.id,
    workflow_name: workflow.name,
    workflow_snapshot: savedSnapshot,
    provenance,
    context_latent_path: contextLatentPath,
    assembly_result_node_id: assemblyResultNodeId,
    result_node_ids: clone(workflow.result_node_ids),
    result_fields: clone(workflow.result_fields),
    outputs: [],
    segment_outputs: [],
    assembly_outputs: [],
    kind: metadata.kind || "base",
    parent_generation_id: metadata.parent_generation_id || "",
    root_generation_id: metadata.root_generation_id || generationId,
    depth: Math.max(0, Number(metadata.depth || 0)),
    total_effective_duration: Number(metadata.total_effective_duration || metadata.effective_duration || 0),
    ...(metadata.continuation ? { continuation: clone(metadata.continuation) } : {}),
    created_at: Date.now(),
    updated_at: Date.now(),
  });
  if (!existingGeneration) project.generations.unshift(generation);
  const earlyProgress = state.pendingGenerationProgress.get(String(promptId));
  state.pendingGenerationProgress.delete(String(promptId));
  if (earlyProgress) Object.assign(generation, { status: "generating", updated_at: Date.now() });
  state.generationProgress.set(String(promptId), { phase: "queued", ...(earlyProgress || {}) });
  markProjectChanged({ render: true });
  await persistProjects();
  touchGeneration(promptId);
  const reportedFailure = state.generationFailures.get(String(promptId));
  if (reportedFailure) {
    state.generationFailures.delete(String(promptId));
    failGeneration(promptId, reportedFailure);
  } else {
    pollGeneration(promptId);
  }
}

async function generateProject() {
  if (!state.apiConnected) {
    setStatus("ComfyUI disconnected — Video Studio is frozen.", "error");
    return;
  }
  const project = activeProject();
  if (!project) return;
  if (isStructuredExtensionProject(project)) {
    // An explicitly restored snapshot takes precedence over extension preparation.
    if (pendingGenerationRestore(project)) return generateRestoredComparison(project);
    await generateStructuredExtension(project);
    return;
  }
  if (pendingGenerationRestore(project)) return generateRestoredComparison(project);
  if (project.pending_generation_restore) { delete project.pending_generation_restore; persistProjects(); }
  const workflow = state.workflows.find(item => item.id === project.workflow_id);
  if (!workflow) {
    setStatus("Select a compatible [PSV] workflow before generating.", "error");
    return;
  }
  const operation = {
    id: makeId("generation"), prompt_id: "", status: "validating", error: "",
    workflow_id: workflow.id, workflow_name: workflow.name, outputs: [], segment_outputs: [],
    kind: "base", preparation_kind: "base", document: clone(project.document),
    new_seed: state.panel.querySelector("#psvstudio-new-seed")?.checked !== false,
    created_at: Date.now(), updated_at: Date.now(),
  };
  project.generations.unshift(operation);
  const controller = new AbortController();
  state.generationControllers.set(operation.id, controller);
  markProjectChanged({ project, render: true });
  const generate = state.panel.querySelector("#psvstudio-generate");
  generate.disabled = true;
  try {
    await persistProjects({ immediate: true });
    const response = await api.fetchApi("/promptstudio-video/document/compile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      body: JSON.stringify({ document: project.document }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "The video document is invalid.");
    Object.assign(operation, { status: "compiling", updated_at: Date.now() });
    markProjectChanged({ project, render: true });
    project.document = data.document;
    project.brief = project.document.main_description || "";
    const snapshot = clone(workflow.snapshot);
    const director = snapshot.output?.[workflow.director_node_id];
    if (!director) throw new Error("The selected workflow no longer contains its Director node.");
    director.inputs ||= {};
    director.inputs.document_json = JSON.stringify(data.document);
    if (operation.new_seed !== false) randomizeSnapshotSeeds(snapshot);
    Object.assign(operation, { status: "queueing", updated_at: Date.now() });
    markProjectChanged({ project, render: true });
    await queueSnapshot(project, workflow, snapshot, data, operation);
  } catch (error) {
    const cancelled = controller.signal.aborted;
    Object.assign(operation, {
      status: cancelled ? "cancelled" : "error",
      error: cancelled ? "Cancelled." : (error.message || String(error)),
      updated_at: Date.now(),
    });
    markProjectChanged({ project, render: true });
    await persistProjects();
  } finally {
    state.generationControllers.delete(operation.id);
    generate.disabled = false;
  }
}

async function replayGeneration(generation) {
  const project = activeProject();
  const workflow = {
    id: generation.workflow_id,
    name: generation.workflow_name,
    director_node_id: Object.entries(generation.workflow_snapshot?.output || {})
      .find(([, node]) => node?.class_type === DIRECTOR_TYPE)?.[0],
    result_node_ids: generation.result_node_ids,
    result_fields: generation.result_fields,
  };
  if (!project || !generation.workflow_snapshot) return;
  if (!(await reviewReplay(state.panel, generation.workflow_snapshot, generation.provenance, "video"))) return;
  const operation = {
    id: makeId("generation"), prompt_id: "", status: "queueing", error: "",
    workflow_id: workflow.id, workflow_name: workflow.name,
    workflow_snapshot: clone(generation.workflow_snapshot),
    result_node_ids: clone(generation.result_node_ids || []),
    result_fields: clone(generation.result_fields || []),
    document: clone(generation.document), compiled_prompt: generation.compiled_prompt,
    resolved_mode: generation.resolved_mode, frame_count: generation.frame_count,
    effective_duration: generation.effective_duration,
    total_effective_duration: generation.total_effective_duration,
    ...(generation.continuation ? { continuation: clone(generation.continuation) } : {}),
    outputs: [], segment_outputs: [], preparation_kind: "replay",
    kind: generation.kind || "base", created_at: Date.now(), updated_at: Date.now(),
  };
  project.generations.unshift(operation);
  state.generationControllers.set(operation.id, new AbortController());
  markProjectChanged({ project, render: true });
  try {
    await persistProjects({ immediate: true });
    await queueSnapshot(project, workflow, clone(generation.workflow_snapshot), {
      document: generation.document,
      compiled_prompt: generation.compiled_prompt,
      resolved_mode: generation.resolved_mode,
      frame_count: generation.frame_count,
      effective_duration: generation.effective_duration,
      kind: generation.kind,
      parent_generation_id: generation.parent_generation_id,
      root_generation_id: generation.root_generation_id,
      depth: generation.depth,
      total_effective_duration: generation.total_effective_duration,
      continuation: generation.continuation,
    }, operation);
  } catch (error) {
    const cancelled = operation.status === "cancelled";
    Object.assign(operation, {
      status: cancelled ? "cancelled" : "error",
      error: cancelled ? "Cancelled." : (error.message || String(error)),
      updated_at: Date.now(),
    });
    markProjectChanged({ project, render: true });
    await persistProjects();
  } finally {
    state.generationControllers.delete(operation.id);
  }
}

function savedGenerationWorkflow(generation) {
  const snapshot = clone(generation.workflow_snapshot);
  const director = Object.entries(snapshot?.output || {}).find(([, node]) => node?.class_type === DIRECTOR_TYPE);
  if (!director) throw new Error("The saved generation workflow has no Prompt Studio Video Director.");
  if (!workflowSupportsMotionContext(snapshot, director[0])) {
    throw new Error("This saved workflow has no connected MiniMax H3 FL2VA model and audiovisual VAEs for continuation.");
  }
  return {
    workflow: {
      id: generation.workflow_id,
      name: generation.workflow_name,
      director_node_id: String(director[0]),
      result_node_ids: clone(generation.result_node_ids || []),
      result_fields: clone(generation.result_fields || []),
    },
    snapshot,
  };
}

function continuationDirectorContext(generation) {
  const sourceDocument = generation?.document || {};
  const shot = clone(sourceDocument.shots?.at(-1) || {});
  const clean = value => String(value || "").replace(/<\s*(?:Picture|Video|Audio|Subject)\s+\d+\s*>/gi, "the established source");
  return {
    type: "native_h3_soft_av_extension",
    engine: "native_h3_soft_av_39",
    transition_policy: "soft_av",
    context_frames: CONTINUATION_CONTEXT_FRAMES,
    video_latent_steps: 12,
    audio_latent_steps: 65,
    audio_feather_steps: 8,
    source_effective_duration: Number(generation?.effective_duration || 0),
    source_final_shot: {
      composition: clean(shot.composition),
      subjects: clean(shot.subjects),
      environment: clean(shot.environment),
      lighting: clean(shot.lighting),
      camera: {
        type: shot.camera?.type || "Static Shot",
        amplitude: shot.camera?.amplitude || "default",
        speed: shot.camera?.speed || "default",
        target: clean(shot.camera?.target),
      },
      steps: (shot.steps || []).filter(step => !String(step?.id || "").startsWith("continuation-opening")).map(step => ({
        type: step.type,
        text: clean(step.text),
        ...(step.type === "dialogue" ? {
          speaker: clean(step.speaker), speaker_id: step.speaker_id,
          language: step.language, performance: step.performance,
        } : {}),
      })),
      sounds: (shot.sounds || []).filter(sound => !/^(?:Any ambience, dialogue, music, or physical sound already in progress|Synchronize the extension's authored sounds)/.test(String(sound || ""))).map(clean),
    },
  };
}

async function createStructuredExtensionProject(parentProject, generation, brief, durationSeconds, dialog, plan) {
  if (!plan?.result?.valid || !plan.result.document?.shots?.length || plan.status !== "complete") throw new Error("Build and validate the full extension plan before applying it.");
  const projectId = `project-${plan.job_id}`;
  const existing = state.projects.find(item => item.id === projectId);
  if (existing) {
    await persistProjects({ immediate: true });
    selectProject(existing.id);
    dialog?.close();
    return existing;
  }
  if (!parentProject || !generation) return null;
  const duration = Number(durationSeconds);
  if (!Number.isFinite(duration) || duration < 5 || duration > 15) {
    throw new Error("Extension duration must be between 5 and 15 seconds.");
  }
  const source = continuationSourceOutput(generation);
  if (!source) throw new Error("The selected generation has no saved video output to continue.");
  const lineage = continuationLineageOutputs(generation);
  const assemblyLineage = continuationAssemblyLineageOutputs(generation);
  const { workflow, snapshot } = savedGenerationWorkflow(generation);
  const now = Date.now();
  const project = {
    id: projectId,
    name: `${parentProject.name || "Untitled video"} · Extension ${Number(generation.depth || 0) + 1}`,
    brief: String(brief || "").trim(),
    document: clone(plan.result.document),
    workflow_id: workflow.id,
    generations: [],
    extension_source: {
      engine: "native_h3_soft_av_39",
      parent_project_id: parentProject.id,
      parent_generation_id: generation.id,
      root_generation_id: generation.root_generation_id || generation.id,
      depth: Number(generation.depth || 0) + 1,
      continuation_base_duration: Number(generation.total_effective_duration || generation.effective_duration || 0),
      source: clone(source), source_segments: clone(lineage), source_document: clone(generation.document),
      source_assembly_segments: clone(assemblyLineage),
      parent_context_latent_path: generation.context_latent_path
        || continuationLatentPath(parentProject.id, generation.id),
      workflow_id: workflow.id, workflow_name: workflow.name,
      workflow_snapshot: clone(snapshot), workflow_director_node_id: workflow.director_node_id,
      result_node_ids: clone(workflow.result_node_ids || []), result_fields: clone(workflow.result_fields || []),
      director_context: clone(plan.result.continuation_context),
    },
    created_at: now, updated_at: now,
  };
  state.projects.unshift(project);
  state.activeProjectId = project.id;
  state.selectedShotId = project.document.shots[0].id;
  markProjectChanged({ project, render: true });
  await persistProjects({ immediate: true });
  dialog?.close();
  setStatus("Validated extension plan saved. Review its shots, then generate the extension.", "ready");
  return project;
}

async function generateStructuredExtension(project) {
  const source = project?.extension_source;
  if (!project || !source?.workflow_snapshot || !source?.source_document) {
    setStatus("This extension no longer has its saved source workflow and document.", "error");
    return;
  }
  const workflow = {
    id: source.workflow_id, name: source.workflow_name,
    director_node_id: String(source.workflow_director_node_id || ""),
    result_node_ids: clone(source.result_node_ids || []), result_fields: clone(source.result_fields || []),
    snapshot: clone(source.workflow_snapshot),
  };
  const snapshot = clone(source.workflow_snapshot);
  const durationSeconds = Number(project.document.duration_seconds || 5);
  const operation = {
    id: makeId("generation"), prompt_id: "", status: "compiling", error: "",
    workflow_id: workflow.id, workflow_name: workflow.name,
    workflow_snapshot: clone(snapshot), workflow_director_node_id: workflow.director_node_id,
    result_node_ids: clone(workflow.result_node_ids), result_fields: clone(workflow.result_fields),
    document: clone(project.document), outputs: [], segment_outputs: [], assembly_outputs: [],
    kind: "extension", preparation_kind: "continuation", parent_generation_id: source.parent_generation_id,
    root_generation_id: source.root_generation_id, depth: Number(source.depth || 1),
    continuation_base_duration: Number(source.continuation_base_duration || 0),
    new_seed: state.panel.querySelector("#psvstudio-new-seed")?.checked !== false,
    continuation_request: {
      source: clone(source.source), source_document: clone(source.source_document),
      extension_document: clone(project.document), brief: project.brief,
      duration_seconds: durationSeconds, context_frames: CONTINUATION_CONTEXT_FRAMES,
      source_segments: clone(source.source_segments || []),
      parent_context_latent_path: source.parent_context_latent_path || "",
      source_assembly_segments: clone(source.source_assembly_segments || source.source_segments || []),
    },
    created_at: Date.now(), updated_at: Date.now(),
  };
  project.generations.unshift(operation);
  const controller = new AbortController();
  state.generationControllers.set(operation.id, controller);
  markProjectChanged({ project, render: true });
  const generate = state.panel.querySelector("#psvstudio-generate");
  generate.disabled = true;
  try {
    await persistProjects({ immediate: true });
    const response = await api.fetchApi(CONTINUATION_PREPARE_ENDPOINT, {
      method: "POST", headers: { "Content-Type": "application/json" }, signal: controller.signal,
      body: JSON.stringify({
        document: source.source_document, extension_document: project.document,
        source: source.source, brief: project.brief,
        duration_seconds: durationSeconds, context_frames: CONTINUATION_CONTEXT_FRAMES,
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "The structured extension could not be prepared.");
    const director = snapshot.output?.[workflow.director_node_id];
    if (!director) throw new Error("The saved continuation workflow has no Director node.");
    director.inputs ||= {};
    director.inputs.document_json = JSON.stringify(data.document);
    if (operation.new_seed !== false) randomizeSnapshotSeeds(snapshot);
    Object.assign(operation, { status: "queueing", document: clone(data.document), updated_at: Date.now() });
    markProjectChanged({ project, render: true });
    await queueSnapshot(project, workflow, snapshot, {
      ...data, kind: "extension",
      parent_generation_id: source.parent_generation_id,
      root_generation_id: source.root_generation_id,
      depth: Number(source.depth || 1),
      total_effective_duration: Number(source.continuation_base_duration || 0) + Number(data.effective_duration || 0),
      continuation: {
        ...data.continuation, source_generation_id: source.parent_generation_id,
        source_segments: clone(source.source_segments || []),
        parent_context_latent_path: source.parent_context_latent_path || "",
        source_assembly_segments: clone(source.source_assembly_segments || source.source_segments || []),
        brief: project.brief, structured: true,
      },
    }, operation);
  } catch (error) {
    const cancelled = controller.signal.aborted;
    Object.assign(operation, {
      status: cancelled ? "cancelled" : "error",
      error: cancelled ? "Cancelled." : (error.message || String(error)), updated_at: Date.now(),
    });
    markProjectChanged({ project, render: true });
    await persistProjects();
  } finally {
    state.generationControllers.delete(operation.id);
    generate.disabled = false;
  }
}

async function queueContinuation(project, parent, brief, durationSeconds, dialog, submit) {
  const source = continuationSourceOutput(parent);
  if (!source) throw new Error("The selected generation has no saved video output to continue.");
  const lineage = continuationLineageOutputs(parent);
  const { workflow, snapshot } = savedGenerationWorkflow(parent);
  const assemblyLineage = continuationAssemblyLineageOutputs(parent);
  const operation = {
    id: makeId("generation"), prompt_id: "", status: "compiling", error: "",
    workflow_id: workflow.id, workflow_name: workflow.name,
    workflow_snapshot: clone(snapshot), workflow_director_node_id: workflow.director_node_id,
    result_node_ids: clone(workflow.result_node_ids || []), result_fields: clone(workflow.result_fields || []),
    document: clone(parent.document), outputs: [], segment_outputs: [], assembly_outputs: [],
    kind: "extension", preparation_kind: "continuation", parent_generation_id: parent.id,
    root_generation_id: parent.root_generation_id || parent.id,
    depth: Number(parent.depth || 0) + 1,
    continuation_base_duration: Number(parent.total_effective_duration || parent.effective_duration || 0),
    continuation_request: {
      source: clone(source), source_document: clone(parent.document),
      brief, duration_seconds: durationSeconds,
      context_frames: CONTINUATION_CONTEXT_FRAMES, source_segments: clone(lineage),
      parent_context_latent_path: parent.context_latent_path
        || continuationLatentPath(project.id, parent.id),
      source_assembly_segments: clone(assemblyLineage),
    },
    created_at: Date.now(), updated_at: Date.now(),
  };
  project.generations.unshift(operation);
  const controller = new AbortController();
  state.generationControllers.set(operation.id, controller);
  markProjectChanged({ project, render: true });
  submit.disabled = true;
  try {
    await persistProjects({ immediate: true });
    const response = await api.fetchApi(CONTINUATION_PREPARE_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      body: JSON.stringify({
        document: parent.document,
        source,
        brief,
        duration_seconds: durationSeconds,
        context_frames: CONTINUATION_CONTEXT_FRAMES,
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "The continuation prompt could not be prepared.");
    const director = snapshot.output?.[workflow.director_node_id];
    director.inputs ||= {};
    director.inputs.document_json = JSON.stringify(data.document);
    randomizeSnapshotSeeds(snapshot);
    Object.assign(operation, { status: "queueing", document: clone(data.document), updated_at: Date.now() });
    markProjectChanged({ project, render: true });
    await queueSnapshot(project, workflow, snapshot, {
      ...data,
      kind: "extension",
      parent_generation_id: parent.id,
      root_generation_id: parent.root_generation_id || parent.id,
      depth: Number(parent.depth || 0) + 1,
      total_effective_duration: Number(parent.total_effective_duration || parent.effective_duration || 0)
        + Number(data.effective_duration || 0),
      continuation: {
        ...data.continuation,
        source_generation_id: parent.id,
        source_segments: lineage,
        parent_context_latent_path: parent.context_latent_path
          || continuationLatentPath(project.id, parent.id),
        source_assembly_segments: assemblyLineage,
        brief,
      },
    }, operation);
    dialog.close();
  } catch (error) {
    const cancelled = controller.signal.aborted || operation.status === "cancelled";
    Object.assign(operation, {
      status: cancelled ? "cancelled" : "error",
      error: cancelled ? "Cancelled." : (error.message || String(error)),
      updated_at: Date.now(),
    });
    markProjectChanged({ project, render: true });
    await persistProjects();
    if (!cancelled) throw error;
  } finally {
    state.generationControllers.delete(operation.id);
    submit.disabled = false;
  }
}

function extensionRegenerationRequest(project, generation) {
  const saved = clone(generation?.continuation_request || {});
  const structured = Boolean(generation?.continuation?.structured || saved.extension_document);
  const extensionSource = isStructuredExtensionProject(project) ? project.extension_source : null;
  const parent = project?.generations?.find(item => item.id === generation?.parent_generation_id);
  const source = outputDescriptor(saved.source || extensionSource?.source);
  const sourceDocument = clone(saved.source_document || extensionSource?.source_document || parent?.document || null);
  const sourceSegments = clone(saved.source_segments || generation?.continuation?.source_segments || extensionSource?.source_segments || []);
  if (!source || !sourceDocument || !sourceSegments.length) {
    throw new Error("This extension no longer has the immutable source snapshot needed for regeneration.");
  }
  const extensionDocument = structured
    ? clone(extensionSource ? project.document : saved.extension_document)
    : null;
  if (structured && !extensionDocument) {
    throw new Error("This structured extension no longer has its authored shot document.");
  }
  const sourceAssemblySegments = clone(
    saved.source_assembly_segments || generation?.continuation?.source_assembly_segments
      || extensionSource?.source_assembly_segments
      || sourceSegments.map(value => ({ ...value, overlap_frames: 0 })),
  );
  return {
    structured,
    source,
    source_document: sourceDocument,
    source_segments: sourceSegments,
    source_assembly_segments: sourceAssemblySegments,
    extension_document: extensionDocument,
    brief: structured
      ? String(project?.brief || extensionDocument?.main_description || "").trim()
      : String(saved.brief || generation?.continuation?.brief || generation?.document?.main_description || "").trim(),
    duration_seconds: Number(
      structured
        ? extensionDocument.duration_seconds
        : (saved.duration_seconds || generation?.effective_duration || 5),
    ),
    parent_context_latent_path: saved.parent_context_latent_path
      || generation?.continuation?.parent_context_latent_path
      || extensionSource?.parent_context_latent_path
      || "",
  };
}

async function regenerateExtension(project, generation, request, dialog, submit) {
  const { workflow, snapshot } = savedGenerationWorkflow(generation);
  const operation = {
    id: makeId("generation"), prompt_id: "", status: "compiling", error: "",
    workflow_id: workflow.id, workflow_name: workflow.name,
    workflow_snapshot: clone(snapshot), workflow_director_node_id: workflow.director_node_id,
    result_node_ids: clone(workflow.result_node_ids || []), result_fields: clone(workflow.result_fields || []),
    document: clone(request.extension_document || request.source_document), outputs: [], segment_outputs: [], assembly_outputs: [],
    kind: "extension", preparation_kind: "continuation",
    parent_generation_id: generation.parent_generation_id,
    root_generation_id: generation.root_generation_id,
    depth: Number(generation.depth || 1),
    continuation_base_duration: Math.max(
      0,
      Number(
        generation.continuation_base_duration
        ?? (Number(generation.total_effective_duration || 0) - Number(generation.effective_duration || 0)),
      ),
    ),
    regenerated_from_generation_id: generation.id,
    new_seed: true,
    continuation_request: clone(request),
    created_at: Date.now(), updated_at: Date.now(),
  };
  project.generations.unshift(operation);
  const controller = new AbortController();
  state.generationControllers.set(operation.id, controller);
  markProjectChanged({ project, render: true });
  submit.disabled = true;
  try {
    await persistProjects({ immediate: true });
    const response = await api.fetchApi(CONTINUATION_PREPARE_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      body: JSON.stringify({
        document: request.source_document,
        ...(request.extension_document ? { extension_document: request.extension_document } : {}),
        source: request.source,
        brief: request.brief,
        duration_seconds: request.duration_seconds,
        context_frames: CONTINUATION_CONTEXT_FRAMES,
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "The replacement extension could not be prepared.");
    const director = snapshot.output?.[workflow.director_node_id];
    if (!director) throw new Error("The saved continuation workflow has no Director node.");
    director.inputs ||= {};
    director.inputs.document_json = JSON.stringify(data.document);
    randomizeSnapshotSeeds(snapshot);
    Object.assign(operation, { status: "queueing", document: clone(data.document), updated_at: Date.now() });
    markProjectChanged({ project, render: true });
    await queueSnapshot(project, workflow, snapshot, {
      ...data,
      kind: "extension",
      parent_generation_id: generation.parent_generation_id,
      root_generation_id: generation.root_generation_id,
      depth: Number(generation.depth || 1),
      total_effective_duration: operation.continuation_base_duration + Number(data.effective_duration || 0),
      continuation: {
        ...data.continuation,
        source_generation_id: generation.parent_generation_id,
        source_segments: clone(request.source_segments),
        parent_context_latent_path: request.parent_context_latent_path,
        brief: request.brief,
        structured: request.structured,
        source_assembly_segments: clone(request.source_assembly_segments),
      },
    }, operation);
    dialog.close();
    setStatus("Replacement extension queued from the same source ending.", "ready");
  } catch (error) {
    const cancelled = controller.signal.aborted || operation.status === "cancelled";
    Object.assign(operation, {
      status: cancelled ? "cancelled" : "error",
      error: cancelled ? "Cancelled." : (error.message || String(error)),
      updated_at: Date.now(),
    });
    markProjectChanged({ project, render: true });
    await persistProjects();
    if (!cancelled) throw error;
  } finally {
    state.generationControllers.delete(operation.id);
    submit.disabled = false;
  }
}

function showRegenerateExtension(generation) {
  const project = activeProject();
  if (!project || !generation || generation.kind !== "extension") return;
  let request;
  try {
    request = extensionRegenerationRequest(project, generation);
  } catch (error) {
    setStatus(error.message || String(error), "error");
    return;
  }
  const dialog = el("dialog", "psvstudio-continuation-dialog");
  dialog.setAttribute("aria-label", "Regenerate extension only");
  const heading = el("div", "psvstudio-continuation-heading");
  heading.append(
    el("h2", "", "Regenerate extension only"),
    el("p", "", "The original video and all earlier accepted segments stay untouched. The replacement branches from the same source ending with a new seed."),
  );
  const body = el("div", "psvstudio-continuation-body");
  if (request.structured) {
    body.append(el(
      "div", "psvstudio-continuation-note",
      "The current extension project shots, dialogue, camera, and sounds will be used. Close this dialog first if you want to edit them before regenerating.",
    ));
  } else {
    const briefInput = textArea(request.brief, value => { request.brief = value; }, 7, "Describe only what should happen in the replacement extension.");
    const durationInput = textInput(request.duration_seconds, value => { request.duration_seconds = value; }, "number");
    durationInput.min = "5";
    durationInput.max = "15";
    durationInput.step = "0.1";
    body.append(
      field("What should happen instead?", briefInput, "Revise only the new action. The immutable source ending remains the same."),
      field("Added duration (seconds)", durationInput, "The replacement is snapped to MiniMax's native frame grid."),
    );
  }
  body.append(el(
    "div", "psvstudio-continuation-note",
    "The previous result remains in immutable history so you can compare it or return to it later.",
  ));
  const footer = el("footer", "psvstudio-continuation-actions");
  const cancel = button("Cancel", () => dialog.close());
  const submit = button("Regenerate extension", async () => {
    request.brief = String(request.brief || "").trim();
    request.duration_seconds = Number(request.duration_seconds);
    if (!request.structured && !request.brief) {
      setStatus("Describe what should happen in the replacement extension.", "warning");
      return;
    }
    if (!Number.isFinite(request.duration_seconds) || request.duration_seconds < 5 || request.duration_seconds > 15) {
      setStatus("Extension duration must be between 5 and 15 seconds.", "warning");
      return;
    }
    try {
      await regenerateExtension(project, generation, request, dialog, submit);
    } catch (error) {
      setStatus(error.message || String(error), "error");
    }
  }, "psvstudio-button psvstudio-button-primary");
  footer.append(cancel, submit);
  dialog.append(heading, body, footer);
  dialog.addEventListener("cancel", event => {
    event.preventDefault();
    dialog.close();
  });
  dialog.addEventListener("close", () => dialog.remove(), { once: true });
  state.panel.ownerDocument.body.append(dialog);
  dialog.showModal();
}

function saveContinuationPlan(key, record) {
  const plans = storedObject(CONTINUATION_PLANS_KEY);
  plans[key] = { ...record, updated_at: Date.now() };
  const retained = Object.fromEntries(Object.entries(plans)
    .sort(([, left], [, right]) => Number(right.updated_at) - Number(left.updated_at)).slice(0, 20));
  // Starting/applying requires a durable request ID; storage failure is actionable.
  localStorage.setItem(CONTINUATION_PLANS_KEY, JSON.stringify(retained));
}

function showContinueVideo(generation) {
  const project = activeProject();
  if (!project || generation.status !== "complete" || !continuationSourceOutput(generation)) return;
  const key = [project.id, generation.id].join(":");
  let record = storedObject(CONTINUATION_PLANS_KEY)[key] || null;
  const dialog = el("dialog", "psvstudio-continuation-dialog");
  dialog.setAttribute("aria-label", "Continue video");
  const heading = el("div", "psvstudio-continuation-heading");
  heading.append(el("h2", "", "Continue video"), el("small", "", "Plan only the new tail after the selected render's exact ending."));
  let brief = record?.request?.brief || "Continue the action naturally from the exact ending, preserving the established subjects, environment, camera motion, lighting, and sound.";
  let duration = Number(record?.request?.duration_seconds || 5);
  let controller = null;
  let busy = false;
  const body = el("div", "psvstudio-continuation-body");
  const briefInput = textArea(brief, value => { brief = value; refresh(); }, 7, "Describe only what should happen next.");
  const durationInput = textInput(duration, value => { duration = Number(value); refresh(); }, "number");
  durationInput.min = "5"; durationInput.max = "15"; durationInput.step = "0.1";
  const progress = el("p", "psvstudio-continuation-progress", "");
  progress.setAttribute("role", "status");
  progress.setAttribute("aria-live", "polite");
  const preview = el("pre", "psvstudio-continuation-preview", "");
  preview.style.cssText = "white-space:pre-wrap;max-height:35vh;overflow:auto";
  body.append(
    field("What happens next?", briefInput, "Describe new action and exact spoken words after the source ending."),
    field("Added duration (seconds)", durationInput, "The plan is checked against the exact delivered tail after native frame-grid alignment."),
    el("div", "psvstudio-continuation-note", "Quick generate uses the brief directly. Build full extension asks the Director for structured shots, dialogue, camera, and sounds, then lets you review and apply. Both use the native 39-frame Soft AV handoff."),
    progress, preview,
  );
  const footer = el("footer", "psvstudio-continuation-actions");
  const close = button("Close", () => dialog.close());
  const submit = button("Quick generate", async () => {
    try {
      validateInputs();
      await queueContinuation(project, generation, brief.trim(), duration, dialog, submit);
    } catch (error) { progress.textContent = error.message || String(error); }
  }, "psvstudio-button psvstudio-button-primary");
  submit.textContent = "Quick generate";
  const build = button("Build full extension", () => runPlan(), "psvstudio-button psvstudio-button-primary");
  const cancel = button("Cancel planning", () => cancelPlan());
  const apply = button("Apply extension plan", async () => {
    try {
      if (!matches() || record.status !== "complete") throw new Error("Build a current plan before applying.");
      busy = true; refresh();
      const child = await createStructuredExtensionProject(project, record.source_generation, brief.trim(), duration, dialog, record);
      record.applied_project_id = child.id;
      saveContinuationPlan(key, record);
    } catch (error) { progress.textContent = error.message || String(error); }
    finally { busy = false; refresh(); }
  }, "psvstudio-button psvstudio-button-primary");
  footer.append(close, submit, build, cancel, apply);
  dialog.append(heading, body, footer);
  const matches = () => record?.request?.brief === brief.trim() && Number(record?.request?.duration_seconds) === duration;
  function validateInputs() {
    if (!brief.trim()) throw new Error("Describe what should happen in the extension.");
    if (!Number.isFinite(duration) || duration < 5 || duration > 15) throw new Error("Extension duration must be between 5 and 15 seconds.");
  }
  function refresh() {
    const cancelling = record?.status === "cancelling";
    briefInput.disabled = busy || cancelling;
    durationInput.disabled = busy || cancelling;
    build.disabled = busy || cancelling;
    submit.disabled = busy || cancelling;
    build.textContent = ["failed", "interrupted", "cancelled"].includes(record?.status) && matches() ? "Retry full extension" : "Build full extension";
    build.title = jobRetryText({ retry_action: "replan" });
    cancel.hidden = !(busy && ["queued", "running", "interrupted"].includes(record?.status)) && !cancelling;
    cancel.disabled = false;
    cancel.textContent = cancelling ? "Retry cancel" : "Cancel planning";
    apply.hidden = !matches() || record?.status !== "complete";
    apply.disabled = busy;
    preview.hidden = apply.hidden;
    preview.textContent = apply.hidden ? "" : String(record.result.compiled_prompt || "");
  }
  async function cancelPlan() {
    if (!record || ["complete", "failed", "cancelled"].includes(record.status)) return;
    controller?.abort();
    record.status = "cancelling";
    try {
      saveContinuationPlan(key, record);
      refresh();
      const response = await api.fetchApi(CONTINUATION_PLAN_ENDPOINT + "/" + encodeURIComponent(record.job_id) + "/cancel", { method: "POST" });
      const data = await response.json().catch(() => ({}));
      if (!response.ok && response.status !== 404) throw new Error(data.error || "Cancellation could not be confirmed. Retry cancel.");
      record.status = "cancelled";
      record.result = null;
      saveContinuationPlan(key, record);
      progress.textContent = "Planning cancelled. Your request is retained for retry.";
    } catch (error) { progress.textContent = error.message || String(error); }
    finally { busy = false; refresh(); }
  }
  async function runPlan() {
    if (busy) return;
    const active = new AbortController();
    controller = active;
    try {
      validateInputs();
      // Transport retries reuse the exact request; failed/cancelled work gets a new job.
      if (!matches() || !["queued", "running", "interrupted"].includes(record?.status)) {
        const jobId = makeId("extension-plan");
        record = {
          job_id: jobId, status: "queued", source_generation: clone(generation),
          request: { ...saveDirectorSettings(), job_id: jobId, origin: { project_id: project.id, message_id: generation.id || "" }, brief: brief.trim(), duration_seconds: duration,
            document: clone(generation.document), source_effective_duration: generation.effective_duration },
          result: null,
        };
      }
      saveContinuationPlan(key, record);
      busy = true; refresh();
      progress.textContent = "Extension plan is queued…";
      let response = await api.fetchApi(CONTINUATION_PLAN_ENDPOINT, {
        method: "POST", headers: { "Content-Type": "application/json" }, signal: active.signal,
        body: JSON.stringify(record.request),
      });
      let data = await response.json().catch(() => ({}));
      if (!response.ok) {
        record.status = response.status < 500 ? "failed" : "interrupted";
        throw new Error(data.error || "The extension plan could not be started.");
      }
      while (!active.signal.aborted) {
        response = await api.fetchApi(CONTINUATION_PLAN_ENDPOINT + "/" + encodeURIComponent(record.job_id), { signal: active.signal });
        data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || "Plan status was interrupted. Retry to reconnect.");
        const job = normalizeJobWire({ ...data, job_id: record.job_id });
        record.status = job.status;
        if (job.status === "complete") {
          if (!job.result?.valid || !job.result.document?.shots?.length) throw new Error("The planner returned an invalid document.");
          record.result = clone(job.result);
          saveContinuationPlan(key, record);
          progress.textContent = "Plan ready. Review the compiled prompt, then apply the extension project.";
          break;
        }
        if (job.status === "failed") throw recoveredJobError(data)
          || new Error(`${job.error || "Extension planning failed."} ${jobRetryText({ retry_action: "replan" })}`);
        if (job.status === "cancelled") throw new Error("Extension planning was cancelled. Your request is retained.");
        saveContinuationPlan(key, record);
        progress.textContent = job.status === "queued" ? "Extension plan is queued…" : directorJobStatusText(data);
        await new Promise(resolve => setTimeout(resolve, DIRECTOR_JOB_POLL_MS));
      }
    } catch (error) {
      if (!active.signal.aborted) {
        if (record && !["failed", "cancelled"].includes(record.status)) record.status = "interrupted";
        try { if (record) saveContinuationPlan(key, record); } catch (_) { /* Surface original storage/transport error. */ }
        progress.textContent = error.message || String(error);
      }
    } finally { if (controller === active) { busy = false; refresh(); } }
  }
  dialog.addEventListener("close", () => { controller?.abort(); dialog.remove(); }, { once: true });
  state.panel.ownerDocument.body.append(dialog);
  dialog.showModal();
  refresh();
  if (record?.status === "complete") progress.textContent = "Recovered completed plan. Apply it to open the extension project.";
  else if (["queued", "running"].includes(record?.status)) runPlan();
  else if (record?.status === "cancelling") progress.textContent = "Cancellation was interrupted. Retry cancel to confirm.";
  else if (record) progress.textContent = "Previous request retained. Retry full extension when ready.";
  briefInput.focus();
}

function showGenerationOutput(output, title) {
  const dialog = el("dialog", "psvstudio-media-preview-dialog");
  dialog.setAttribute("aria-label", title || "Video output");
  const header = el("header", "psvstudio-media-preview-header");
  header.append(el("h2", "", title), button("Close", () => dialog.close()));
  const body = el("div", "psvstudio-media-preview-body");
  const video = document.createElement("video");
  video.src = outputUrl(output);
  video.controls = true;
  video.preload = "metadata";
  enforceSingleVideoPlayback(video);
  body.append(video);
  dialog.append(header, body);
  dialog.addEventListener("close", () => dialog.remove(), { once: true });
  state.panel.ownerDocument.body.append(dialog);
  dialog.showModal();
}

function renderProjectList() {
  const list = state.panel?.querySelector("#psvstudio-project-list");
  if (!list) return;
  list.replaceChildren();
  if (!state.projects.length) {
    list.append(el("div", "psvstudio-empty", "No video projects yet."));
    return;
  }
  for (const project of [...state.projects].sort((left, right) => Number(right.updated_at) - Number(left.updated_at))) {
    const pendingCount = projectPendingGenerationCount(project);
    const row = el("div", "psvstudio-project-row");
    row.dataset.active = project.id === state.activeProjectId ? "true" : "false";
    const control = el("button", "psvstudio-project");
    control.type = "button";
    control.setAttribute("aria-current", project.id === state.activeProjectId ? "true" : "false");
    const projectKind = isStructuredExtensionProject(project) ? "Extension · " : "";
    control.append(
      el("strong", "", project.name || "Untitled video"),
      el("small", "", `${projectKind}${project.document?.shots?.length || 0} shot${project.document?.shots?.length === 1 ? "" : "s"} · ${project.generations?.length || 0} render${project.generations?.length === 1 ? "" : "s"}`),
    );
    control.addEventListener("click", () => selectProject(project.id));
    const remove = el("button", "psvstudio-project-delete", "Delete");
    remove.type = "button";
    const directorPending = projectHasPendingDirectorJob(project.id);
    remove.disabled = pendingCount > 0 || directorPending || (state.directorBusy && project.id === state.activeProjectId);
    remove.title = pendingCount
      ? "Wait for this session's queued video work to finish before deleting it."
      : directorPending || (state.directorBusy && project.id === state.activeProjectId)
        ? "Wait for Video Director to finish before deleting this session."
        : `Delete video session ${project.name || "Untitled video"}`;
    remove.setAttribute("aria-label", remove.title);
    remove.addEventListener("click", () => deleteProject(project.id));
    row.append(control, remove);
    list.append(row);
  }
  if (state.projectHasMore) {
    const older = button(state.projectPageLoading ? "Loading…" : "Load older projects", loadOlderProjects);
    older.disabled = Boolean(state.projectPageLoading);
    list.append(older);
  }
}

function renderWorkflowSelect() {
  const select = state.panel?.querySelector("#psvstudio-workflow");
  const project = activeProject();
  if (!select) return;
  select.replaceChildren();
  const empty = document.createElement("option");
  empty.value = "";
  empty.textContent = state.workflows.length ? "Choose workflow" : "No [PSV] workflow found";
  select.append(empty);
  for (const workflow of state.workflows) {
    const option = document.createElement("option");
    option.value = workflow.id;
    option.textContent = `${workflow.name}${workflow.stale ? " (cached)" : ""}`;
    select.append(option);
  }
  select.value = project?.workflow_id || "";
  select.disabled = !project || isStructuredExtensionProject(project);
  select.title = isStructuredExtensionProject(project)
    ? "Extensions use the immutable workflow snapshot saved with their source render."
    : "Choose a compatible [PSV] workflow.";
}

function latestOutput(project) {
  for (const generation of project?.generations || []) {
    if (generation.status === "complete" && generation.outputs?.length) return generation.outputs[0];
  }
  return null;
}

function enforceSingleVideoPlayback(video) {
  video.addEventListener("play", () => {
    for (const otherVideo of state.panel?.ownerDocument?.querySelectorAll("video") || []) {
      if (otherVideo !== video) otherVideo.pause();
    }
  });
}

function renderPreview() {
  const preview = state.panel?.querySelector("#psvstudio-preview");
  const project = activeProject();
  if (!preview) return;
  preview.replaceChildren();
  if (!project) {
    const empty = el("div", "psvstudio-preview-empty");
    empty.append(el("strong", "", "Create your first video project"), el("span", "", "Build manually, ask the Director, or combine both approaches."));
    empty.append(button("New video", newProject, "psvstudio-button psvstudio-button-primary"));
    preview.append(empty);
    return;
  }
  preview.append(
    el("span", "psvstudio-mode-badge", isStructuredExtensionProject(project) ? "EXTENSION" : localResolvedMode(project.document).toUpperCase()),
    renderTurboProfileIndicator(project, true),
  );
  preview.append(button("Global settings", () => showGlobalSettings(project), "psvstudio-button psvstudio-global-settings-button"));
  if (isStructuredExtensionProject(project)) {
    preview.append(el(
      "div", "psvstudio-extension-banner",
      `Structured extension · ${CONTINUATION_CONTEXT_FRAMES} source frames are carried before authored Shot 1 · use the per-shot Director from any Shot Editor`,
    ));
  }
  const output = latestOutput(project) || (isStructuredExtensionProject(project) ? project.extension_source.source : null);
  if (output) {
    const video = document.createElement("video");
    video.controls = true;
    video.preload = "metadata";
    video.src = outputUrl(output);
    enforceSingleVideoPlayback(video);
    preview.append(video);
  } else {
    const empty = el("div", "psvstudio-preview-empty");
    empty.append(
      el("strong", "", isStructuredExtensionProject(project) ? "Build the next segment" : "Your production starts here"),
      el("span", "", isStructuredExtensionProject(project)
        ? "Author shots, dialogue, camera, and sounds. Generation joins them seamlessly to the saved source ending."
        : "Describe the video, refine the selected shot, then generate an immutable workflow snapshot."),
    );
    preview.append(empty);
  }
}

function humanizePromptOption(value) {
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, letter => letter.toUpperCase());
}

function derivedReferenceTaskTypes(document) {
  const roles = new Set((document.references || []).flatMap(reference => reference.roles || []));
  const tasks = [];
  if ([...roles].some(role => ["first_frame", "last_frame"].includes(role))) tasks.push("keyframe completion");
  if ([...roles].some(role => ["subject", "scene", "style", "action", "pose", "camera", "storyboard"].includes(role))) tasks.push("reference generation");
  if (roles.has("video_edit")) tasks.push("video editing");
  if (roles.has("video_continue")) tasks.push("video continuation");
  if (roles.has("audio_copy")) tasks.push("audio reuse");
  if (roles.has("audio_reference")) tasks.push("audio reference");
  return tasks.length ? tasks : ["reference generation"];
}

function renderReferenceSemantics(project, refresh) {
  const section = inspectorDetails("Reference prompt semantics", localResolvedMode(project.document) === "ref2va");
  const references = (project.document.references || []).filter(reference => !(reference.kind === "audio"
    && (reference.roles || []).length === 1 && reference.roles[0] === "exact_audio"));
  if (!references.length) {
    section.body.append(el(
      "small", "psvstudio-help",
      exactAudioReferences(project).length
        ? "Exact audio is controlled inside each Shot Editor timeline and does not require REF2VA prompt semantics."
        : "Add media references to configure REF2VA source semantics here.",
    ));
    return section.details;
  }

  section.body.append(el(
    "div",
    "psvstudio-semantic-intro",
    "Manual REF2VA authoring: define every source, use every defined label in the summary and applicable shot or audio fields, and give every label one retention rule.",
  ));
  section.body.append(field(
    "Reference image fit",
    selectInput([
      { value: "match", label: "Match generation size" },
      { value: "max", label: "Use maximum reference size" },
    ], project.document.ref_image_size || "match", value => {
      project.document.ref_image_size = value;
      markProjectChanged();
    }),
  ));

  const sourceDetails = inspectorDetails("Source details");
  references.forEach(reference => {
    const card = el("div", "psvstudio-semantic-card");
    card.append(el("strong", "", `${reference.label || referenceDisplayLabel(references, reference)} · ${reference.name || reference.path || "Media"}`));
    card.append(field(
      "Source description",
      textArea(reference.prompt || "", value => {
        reference.prompt = value;
        markProjectChanged();
      }, 2, "Concrete source facts or manual reference guidance."),
      "Available to the Director and useful when maintaining source semantics manually.",
    ));
    if (reference.kind === "video") {
      const trimStart = textInput(reference.trim_start ?? 0, (value, control) => {
        reference.trim_start = control.value === "" ? 0 : Math.max(0, value);
        markProjectChanged();
      }, "number");
      trimStart.min = "0";
      trimStart.step = "0.01";
      const trimEnd = textInput(reference.trim_end ?? "", (value, control) => {
        reference.trim_end = control.value === "" ? null : Math.max(0, value);
        markProjectChanged();
      }, "number");
      trimEnd.min = "0";
      trimEnd.step = "0.01";
      const trims = el("div", "psvstudio-field-row");
      trims.append(field("Trim start (s)", trimStart), field("Trim end (s)", trimEnd, "Blank uses the source end."));
      card.append(trims, checkControl("Use embedded audio", reference.use_embedded_audio, value => {
        reference.use_embedded_audio = value;
        markProjectChanged();
      }));
    }
    sourceDetails.body.append(card);
  });
  section.body.append(sourceDetails.details);

  const taskDetails = inspectorDetails("Task types", true);
  const taskGrid = el("div", "psvstudio-semantic-checks");
  for (const task of state.config.task_types || []) {
    taskGrid.append(checkControl(humanizePromptOption(task), (project.document.task_types || []).includes(task), checked => {
      const current = new Set(project.document.task_types || []);
      if (checked) current.add(task);
      else current.delete(task);
      project.document.task_types = [...current];
      markProjectChanged();
    }));
  }
  taskDetails.body.append(taskGrid, button("Use media-role defaults", () => {
    project.document.task_types = derivedReferenceTaskTypes(project.document);
    markProjectChanged();
    refresh();
  }));
  section.body.append(taskDetails.details);

  section.body.append(field(
    "Reference summary",
    textArea(project.document.summary || "", value => {
      project.document.summary = value;
      markProjectChanged();
    }, 3, PLACEHOLDERS.referenceSummary),
    "Use every defined <Subject N>, <Picture N>, <Video N>, or <Audio N> label here.",
  ));

  const definitions = project.document.subject_definitions ||= [];
  const definitionDetails = inspectorDetails("Subject and source definitions", true);
  if (!definitions.length) definitionDetails.body.append(el("div", "psvstudio-empty", "No definitions yet. Every reference source must be represented."));
  definitions.forEach((definition, index) => {
    const card = el("div", "psvstudio-semantic-card");
    const row = el("div", "psvstudio-field-row");
    row.append(
      field("Label", textInput(definition.label || "", value => { definition.label = value; markProjectChanged(); }, "text", `Subject ${index + 1}`)),
      field("Definition", textArea(definition.text || "", value => { definition.text = value; markProjectChanged(); }, 3, PLACEHOLDERS.subjectDefinition)),
    );
    card.append(row, button("Remove definition", () => {
      definitions.splice(index, 1);
      markProjectChanged();
      refresh();
    }, "psvstudio-button psvstudio-button-danger"));
    definitionDetails.body.append(card);
  });
  definitionDetails.body.append(button("Add definition", () => {
    definitions.push({ label: `Subject ${definitions.length + 1}`, text: "" });
    markProjectChanged();
    refresh();
  }));
  section.body.append(definitionDetails.details);

  const retention = project.document.retention_analysis ||= [];
  const retentionDetails = inspectorDetails("Retention analysis", true);
  const relationshipOptions = [
    ...(state.config.visual_retention || []),
    ...(state.config.audio_retention || []),
  ].filter((value, index, values) => values.indexOf(value) === index)
    .map(value => ({ value, label: humanizePromptOption(value) }));
  if (!retention.length) retentionDetails.body.append(el("div", "psvstudio-empty", "No retention rules yet. Add exactly one for each defined label."));
  retention.forEach((item, index) => {
    const card = el("div", "psvstudio-semantic-card");
    const identity = el("div", "psvstudio-field-row");
    identity.append(
      field("Label", textInput(item.label || "", value => { item.label = value; markProjectChanged(); }, "text", "<Subject 1>")),
      field("Relationship", selectInput(relationshipOptions, item.relationship || "fully_preserved", value => { item.relationship = value; markProjectChanged(); })),
    );
    card.append(
      identity,
      field("Where retained", textInput(item.where || "", value => { item.where = value; markProjectChanged(); }, "text", PLACEHOLDERS.retentionWhere)),
      field("Retention detail", textArea(item.detail || "", value => { item.detail = value; markProjectChanged(); }, 2, PLACEHOLDERS.retentionDetail)),
      button("Remove retention rule", () => {
        retention.splice(index, 1);
        markProjectChanged();
        refresh();
      }, "psvstudio-button psvstudio-button-danger"),
    );
    retentionDetails.body.append(card);
  });
  retentionDetails.body.append(button("Add retention rule", () => {
    const definition = definitions[retention.length];
    const label = definition?.label ? `<${String(definition.label).replace(/[<>]/g, "")}>` : `<Subject ${retention.length + 1}>`;
    retention.push({ label, where: "[Shot 1]", relationship: "fully_preserved", detail: "" });
    markProjectChanged();
    refresh();
  }));
  section.body.append(retentionDetails.details);
  return section.details;
}

function appendGlobalSettings(container, project, refresh) {
  let turboIndicator = null;
  const refreshTurboIndicator = () => {
    if (turboIndicator?.isConnected) {
      const replacement = renderTurboProfileIndicator(project);
      turboIndicator.replaceWith(replacement);
      turboIndicator = replacement;
    }
    renderPreview();
  };
  if (isStructuredExtensionProject(project)) {
    const duration = textInput(project.document.duration_seconds, value => {
      if (!Number.isFinite(value)) return;
      project.document.duration_seconds = Math.min(15, Math.max(5, value));
      markProjectChanged();
    }, "number");
    duration.min = "5";
    duration.max = "15";
    duration.step = "0.1";
    const fixed = el("div", "psvstudio-extension-settings-note");
    fixed.append(
      el("strong", "", "Native structured extension"),
      el("span", "", `${project.document.width}×${project.document.height} source canvas · T2VA text document plus native audiovisual handoff`),
      el("small", "", `The first ${CONTINUATION_CONTEXT_FRAMES} frames are injected from the source and removed after generation. Authored cut times are offset automatically.`),
    );
    const row = el("div", "psvstudio-field-row psvstudio-canvas-row");
    row.append(field("Added duration", duration, "Each structured extension adds 5–15 seconds before native frame-grid snapping."));
    turboIndicator = renderTurboProfileIndicator(project);
    container.append(fixed, row, turboIndicator);
    const production = inspectorDetails("Extension production settings", true);
    production.body.append(
      field("Visual style", textArea(project.document.style, value => { project.document.style = value; markProjectChanged(); }, 2, PLACEHOLDERS.style)),
      field("Overall soundscape", textArea(project.document.overall_soundscape, value => { project.document.overall_soundscape = value; markProjectChanged(); }, 3, PLACEHOLDERS.soundscape)),
      field("Non-diegetic music", textArea(project.document.non_diegetic_music, value => { project.document.non_diegetic_music = value; markProjectChanged(); }, 2, PLACEHOLDERS.music)),
      checkControl("Complete silence", project.document.complete_silence, value => { project.document.complete_silence = value; markProjectChanged(); }),
    );
    container.append(production.details);
    return;
  }
  const sizeValue = `${project.document.width}x${project.document.height}`;
  const aspectOptions = state.config.aspect_presets.map(item => ({ value: `size:${item.width}x${item.height}`, label: item.label }));
  for (const reference of project.document.references || []) {
    const canvas = minimaxCanvasDimensions(
      reference.source_width,
      reference.source_height,
      project.document.target_megapixels,
    );
    if (!canvas || !["image", "video"].includes(reference.kind)) continue;
    const role = (reference.roles || []).some(value => CANVAS_MEDIA_ROLES.has(value)) ? "final-media" : "reference";
    aspectOptions.push({
      value: `media:${reference.id}`,
      label: `${reference.name || "Imported media"} · ${reference.source_width}×${reference.source_height} → ${canvas.width}×${canvas.height} (${role})`,
    });
  }
  if (!aspectOptions.some(item => item.value === `size:${sizeValue}`)) {
    aspectOptions.push({ value: `size:${sizeValue}`, label: `Custom ${project.document.width}×${project.document.height}` });
  }
  const linkedReference = (project.document.references || []).find(reference => reference.id === project.document.canvas_reference_id);
  const selectedCanvas = linkedReference && minimaxCanvasDimensions(
    linkedReference.source_width,
    linkedReference.source_height,
    project.document.target_megapixels,
  )
    ? `media:${linkedReference.id}`
    : `size:${sizeValue}`;
  const aspect = selectInput(aspectOptions, selectedCanvas, value => {
    if (value.startsWith("media:")) {
      const reference = project.document.references.find(item => item.id === value.slice(6));
      useReferenceCanvas(project, reference);
    } else {
      const [width, height] = value.slice(5).split("x").map(Number);
      project.document.width = width;
      project.document.height = height;
      project.document.canvas_reference_id = "";
    }
    markProjectChanged();
    refreshTurboIndicator();
  });
  const canvasRules = state.config.canvas || {};
  const targetMegapixelValue = Number(
    project.document.target_megapixels ?? canvasRules.default_megapixels ?? (768 * 1344) / 1_000_000,
  );
  const targetMegapixels = textInput(
    Number.isFinite(targetMegapixelValue) ? Number(targetMegapixelValue.toFixed(2)) : "",
    (value, control) => {
      if (!Number.isFinite(value)) return;
      const minimum = Number(canvasRules.minimum_megapixels) || 0.1;
      const maximum = Number(canvasRules.maximum_megapixels) || 4;
      project.document.target_megapixels = Math.min(maximum, Math.max(minimum, value));
      const activeLinkedReference = project.document.references.find(
        reference => reference.id === project.document.canvas_reference_id,
      );
      if (activeLinkedReference && useReferenceCanvas(project, activeLinkedReference)) {
        const canvas = minimaxCanvasDimensions(
          activeLinkedReference.source_width,
          activeLinkedReference.source_height,
          project.document.target_megapixels,
        );
        const option = [...aspect.options].find(item => item.value === `media:${activeLinkedReference.id}`);
        const role = (activeLinkedReference.roles || []).some(item => CANVAS_MEDIA_ROLES.has(item)) ? "final-media" : "reference";
        if (option && canvas) {
          option.textContent = `${activeLinkedReference.name || "Imported media"} · ${activeLinkedReference.source_width}×${activeLinkedReference.source_height} → ${canvas.width}×${canvas.height} (${role})`;
        }
      }
      markProjectChanged();
      refreshTurboIndicator();
    },
    "number",
  );
  targetMegapixels.min = String(canvasRules.minimum_megapixels ?? 0.1);
  targetMegapixels.max = String(canvasRules.maximum_megapixels ?? 4);
  targetMegapixels.step = "0.05";
  const duration = textInput(project.document.duration_seconds, (value, control) => {
    if (!Number.isFinite(value) || value <= 0) return;
    project.document.duration_seconds = Math.min(150, Math.max(0.25, value));
    control.nextElementSibling && (control.nextElementSibling.textContent = effectiveDurationHint(value));
    markProjectChanged();
  }, "number", PLACEHOLDERS.durationSeconds);
  duration.min = "0.25";
  duration.max = "150";
  duration.step = "0.25";
  const durationField = field("Requested duration", duration);
  durationField.append(el("small", "psvstudio-help", effectiveDurationHint(project.document.duration_seconds)));
  const row = el("div", "psvstudio-field-row psvstudio-canvas-row");
  row.append(
    field("Canvas", aspect, "Imported ratios keep their shape and snap to MiniMax H3's 32 px grid."),
    field("Target MP", targetMegapixels, "For imported media ratios."),
    durationField,
  );
  turboIndicator = renderTurboProfileIndicator(project);
  container.append(row, turboIndicator);

  const production = inspectorDetails("Production settings");
  production.body.append(
    field("Visual style", textArea(project.document.style, value => { project.document.style = value; markProjectChanged(); }, 2, PLACEHOLDERS.style)),
    field("Overall soundscape", textArea(project.document.overall_soundscape, value => { project.document.overall_soundscape = value; markProjectChanged(); }, 3, PLACEHOLDERS.soundscape)),
    field("Non-diegetic music", textArea(project.document.non_diegetic_music, value => { project.document.non_diegetic_music = value; markProjectChanged(); }, 2, PLACEHOLDERS.music)),
    checkControl("Complete silence", project.document.complete_silence, value => { project.document.complete_silence = value; markProjectChanged(); }),
    field("Mode override", selectInput(state.config.modes.map(value => ({ value, label: value === "auto" ? "Automatic (recommended)" : value.toUpperCase() })), project.document.mode, value => {
      project.document.mode = value;
      markProjectChanged({ render: true });
      refreshTurboIndicator();
    }), "Automatic selects the correct MiniMax route from your media roles."),
  );
  container.append(production.details, renderReferenceSemantics(project, refresh));
}

function globalSettingsSummary(project) {
  const mode = localResolvedMode(project.document).toUpperCase();
  const duration = Number(project.document.duration_seconds || 0).toFixed(2);
  return `${project.document.width}×${project.document.height} · ${duration}s · ${mode}`;
}

function appendProjectBrief(container, project) {
  const section = inspectorDetails("Production brief", true);
  const brief = textArea(project.brief, value => {
    project.brief = value;
    project.document.main_description = value;
    markProjectChanged();
  }, 4, PLACEHOLDERS.brief);
  const structuredExtension = isStructuredExtensionProject(project);
  section.body.append(field(
    structuredExtension ? "Extension overview" : "Video overview",
    brief,
    structuredExtension
      ? "Planning synopsis for this extension. Author prompt-relevant details in its shots; use the Shot director for focused assistance."
      : "Planning synopsis for you and the Video director. It is not sent to MiniMax; the director translates it into the required shot timeline.",
  ));

  if (structuredExtension) {
    section.body.append(el(
      "div", "psvstudio-extension-settings-note",
      "Video director is intentionally disabled for extension projects. The Shot director receives the source-ending handoff and can revise each authored extension shot.",
    ));
    container.append(section.details);
    return;
  }

  const command = textArea("", () => {}, 2, PLACEHOLDERS.directorCommand);
  const ask = button(
    "Ask Video director",
    () => openDirector("project", command.value.trim()),
    "psvstudio-button psvstudio-button-primary psvstudio-director-launch-button",
  );
  ask.title = "Consult about the entire video and review any proposed multi-shot changes before applying them.";
  const directorActions = el("div", "psvstudio-director-launch");
  directorActions.append(
    field("Video director instruction", command),
    ask,
    el("small", "psvstudio-help", `Full-video scope · ${project.document.shots.length} shot${project.document.shots.length === 1 ? "" : "s"} in context`),
  );
  section.body.append(directorActions);
  container.append(section.details);
}

function showGlobalSettings(project = activeProject()) {
  if (!project) return;
  const dialog = el("dialog", "psvstudio-global-settings-dialog");
  dialog.setAttribute("aria-label", "Global video settings");
  const render = () => {
    if (!dialog.isConnected) return;
    dialog.replaceChildren();
    const header = el("header", "psvstudio-global-settings-header");
    const heading = el("div", "psvstudio-global-settings-heading");
    heading.append(
      el("h2", "", "Global video settings"),
      el("small", "", `${globalSettingsSummary(project)} · Production brief, style, audio, mode, and reference semantics`),
    );
    header.append(heading, button("Close", () => dialog.close()));
    const body = el("div", "psvstudio-global-settings-body");
    appendProjectBrief(body, project);
    appendGlobalSettings(body, project, render);
    const footer = el("footer", "psvstudio-global-settings-footer");
    footer.append(
      el("small", "psvstudio-help", "Changes save automatically and apply to the whole project."),
      button("Done", () => dialog.close(), "psvstudio-button psvstudio-button-primary"),
    );
    dialog.append(header, body, footer);
  };
  dialog.addEventListener("cancel", event => {
    event.preventDefault();
    dialog.close();
  });
  dialog.addEventListener("close", () => {
    dialog.remove();
  }, { once: true });
  state.panel.ownerDocument.body.append(dialog);
  render();
  dialog.showModal();
}

function documentDuration(project = activeProject()) {
  return Math.max(0.25, Number(project?.document?.duration_seconds) || 5);
}

function captureShotDurations(project) {
  const duration = documentDuration(project);
  return new Map(project.document.shots.map((shot, index) => [
    shot.id,
    Math.max(0.25, Number(project.document.shots[index + 1]?.start ?? duration) - Number(shot.start || 0)),
  ]));
}

function clearTimelineDragArtifacts(track = state.panel?.querySelector("#psvstudio-timeline-track")) {
  track?.querySelectorAll(".psvstudio-timeline-drop-marker").forEach(marker => marker.remove());
  state.panel?.querySelectorAll(".psvstudio-shot-block.is-dragging").forEach(block => block.classList.remove("is-dragging"));
}

function beginShotPointerDrag(event, project, shot, block, track) {
  if (event.button !== 0 || event.target.closest(".psvstudio-trim-handle")) return;
  const ownerWindow = state.panel.ownerDocument.defaultView;
  const startX = event.clientX;
  const startY = event.clientY;
  let dragging = false;
  const move = moveEvent => {
    if (!dragging && Math.hypot(moveEvent.clientX - startX, moveEvent.clientY - startY) < 5) return;
    if (!dragging) {
      dragging = true;
      state.shotDrag = { projectId: project.id, id: shot.id, durations: captureShotDurations(project) };
      state.selectedShotId = shot.id;
      block.classList.add("is-dragging");
      state.panel.ownerDocument.body.classList.add("psvstudio-dragging-shot");
    }
    moveEvent.preventDefault();
    showTimelineDropMarker(track, timelineInsertionIndex(track, moveEvent.clientX));
  };
  const finish = finishEvent => {
    ownerWindow.removeEventListener("pointermove", move);
    ownerWindow.removeEventListener("pointerup", finish);
    ownerWindow.removeEventListener("pointercancel", cancel);
    state.panel.ownerDocument.body.classList.remove("psvstudio-dragging-shot");
    if (!dragging) return;
    const insertIndex = timelineInsertionIndex(track, finishEvent.clientX);
    reorderShot(project, insertIndex);
    state.shotDrag = null;
    clearTimelineDragArtifacts(track);
    renderTimeline();
    renderInspector();
  };
  const cancel = () => {
    ownerWindow.removeEventListener("pointermove", move);
    ownerWindow.removeEventListener("pointerup", finish);
    ownerWindow.removeEventListener("pointercancel", cancel);
    state.panel.ownerDocument.body.classList.remove("psvstudio-dragging-shot");
    state.shotDrag = null;
    clearTimelineDragArtifacts(track);
  };
  ownerWindow.addEventListener("pointermove", move, { passive: false });
  ownerWindow.addEventListener("pointerup", finish, { once: true });
  ownerWindow.addEventListener("pointercancel", cancel, { once: true });
}

function reorderShot(project, insertIndex) {
  const drag = state.shotDrag;
  if (!drag || drag.projectId !== project.id) return;
  const shot = project.document.shots.find(item => item.id === drag.id);
  const remaining = project.document.shots.filter(item => item.id !== drag.id);
  if (!shot) return;
  remaining.splice(Math.max(0, Math.min(insertIndex, remaining.length)), 0, shot);
  let start = 0;
  for (const item of remaining) {
    item.start = Math.round(start * 1000) / 1000;
    start += drag.durations.get(item.id) || 0.25;
  }
  project.document.shots = remaining;
  project.document.duration_seconds = Math.round(start * 1000) / 1000;
  state.selectedShotId = shot.id;
  markProjectChanged();
}

function timelineInsertionIndex(track, clientX) {
  const blocks = [...track.querySelectorAll(".psvstudio-shot-block")]
    .filter(block => block.dataset.shotId !== state.shotDrag?.id);
  return blocks.filter(block => {
    const rect = block.getBoundingClientRect();
    return clientX >= rect.left + rect.width / 2;
  }).length;
}

function showTimelineDropMarker(track, insertIndex) {
  track.querySelector(".psvstudio-timeline-drop-marker")?.remove();
  const blocks = [...track.querySelectorAll(".psvstudio-shot-block")]
    .filter(block => block.dataset.shotId !== state.shotDrag?.id);
  const marker = el("div", "psvstudio-timeline-drop-marker");
  const left = insertIndex < blocks.length
    ? blocks[insertIndex].offsetLeft - 2
    : blocks.length ? blocks.at(-1).offsetLeft + blocks.at(-1).offsetWidth : 0;
  marker.style.left = `${Math.max(0, left)}px`;
  track.append(marker);
}

function currentBoundary(project, boundaryIndex) {
  return boundaryIndex < project.document.shots.length
    ? Number(project.document.shots[boundaryIndex].start) || 0
    : documentDuration(project);
}

function setBoundary(project, boundaryIndex, requestedTime) {
  const shots = project.document.shots;
  if (boundaryIndex < 1 || boundaryIndex > shots.length) return;
  const minimum = (Number(shots[boundaryIndex - 1].start) || 0) + 0.25;
  const maximum = boundaryIndex < shots.length
    ? Number(shots[boundaryIndex + 1]?.start ?? documentDuration(project)) - 0.25
    : (isStructuredExtensionProject(project) ? 15 : 150);
  const time = Math.round(Math.max(minimum, Math.min(maximum, requestedTime)) * 1000) / 1000;
  if (boundaryIndex < shots.length) shots[boundaryIndex].start = time;
  else project.document.duration_seconds = time;
}

function refreshTimelineGeometry(track, project, scale) {
  const duration = documentDuration(project);
  track.style.width = `${Math.max(600, duration * scale)}px`;
  project.document.shots.forEach((shot, index) => {
    const start = Number(shot.start) || 0;
    const end = Number(project.document.shots[index + 1]?.start ?? duration);
    const block = track.querySelector(`[data-shot-id="${CSS.escape(shot.id)}"]`);
    if (!block) return;
    block.style.left = `${start * scale}px`;
    block.style.width = `${Math.max(30, (end - start) * scale - 3)}px`;
    const range = block.querySelector(".psvstudio-shot-range");
    if (range) range.textContent = `${start.toFixed(2)}–${end.toFixed(2)}s`;
    const durationLabel = block.querySelector(".psvstudio-shot-duration");
    if (durationLabel) durationLabel.textContent = `${(end - start).toFixed(2)}s`;
  });
}

function finishBoundaryResize(project) {
  project.document.shots.forEach((shot, index) => clampExistingShotTimeline(
    shot,
    Math.max(1 / 24, Number(project.document.shots[index + 1]?.start ?? documentDuration(project)) - Number(shot.start || 0)),
  ));
  markProjectChanged();
  renderTimeline();
  renderInspector();
}

function beginBoundaryResize(event, project, index, edge, track, scale) {
  event.preventDefault();
  event.stopPropagation();
  const boundaryIndex = edge === "left" ? index : index + 1;
  if (!boundaryIndex) return;
  state.selectedShotId = project.document.shots[index].id;
  const ownerWindow = state.panel.ownerDocument.defaultView;
  state.panel.ownerDocument.body.classList.add("psvstudio-resizing");
  const move = moveEvent => {
    const rect = track.getBoundingClientRect();
    const rawTime = (moveEvent.clientX - rect.left) / scale;
    const time = moveEvent.altKey ? rawTime : Math.round(rawTime * 24) / 24;
    setBoundary(project, boundaryIndex, time);
    refreshTimelineGeometry(track, project, scale);
    const start = Number(project.document.shots[index].start) || 0;
    const end = Number(project.document.shots[index + 1]?.start ?? documentDuration(project));
    setStatus(`Shot ${index + 1}: ${(end - start).toFixed(2)}s${moveEvent.altKey ? "" : " · snapped to 24 fps"}`, "working");
  };
  const finish = () => {
    ownerWindow.removeEventListener("pointermove", move);
    ownerWindow.removeEventListener("pointerup", finish);
    ownerWindow.removeEventListener("pointercancel", finish);
    state.panel.ownerDocument.body.classList.remove("psvstudio-resizing");
    finishBoundaryResize(project);
  };
  ownerWindow.addEventListener("pointermove", move);
  ownerWindow.addEventListener("pointerup", finish, { once: true });
  ownerWindow.addEventListener("pointercancel", finish, { once: true });
}

function trimHandle(project, index, edge, track, scale) {
  const boundaryIndex = edge === "left" ? index : index + 1;
  const handle = el("div", `psvstudio-trim-handle psvstudio-trim-${edge}${boundaryIndex ? "" : " is-disabled"}`);
  handle.role = "separator";
  handle.setAttribute("aria-orientation", "vertical");
  handle.setAttribute("aria-valuemin", "0");
  handle.setAttribute("aria-valuemax", String(documentDuration(project)));
  handle.setAttribute("aria-valuenow", String(currentBoundary(project, boundaryIndex)));
  handle.setAttribute("aria-valuetext", `${currentBoundary(project, boundaryIndex).toFixed(2)} seconds`);
  handle.setAttribute("aria-disabled", String(!boundaryIndex));
  handle.tabIndex = boundaryIndex ? 0 : -1;
  handle.ariaLabel = edge === "left" ? `Resize the start of shot ${index + 1}` : `Resize the end of shot ${index + 1}`;
  handle.title = boundaryIndex ? "Drag to resize. Hold Alt for sub-frame precision." : "The production starts at 0 seconds.";
  handle.addEventListener("pointerdown", resizeEvent => beginBoundaryResize(resizeEvent, project, index, edge, track, scale));
  handle.addEventListener("dragstart", dragEvent => dragEvent.preventDefault());
  handle.addEventListener("keydown", keyEvent => {
    if (!boundaryIndex || !["ArrowLeft", "ArrowRight"].includes(keyEvent.key)) return;
    keyEvent.preventDefault();
    const step = keyEvent.shiftKey ? 1 : 1 / 24;
    setBoundary(project, boundaryIndex, currentBoundary(project, boundaryIndex) + (keyEvent.key === "ArrowLeft" ? -step : step));
    finishBoundaryResize(project);
  });
  return handle;
}

function fitTimeline() {
  const viewport = state.panel?.querySelector("#psvstudio-shot-list");
  const project = activeProject();
  if (!viewport || !project) return;
  state.timelineZoom = Math.max(36, Math.min(160, (viewport.clientWidth - 34) / documentDuration(project)));
  const zoom = state.panel.querySelector("#psvstudio-timeline-zoom");
  if (zoom) zoom.value = String(Math.round(state.timelineZoom));
  renderTimeline();
}

function renderTimeline() {
  const viewport = state.panel?.querySelector("#psvstudio-shot-list");
  const project = activeProject();
  const add = state.panel?.querySelector("#psvstudio-add-shot");
  const director = state.panel?.querySelector("#psvstudio-video-director");
  if (!viewport) return;
  const previousScroll = viewport.scrollLeft;
  viewport.replaceChildren();
  if (add) add.disabled = !project;
  if (director) director.disabled = !project;
  if (!project) return;
  const duration = documentDuration(project);
  const minimumWidth = Math.max(420, viewport.clientWidth - 28);
  const scale = Math.max(state.timelineZoom, minimumWidth / duration);
  const timeline = el("div", "psvstudio-timeline");
  const ruler = el("div", "psvstudio-timeline-ruler");
  const track = el("div", "psvstudio-timeline-track");
  track.id = "psvstudio-timeline-track";
  track.role = "list";
  track.ariaLabel = "Video shots";
  const width = Math.max(minimumWidth, duration * scale);
  ruler.style.width = `${width}px`;
  track.style.width = `${width}px`;

  const tickStep = scale >= 100 ? 0.5 : 1;
  for (let time = 0; time <= duration + 0.001; time += tickStep) {
    const tick = el("span", Number.isInteger(time) ? "is-major" : "");
    tick.style.left = `${time * scale}px`;
    if (Number.isInteger(time)) tick.textContent = `${time}s`;
    ruler.append(tick);
  }

  project.document.shots.forEach((shot, index) => {
    const start = Number(shot.start) || 0;
    const end = Number(project.document.shots[index + 1]?.start ?? duration);
    const block = el("div", `psvstudio-shot-block${shot.id === state.selectedShotId ? " is-selected" : ""}`);
    block.dataset.shotId = shot.id;
    block.role = "listitem";
    block.ariaLabel = `Shot ${index + 1}. Drag to reorder. Press Alt plus Left or Right Arrow to move it.`;
    block.tabIndex = 0;
    block.title = "Drag to reorder · Alt + Left/Right Arrow also moves the shot";
    block.style.left = `${start * scale}px`;
    block.style.width = `${Math.max(30, (end - start) * scale - 3)}px`;
    const header = el("div", "psvstudio-shot-block-header");
    header.append(el("strong", "", `Shot ${index + 1}`), el("span", "psvstudio-shot-duration", `${(end - start).toFixed(2)}s`));
    block.append(
      trimHandle(project, index, "left", track, scale),
      header,
      el("span", "psvstudio-shot-range", `${start.toFixed(2)}–${end.toFixed(2)}s`),
      el("span", "psvstudio-shot-summary", shotStepSummary(shot)),
      el("span", "psvstudio-shot-camera", shot.camera?.type || "No camera movement"),
      trimHandle(project, index, "right", track, scale),
    );
    block.addEventListener("click", clickEvent => {
      if (clickEvent.target.closest(".psvstudio-trim-handle")) return;
      state.selectedShotId = shot.id;
      renderTimeline();
      renderInspector();
    });
    block.addEventListener("dblclick", clickEvent => {
      if (clickEvent.target.closest(".psvstudio-trim-handle")) return;
      state.selectedShotId = shot.id;
      openShotEditor(shot.id);
    });
    block.addEventListener("keydown", keyEvent => {
      if (["Enter", " "].includes(keyEvent.key)) {
        keyEvent.preventDefault();
        state.selectedShotId = shot.id;
        renderTimeline();
        renderInspector();
        return;
      }
      if (!keyEvent.altKey || !["ArrowLeft", "ArrowRight"].includes(keyEvent.key)) return;
      const destination = index + (keyEvent.key === "ArrowLeft" ? -1 : 1);
      if (destination < 0 || destination >= project.document.shots.length) return;
      keyEvent.preventDefault();
      state.shotDrag = { projectId: project.id, id: shot.id, durations: captureShotDurations(project) };
      reorderShot(project, keyEvent.key === "ArrowLeft" ? index - 1 : index + 1);
      state.shotDrag = null;
      renderTimeline();
      renderInspector();
    });
    block.addEventListener("pointerdown", pointerEvent => beginShotPointerDrag(pointerEvent, project, shot, block, track));
    track.append(block);
  });

  timeline.append(ruler, track);
  viewport.append(timeline);
  viewport.scrollLeft = previousScroll;
}

function renderMediaLane() {
  const lane = state.panel?.querySelector("#psvstudio-media-lane");
  const project = activeProject();
  if (!lane) return;
  lane.replaceChildren();
  const references = project?.document?.references || [];
  if (!references.length) {
    lane.append(el("span", "psvstudio-media-lane-empty", project ? "Drop media or paste an image" : "Create a project to add media"));
    return;
  }
  references.forEach((reference, index) => {
    ensureReferenceDimensions(project, reference);
    const card = el("div", "psvstudio-media-chip");
    card.dataset.referenceId = reference.id;
    card.dataset.referenceIndex = String(index);
    card.draggable = true;
    card.tabIndex = 0;
    card.title = "Drag to change reference order · Alt + Up/Down Arrow also moves it";
    card.ariaLabel = `${referenceDisplayLabel(references, reference)}: ${reference.name || reference.path || "Media"}. Drag to reorder.`;
    const url = mediaInputUrl(reference);
    const displayLabel = referenceDisplayLabel(references, reference);
    const sourceName = reference.name || reference.path || "Media";
    const thumbnailButton = button("", event => {
      event.stopPropagation();
      showMediaPreview(reference, displayLabel);
    }, "psvstudio-media-thumbnail");
    thumbnailButton.title = `Preview ${displayLabel}`;
    thumbnailButton.ariaLabel = thumbnailButton.title;
    thumbnailButton.draggable = false;
    if (reference.kind === "image" && url) {
      const thumbnail = document.createElement("img");
      thumbnail.src = url;
      thumbnail.alt = "";
      thumbnail.loading = "lazy";
      thumbnailButton.append(thumbnail);
    } else if (reference.kind === "video" && url) {
      const thumbnail = document.createElement("video");
      thumbnail.src = url;
      thumbnail.muted = true;
      thumbnail.preload = "metadata";
      thumbnail.tabIndex = -1;
      thumbnailButton.append(thumbnail);
    } else {
      thumbnailButton.append(el("span", "psvstudio-media-kind", reference.kind === "video" ? "VID" : reference.kind === "audio" ? "AUD" : "IMG"));
    }
    card.append(thumbnailButton);
    const details = el("span", "psvstudio-media-chip-details");
    const roleOptions = referenceRoleOptions(reference.kind);
    const currentRole = (reference.roles || [])[0] || roleOptions[0].value;
    const role = selectInput(roleOptions, currentRole, value => setReferenceRole(project, reference, value));
    role.className = "psvstudio-media-role";
    role.ariaLabel = `Role for ${displayLabel}`;
    role.title = "How this reference conditions MiniMax H3";
    const sourceSize = minimaxCanvasDimensions(reference.source_width, reference.source_height)
      ? ` · ${reference.source_width}×${reference.source_height}`
      : "";
    details.append(
      el("strong", "", displayLabel),
      el("small", "", `${sourceName}${sourceSize}`),
      role,
    );
    const remove = button("×", () => {
      removeProjectReference(project, reference, displayLabel);
    }, "psvstudio-media-remove");
    remove.title = `Remove ${displayLabel}`;
    remove.ariaLabel = remove.title;
    card.append(details, remove);
    card.addEventListener("pointerdown", event => {
      if (event.target.closest("select,button,input,textarea")) card.draggable = false;
    });
    const restoreDrag = () => { card.draggable = true; };
    card.addEventListener("pointerup", restoreDrag);
    card.addEventListener("pointercancel", restoreDrag);
    card.addEventListener("focusout", restoreDrag);
    card.addEventListener("dragstart", event => {
      state.mediaDragId = reference.id;
      card.classList.add("is-dragging");
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("application/x-promptstudio-video-reference", reference.id);
    });
    card.addEventListener("dragend", () => {
      state.mediaDragId = "";
      card.classList.remove("is-dragging");
      clearMediaDropMarkers(lane);
    });
    card.addEventListener("keydown", event => {
      if (!event.altKey || !["ArrowUp", "ArrowDown"].includes(event.key)) return;
      const direction = event.key === "ArrowUp" ? -1 : 1;
      const targetIndex = index + direction;
      if (targetIndex < 0 || targetIndex >= references.length) return;
      event.preventDefault();
      moveReference(project, reference.id, direction < 0 ? index - 1 : index + 2);
    });
    lane.append(card);
  });
  lane.addEventListener("dragover", event => {
    if (!state.mediaDragId) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    mediaDropDestination(lane, event.clientY);
  });
  lane.addEventListener("dragleave", event => {
    if (!lane.contains(event.relatedTarget)) clearMediaDropMarkers(lane);
  });
  lane.addEventListener("drop", event => {
    if (!state.mediaDragId) return;
    event.preventDefault();
    event.stopPropagation();
    const destination = mediaDropDestination(lane, event.clientY);
    const referenceId = state.mediaDragId;
    clearMediaDropMarkers(lane);
    moveReference(project, referenceId, destination);
  });
}

function showMediaPreview(reference, displayLabel = "Media") {
  const dialog = el("dialog", "psvstudio-media-preview-dialog");
  dialog.setAttribute("aria-label", `${displayLabel} preview`);
  const header = el("header", "psvstudio-media-preview-header");
  const heading = el("div", "psvstudio-media-preview-heading");
  heading.append(
    el("h2", "", displayLabel),
    el("small", "", reference.name || reference.path || humanizePromptOption(reference.kind)),
  );
  header.append(heading, button("Close", () => dialog.close()));
  const body = el("div", "psvstudio-media-preview-body");
  const url = mediaInputUrl(reference);
  if (!url) {
    body.append(el("div", "psvstudio-empty", "This media source is not available for preview."));
  } else if (reference.kind === "image") {
    const image = document.createElement("img");
    image.src = url;
    image.alt = reference.name || displayLabel;
    body.append(image);
  } else if (reference.kind === "video") {
    const video = document.createElement("video");
    video.src = url;
    video.controls = true;
    video.preload = "metadata";
    body.append(video);
    enforceSingleVideoPlayback(video);
  } else if (reference.kind === "audio") {
    body.append(el("strong", "", reference.name || "Audio reference"));
    const audio = document.createElement("audio");
    audio.src = url;
    audio.controls = true;
    audio.preload = "metadata";
    body.append(audio);
  }
  dialog.append(header, body);
  dialog.addEventListener("cancel", event => {
    event.preventDefault();
    dialog.close();
  });
  dialog.addEventListener("close", () => dialog.remove(), { once: true });
  state.panel.ownerDocument.body.append(dialog);
  dialog.showModal();
}

function showMediaLibrary(project = activeProject()) {
  if (!project) return;
  const dialog = el("dialog", "psvstudio-media-library-dialog");
  dialog.setAttribute("aria-label", "Project media");
  const render = () => {
    if (!dialog.isConnected) return;
    dialog.replaceChildren();
    const references = project.document.references || [];
    const header = el("header", "psvstudio-media-library-header");
    const heading = el("div", "psvstudio-media-library-heading");
    heading.append(
      el("h2", "", "Project media"),
      el("small", "", `${references.length} item${references.length === 1 ? "" : "s"} · Preview, configure, reorder, or remove references`),
    );
    header.append(heading, button("Close", () => dialog.close()));
    const list = el("div", "psvstudio-media-library-list");
    if (!references.length) {
      const empty = el("div", "psvstudio-empty", "No media has been added to this project yet.");
      empty.append(button("Add media", () => {
        dialog.close();
        state.panel.querySelector("#psvstudio-media-input")?.click();
      }, "psvstudio-button psvstudio-button-primary"));
      list.append(empty);
    }
    references.forEach((reference, index) => {
      ensureReferenceDimensions(project, reference);
      const displayLabel = referenceDisplayLabel(references, reference);
      const sourceName = reference.name || reference.path || "Media";
      const url = mediaInputUrl(reference);
      const card = el("article", "psvstudio-media-library-card");
      const preview = button("", () => showMediaPreview(reference, displayLabel), "psvstudio-media-library-preview");
      preview.title = `Preview ${displayLabel}`;
      preview.ariaLabel = preview.title;
      if (reference.kind === "image" && url) {
        const image = document.createElement("img");
        image.src = url;
        image.alt = "";
        image.loading = "lazy";
        preview.append(image);
      } else if (reference.kind === "video" && url) {
        const video = document.createElement("video");
        video.src = url;
        video.muted = true;
        video.preload = "metadata";
        video.tabIndex = -1;
        preview.append(video);
      } else {
        preview.append(el("span", "psvstudio-media-library-kind", reference.kind === "video" ? "VIDEO" : reference.kind === "audio" ? "AUDIO" : "IMAGE"));
      }

      const content = el("div", "psvstudio-media-library-content");
      const cardHeading = el("div", "psvstudio-media-library-card-heading");
      const identity = el("div");
      const sourceSize = reference.source_width && reference.source_height
        ? `${reference.source_width}×${reference.source_height}`
        : humanizePromptOption(reference.kind);
      identity.append(el("strong", "", displayLabel), el("small", "", `${sourceName} · ${sourceSize}`));
      const order = el("div", "psvstudio-inline");
      const up = button("↑", () => {
        moveReference(project, reference.id, index - 1);
        render();
      }, "psvstudio-button psvstudio-icon-button");
      const down = button("↓", () => {
        moveReference(project, reference.id, index + 2);
        render();
      }, "psvstudio-button psvstudio-icon-button");
      up.disabled = index === 0;
      down.disabled = index === references.length - 1;
      up.title = `Move ${displayLabel} up`;
      down.title = `Move ${displayLabel} down`;
      up.ariaLabel = up.title;
      down.ariaLabel = down.title;
      order.append(up, down);
      cardHeading.append(identity, order);

      const roleOptions = referenceRoleOptions(reference.kind);
      const currentRole = (reference.roles || [])[0] || roleOptions[0].value;
      const role = selectInput(roleOptions, currentRole, value => {
        setReferenceRole(project, reference, value);
        render();
      });
      const settings = el("div", "psvstudio-media-library-settings");
      settings.append(
        field("Media role", role, "How this source conditions MiniMax H3."),
        field("Source description", textArea(reference.prompt || "", value => {
          reference.prompt = value;
          markProjectChanged();
        }, 3, "Concrete source facts or manual reference guidance.")),
      );
      if (reference.kind === "video") {
        const trimStart = textInput(reference.trim_start ?? 0, (value, control) => {
          reference.trim_start = control.value === "" ? 0 : Math.max(0, value);
          markProjectChanged();
        }, "number");
        trimStart.min = "0";
        trimStart.step = "0.01";
        const trimEnd = textInput(reference.trim_end ?? "", (value, control) => {
          reference.trim_end = control.value === "" ? null : Math.max(0, value);
          markProjectChanged();
        }, "number");
        trimEnd.min = "0";
        trimEnd.step = "0.01";
        const trims = el("div", "psvstudio-field-row");
        trims.append(field("Trim start (s)", trimStart), field("Trim end (s)", trimEnd, "Blank uses source end."));
        settings.append(trims, checkControl("Use embedded audio", reference.use_embedded_audio, value => {
          reference.use_embedded_audio = value;
          markProjectChanged();
        }));
      }
      const actions = el("div", "psvstudio-media-library-actions");
      actions.append(button("Remove from project", () => {
        removeProjectReference(project, reference, displayLabel);
        render();
      }, "psvstudio-button psvstudio-button-danger"));
      content.append(cardHeading, settings, actions);
      card.append(preview, content);
      list.append(card);
    });
    const footer = el("footer", "psvstudio-media-library-footer");
    footer.append(
      el("small", "psvstudio-help", "Changes save automatically and apply to every shot that uses the reference."),
      button("Done", () => dialog.close(), "psvstudio-button psvstudio-button-primary"),
    );
    dialog.append(header, list, footer);
  };
  dialog.addEventListener("cancel", event => {
    event.preventDefault();
    dialog.close();
  });
  dialog.addEventListener("close", () => dialog.remove(), { once: true });
  state.panel.ownerDocument.body.append(dialog);
  render();
  dialog.showModal();
}

function inspectorDetails(title, open = false) {
  const details = el("details", "psvstudio-inspector-section");
  details.open = open;
  details.append(el("summary", "", title));
  const body = el("div", "psvstudio-inspector-section-body");
  details.append(body);
  return { details, body };
}

function appendDraftShotTextField(body, label, shot, name, rows = 3) {
  body.append(field(label, textArea(shot[name] || "", value => { shot[name] = value; }, rows, PLACEHOLDERS[name] || "")));
}

function moveShotStep(shot, stepId, destination) {
  const steps = ensureShotSteps(shot);
  const source = steps.findIndex(step => step.id === stepId);
  if (source < 0) return;
  const [step] = steps.splice(source, 1);
  const adjusted = source < destination ? destination - 1 : destination;
  steps.splice(Math.max(0, Math.min(steps.length, adjusted)), 0, step);
  renderShotEditorDialog();
}

function renderDialogueStep(body, step) {
  const speakerRow = el("div", "psvstudio-field-row");
  speakerRow.append(
    field("Speaker", textInput(step.speaker, value => { step.speaker = value; }, "text", PLACEHOLDERS.speaker)),
    field("Speaker ID", textInput(step.speaker_id, value => { step.speaker_id = value.toUpperCase(); }, "text", PLACEHOLDERS.speakerId)),
  );
  const flags = el("div", "psvstudio-field-row");
  const voiceover = checkControl("Voiceover", step.voiceover, value => { step.voiceover = value; });
  voiceover.querySelector("input").disabled = step.performance === "singing";
  flags.append(
    voiceover,
    checkControl("Off-screen", step.offscreen, value => { step.offscreen = value; }),
    checkControl("Crosses cut", step.crosses_cut, value => { step.crosses_cut = value; }),
    checkControl("Cut off", step.cutoff, value => { step.cutoff = value; }),
  );
  body.append(
    speakerRow,
    field("Performance", selectInput([
      { value: "speech", label: "Spoken dialogue" },
      { value: "singing", label: "Singing / lyrics" },
    ], step.performance || "speech", value => {
      step.performance = value;
      if (value === "singing") step.voiceover = false;
      renderShotEditorDialog();
    })),
    field("Language", textInput(step.language, value => { step.language = value; }, "text", PLACEHOLDERS.language)),
    field(step.performance === "singing" ? "Exact lyrics" : "Exact dialogue", textArea(step.text, value => { step.text = value; }, 3, PLACEHOLDERS.dialogue)),
    field("Delivery", textInput(step.delivery, value => { step.delivery = value; }, "text", PLACEHOLDERS.delivery)),
    flags,
  );
}

function collapsedStepSummary(step) {
  if (step.type === "dialogue") {
    const speaker = String(step.speaker || step.speaker_id || "The speaker").trim();
    return `${speaker}: ${String(step.text || "Empty dialogue line").trim()}`;
  }
  return String(step.text || "Empty action step").trim();
}

function renderShotSteps(container, shot) {
  const steps = ensureShotSteps(shot);
  container.replaceChildren();
  if (!steps.length) {
    container.append(el("div", "psvstudio-empty", "Add the first action or dialogue step. The compiler follows this list from top to bottom."));
  }
  steps.forEach((step, index) => {
    const expanded = state.shotEditorExpandedStepIds.has(step.id);
    const card = el("article", `psvstudio-step-card is-${step.type}${expanded ? "" : " is-collapsed"}`);
    card.dataset.stepId = step.id;
    const header = el("div", "psvstudio-step-header");
    const drag = el("span", "psvstudio-step-drag", "⋮⋮");
    drag.draggable = true;
    drag.title = "Drag to reorder this step";
    drag.addEventListener("dragstart", event => {
      state.shotStepDragId = step.id;
      card.classList.add("is-dragging");
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", step.id);
    });
    drag.addEventListener("dragend", () => {
      state.shotStepDragId = "";
      card.classList.remove("is-dragging");
    });
    const label = button("", () => {
      if (expanded) state.shotEditorExpandedStepIds.delete(step.id);
      else state.shotEditorExpandedStepIds.add(step.id);
      state.shotEditorRevealStepId = step.id;
      renderShotEditorDialog();
    }, "psvstudio-step-label");
    label.ariaExpanded = String(expanded);
    label.ariaLabel = `${expanded ? "Collapse" : "Expand"} step ${index + 1}`;
    label.append(
      el("span", "psvstudio-step-chevron", expanded ? "▾" : "▸"),
      el("strong", "", `Step ${index + 1}`),
      el("span", "psvstudio-step-kind", step.type === "dialogue" ? "Dialogue" : "Action"),
    );
    if (!expanded) label.append(el("span", "psvstudio-step-summary", collapsedStepSummary(step)));
    const controls = el("div", "psvstudio-step-controls");
    const up = button("↑", () => moveShotStep(shot, step.id, index - 1), "psvstudio-button psvstudio-icon-button");
    const down = button("↓", () => moveShotStep(shot, step.id, index + 2), "psvstudio-button psvstudio-icon-button");
    up.title = `Move step ${index + 1} earlier`;
    up.ariaLabel = up.title;
    down.title = `Move step ${index + 1} later`;
    down.ariaLabel = down.title;
    up.disabled = index === 0;
    down.disabled = index === steps.length - 1;
    const duplicate = button("Duplicate", () => {
      const copy = clone(step);
      copy.id = makeId(step.type === "dialogue" ? "dialogue" : "step");
      steps.splice(index + 1, 0, copy);
      state.shotEditorExpandedStepIds.add(copy.id);
      state.shotEditorRevealStepId = copy.id;
      renderShotEditorDialog();
    });
    const remove = button("Remove", () => {
      steps.splice(index, 1);
      state.shotEditorExpandedStepIds.delete(step.id);
      renderShotEditorDialog();
    }, "psvstudio-button psvstudio-button-danger");
    controls.append(up, down, duplicate, remove);
    header.append(drag, label, controls);
    const body = el("div", "psvstudio-step-body");
    if (step.type === "dialogue") {
      renderDialogueStep(body, step);
    } else {
      body.append(field("Visible action or state change", textArea(step.text, value => { step.text = value; }, 3, PLACEHOLDERS.action)));
    }
    body.hidden = !expanded;
    card.append(header, body);
    card.addEventListener("dragover", event => {
      if (!state.shotStepDragId || state.shotStepDragId === step.id) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
      card.dataset.drop = event.clientY < card.getBoundingClientRect().top + card.offsetHeight / 2 ? "before" : "after";
    });
    card.addEventListener("dragleave", () => { delete card.dataset.drop; });
    card.addEventListener("drop", event => {
      event.preventDefault();
      const destination = index + (card.dataset.drop === "after" ? 1 : 0);
      delete card.dataset.drop;
      moveShotStep(shot, state.shotStepDragId, destination);
      state.shotStepDragId = "";
    });
    container.append(card);
  });
}

function exactAudioReferences(project = activeProject()) {
  return (project?.document?.references || []).filter(reference => reference.kind === "audio"
    && (reference.roles || []).length === 1 && reference.roles[0] === "exact_audio");
}

function fileAudioDuration(file) {
  return new Promise(resolve => {
    const audio = document.createElement("audio");
    const url = URL.createObjectURL(file);
    const finish = value => {
      URL.revokeObjectURL(url);
      resolve(Number.isFinite(value) && value > 0 ? value : 1);
    };
    audio.preload = "metadata";
    audio.onloadedmetadata = () => finish(audio.duration);
    audio.onerror = () => finish(1);
    audio.src = url;
  });
}

function addExactAudioClip(shot, reference, duration, sourceDuration = 1) {
  const length = Math.max(1 / 1000, Math.min(duration, Number(sourceDuration) || 1));
  const clip = {
    id: makeId("audio-clip"), reference_id: reference.id, start: 0, end: length,
    source_start: 0, source_end: null, gain_db: 0, fade_in: 0, fade_out: 0,
    mix_mode: "overlay",
  };
  (shot.audio_clips ||= []).push(clip);
  state.shotTimelineSelectionId = clip.id;
  return clip;
}

async function importExactAudioFile(file, shot, duration) {
  const project = activeProject();
  if (!project || mediaKind(file) !== "audio") throw new Error("Choose a supported audio file.");
  const exactLimit = Number(state.config?.reference_limits?.exact_audio_assets || 32);
  if (exactAudioReferences(project).length >= exactLimit) {
    throw new Error(`The project already has the maximum of ${exactLimit} exact audio assets.`);
  }
  setStatus(`Uploading ${file.name}…`, "working");
  const browserDuration = await fileAudioDuration(file);
  const path = await uploadMediaFile(file);
  let metadata = {};
  try {
    metadata = await probeAudioPath(path);
  } catch (_) {
    metadata = { duration_seconds: browserDuration };
  }
  const sourceDuration = Number(metadata.duration_seconds) || browserDuration;
  const reference = {
    id: makeId("reference"), kind: "audio", path, name: file.name || path.split("/").pop(),
    roles: ["exact_audio"], prompt: "", label: "", trim_start: 0, trim_end: null,
    use_embedded_audio: false, source_width: 0, source_height: 0,
    duration_seconds: sourceDuration, audio_codec: metadata.codec || "",
    audio_sample_rate: Number(metadata.sample_rate) || 0,
  };
  (project.document.references ||= []).push(reference);
  addExactAudioClip(shot, reference, duration, sourceDuration);
  markProjectChanged();
  setStatus(`${reference.name} added as exact timeline audio.`, "ready");
}

function snapShotTime(value, exact = false, fine = false) {
  const step = exact && fine ? 0.001 : 1 / 24;
  return Math.round(Number(value || 0) / step) * step;
}

function sortTimedShotItems(shot) {
  ensureShotSteps(shot);
  syncShotSoundCues(shot);
  shot.sounds = shot.sound_cues.map(cue => cue.text);
}

function beginShotTimelineDrag(event, item, duration, block, {
  exact = false, edge = "move", displayStart = 0, displayEnd = 1 / 24,
} = {}) {
  if (event.button != null && event.button !== 0) return;
  event.preventDefault();
  event.stopPropagation();
  const track = block.parentElement;
  const bounds = track.getBoundingClientRect();
  const pointerStart = event.clientX;
  const pointerId = event.pointerId;
  const scheduled = Number.isFinite(Number(item.start)) && Number.isFinite(Number(item.end))
    && Number(item.end) > Number(item.start);
  const initialStart = scheduled ? Number(item.start) : Number(displayStart);
  const initialEnd = scheduled ? Number(item.end) : Number(displayEnd);
  const minimum = exact ? 0.001 : 1 / 24;
  let moved = false;
  block.classList.add("is-dragging", `is-dragging-${edge}`);
  block.setAttribute("aria-grabbed", "true");
  try {
    if (pointerId != null && block.setPointerCapture) block.setPointerCapture(pointerId);
  } catch (_) {
    // Document-level listeners below remain a safe fallback in embedded browsers.
  }
  const updateBlock = () => {
    block.style.left = `${item.start / duration * 100}%`;
    block.style.width = `${Math.max(.25, (item.end - item.start) / duration * 100)}%`;
    block.querySelector("small").textContent = `${item.start.toFixed(exact ? 3 : 2)}–${item.end.toFixed(exact ? 3 : 2)}s`;
    block.setAttribute("aria-label", `${block.dataset.cueLabel}. Start ${item.start.toFixed(exact ? 3 : 2)} seconds, end ${item.end.toFixed(exact ? 3 : 2)} seconds.`);
  };
  const ownerDocument = block.ownerDocument;
  const move = moveEvent => {
    if (pointerId != null && moveEvent.pointerId != null && moveEvent.pointerId !== pointerId) return;
    if (Math.abs(moveEvent.clientX - pointerStart) < 1) return;
    moved = true;
    const delta = (moveEvent.clientX - pointerStart) / Math.max(1, bounds.width) * duration;
    if (edge === "left") {
      item.start = Math.max(0, Math.min(initialEnd - minimum, snapShotTime(initialStart + delta, exact, moveEvent.altKey)));
    } else if (edge === "right") {
      item.end = Math.min(duration, Math.max(initialStart + minimum, snapShotTime(initialEnd + delta, exact, moveEvent.altKey)));
    } else {
      const length = initialEnd - initialStart;
      item.start = Math.max(0, Math.min(duration - length, snapShotTime(initialStart + delta, exact, moveEvent.altKey)));
      item.end = item.start + length;
    }
    if (!exact) item.timing_explicit = true;
    block.classList.remove("is-auto");
    updateBlock();
  };
  const finish = finishEvent => {
    if (pointerId != null && finishEvent?.pointerId != null && finishEvent.pointerId !== pointerId) return;
    ownerDocument.removeEventListener("pointermove", move, true);
    ownerDocument.removeEventListener("pointerup", finish, true);
    ownerDocument.removeEventListener("pointercancel", finish, true);
    try {
      if (pointerId != null && block.hasPointerCapture?.(pointerId)) block.releasePointerCapture(pointerId);
    } catch (_) {
      // The browser may already have released capture on pointerup.
    }
    block.classList.remove("is-dragging", `is-dragging-${edge}`);
    block.setAttribute("aria-grabbed", "false");
    if (moved) sortTimedShotItems(state.shotEditorDraft);
    state.shotTimelineSelectionId = item.id;
    renderShotEditorDialog();
  };
  ownerDocument.addEventListener("pointermove", move, true);
  ownerDocument.addEventListener("pointerup", finish, true);
  ownerDocument.addEventListener("pointercancel", finish, true);
}

function shotTimelineDragEdge(event, block) {
  if (event.target.closest?.(".psvstudio-shot-cue-handle.is-left")) return "left";
  if (event.target.closest?.(".psvstudio-shot-cue-handle.is-right")) return "right";
  const bounds = block.getBoundingClientRect();
  const edgeWidth = Math.min(14, Math.max(7, bounds.width / 3));
  if (event.clientX - bounds.left <= edgeWidth) return "left";
  if (bounds.right - event.clientX <= edgeWidth) return "right";
  return "move";
}

function renderShotTimelineBlock(track, item, duration, kind, label, exact = false, displayRange = null) {
  const scheduled = Number.isFinite(Number(item.start)) && Number.isFinite(Number(item.end))
    && Number(item.end) > Number(item.start);
  const start = scheduled ? Number(item.start) : Number(displayRange?.start || 0);
  const end = scheduled ? Number(item.end) : Number(displayRange?.end || Math.min(duration, start + 1 / 24));
  const block = el("button", `psvstudio-shot-cue is-${kind}${scheduled ? "" : " is-auto"}${state.shotTimelineSelectionId === item.id ? " is-selected" : ""}`);
  block.type = "button";
  block.dataset.cueLabel = label;
  block.style.left = `${start / duration * 100}%`;
  block.style.width = `${Math.max(.25, (end - start) / duration * 100)}%`;
  block.title = scheduled
    ? `${label} · drag to move · drag either edge to change start/end`
    : `${label} · automatic chronological flow · drag to author timing`;
  block.setAttribute("aria-label", scheduled
    ? `${label}. Start ${start.toFixed(exact ? 3 : 2)} seconds, end ${end.toFixed(exact ? 3 : 2)} seconds. Drag to move or resize.`
    : `${label}. Automatic chronological flow. Drag to assign timing.`);
  block.setAttribute("aria-grabbed", "false");
  block.append(
    el("span", "psvstudio-shot-cue-handle is-left"),
    el("strong", "", label),
    el("small", "", scheduled ? `${start.toFixed(exact ? 3 : 2)}–${end.toFixed(exact ? 3 : 2)}s` : "Automatic flow"),
    el("span", "psvstudio-shot-cue-handle is-right"),
  );
  block.addEventListener("click", () => {
    state.shotTimelineSelectionId = item.id;
    renderShotEditorDialog();
  });
  block.addEventListener("pointerdown", event => {
    const edge = shotTimelineDragEdge(event, block);
    beginShotTimelineDrag(event, item, duration, block, {
      exact, edge, displayStart: start, displayEnd: end,
    });
  });
  track.append(block);
}

function renderShotTimelineLane(timeline, label, kind, items, duration, name, exact = false) {
  const lane = el("div", `psvstudio-shot-timeline-lane is-${kind}`);
  lane.append(el("div", "psvstudio-shot-timeline-lane-label", label));
  const track = el("div", "psvstudio-shot-timeline-track");
  const slot = duration / Math.max(items.length, 1);
  items.forEach((item, index) => {
    const start = index * slot;
    const visualLength = Math.min(slot * .82, Math.max(.5, duration * .25));
    const end = Math.min(duration, Math.max(start + 1 / 24, start + visualLength));
    renderShotTimelineBlock(track, item, duration, kind, name(item), exact, { start, end });
  });
  lane.append(track);
  timeline.append(lane);
}

function timingNumberField(label, item, name, duration, render, { exact = false, nullable = false } = {}) {
  const input = textInput(item[name] ?? "", (value, control) => {
    if (nullable && control.value === "") item[name] = null;
    else item[name] = Math.max(0, Number(value) || 0);
    if (["start", "end"].includes(name)) {
      const minimum = exact ? .001 : 1 / 24;
      item.start = Math.min(duration - minimum, Number(item.start || 0));
      item.end = Math.min(duration, Math.max(item.start + minimum, Number(item.end || 0)));
      if (!exact) item.timing_explicit = true;
    }
  }, "number");
  input.min = "0";
  input.max = String(duration);
  input.step = String(exact ? .001 : 1 / 24);
  input.addEventListener("change", render);
  return field(label, input);
}

function renderShotTimelineInspector(container, shot, duration) {
  const steps = ensureShotSteps(shot);
  const cues = syncShotSoundCues(shot);
  const clips = shot.audio_clips || [];
  const item = [...steps, ...cues, ...clips].find(value => value.id === state.shotTimelineSelectionId);
  if (!item) {
    container.append(el("div", "psvstudio-shot-timeline-hint", "Select a timeline item to edit precise timing. Hold Alt while dragging exact audio for 1 ms positioning."));
    return;
  }
  const exact = clips.includes(item);
  const automatic = !exact && (item.start == null || item.end == null);
  const row = el("div", "psvstudio-shot-timeline-inspector-row");
  row.append(
    timingNumberField("Start in shot (s)", item, "start", duration, renderShotEditorDialog, { exact }),
    timingNumberField("End in shot (s)", item, "end", duration, renderShotEditorDialog, { exact }),
  );
  if (steps.includes(item)) {
    const badge = el("span", "psvstudio-shot-timeline-guarantee is-generated", "Generated timing guidance");
    const open = button("Open event details", () => {
      state.shotEditorExpandedStepIds.add(item.id);
      state.shotEditorRevealStepId = item.id;
      renderShotEditorDialog();
    });
    const automaticFlow = button("Use automatic flow", () => {
      delete item.start;
      delete item.end;
      delete item.timing_explicit;
      renderShotEditorDialog();
    });
    automaticFlow.disabled = automatic;
    container.append(badge, row, automaticFlow, open);
    return;
  }
  if (cues.includes(item)) {
    const automaticFlow = button("Use automatic flow", () => {
      delete item.start;
      delete item.end;
      delete item.timing_explicit;
      renderShotEditorDialog();
    });
    automaticFlow.disabled = automatic;
    container.append(
      el("span", "psvstudio-shot-timeline-guarantee is-generated", "Generated timing guidance"),
      row,
      automaticFlow,
      field("Sound description", textInput(item.text, value => {
        item.text = value;
        shot.sounds = cues.map(cue => cue.text).filter(Boolean);
      }, "text")),
      button("Remove sound", () => {
        shot.sound_cues = cues.filter(cue => cue.id !== item.id);
        shot.sounds = shot.sound_cues.map(cue => cue.text);
        state.shotTimelineSelectionId = "";
        renderShotEditorDialog();
      }, "psvstudio-button psvstudio-button-danger"),
    );
    return;
  }
  const project = activeProject();
  const references = exactAudioReferences(project);
  const activeReference = references.find(reference => reference.id === item.reference_id);
  const sourceEnd = timingNumberField("Source out (s)", item, "source_end", 86400, renderShotEditorDialog, { exact: true, nullable: true });
  row.append(
    timingNumberField("Source in (s)", item, "source_start", 86400, renderShotEditorDialog, { exact: true }),
    sourceEnd,
  );
  const gain = textInput(item.gain_db ?? 0, value => { item.gain_db = Math.max(-60, Math.min(24, Number(value) || 0)); }, "number");
  gain.min = "-60"; gain.max = "24"; gain.step = ".1";
  const fades = el("div", "psvstudio-field-row");
  fades.append(
    timingNumberField("Fade in (s)", item, "fade_in", duration, renderShotEditorDialog, { exact: true }),
    timingNumberField("Fade out (s)", item, "fade_out", duration, renderShotEditorDialog, { exact: true }),
  );
  container.append(
    el("span", "psvstudio-shot-timeline-guarantee is-exact", "Exact post-render media"),
    el(
      "div", "psvstudio-shot-timeline-source-duration",
      `${activeReference?.name || "Audio source"} · source duration ${Number(activeReference?.duration_seconds || 0).toFixed(3)}s · placed duration ${(Number(item.end) - Number(item.start)).toFixed(3)}s`,
    ),
    row,
    field("Audio asset", selectInput(references.map(reference => ({
      value: reference.id, label: reference.name || reference.path,
    })), item.reference_id, value => { item.reference_id = value; })),
    field("Mix mode", selectInput([
      { value: "overlay", label: "Overlay generated audio" },
      { value: "replace", label: "Replace generated audio in range" },
    ], item.mix_mode, value => { item.mix_mode = value; })),
    field("Gain (dB)", gain),
    fades,
    button("Remove audio clip", () => {
      shot.audio_clips = clips.filter(clip => clip.id !== item.id);
      state.shotTimelineSelectionId = "";
      renderShotEditorDialog();
    }, "psvstudio-button psvstudio-button-danger"),
  );
}

function renderShotDetailTimeline(shot, duration) {
  const project = activeProject();
  const { steps, cues, clips } = ensureShotTimeline(shot, duration);
  const wrapper = el("section", "psvstudio-shot-detail-timeline");
  const header = el("div", "psvstudio-shot-detail-timeline-header");
  const heading = el("div", "");
  heading.append(
    el("h3", "", "Inside this shot"),
    el("small", "", `Shot-local timing · ${duration.toFixed(3)}s · drag blocks to move · drag edges to change start/end · overlaps allowed`),
  );
  const controls = el("div", "psvstudio-inline");
  const exactReferences = exactAudioReferences(project);
  exactReferences.forEach(reference => ensureAudioReferenceDuration(project, reference));
  const audioSelect = selectInput(exactReferences.map(reference => ({
    value: reference.id, label: reference.name || reference.path,
  })), exactReferences[0]?.id || "", () => {});
  audioSelect.disabled = !exactReferences.length;
  audioSelect.setAttribute("aria-label", "Exact audio reference");
  const fileInput = el("input", "psvstudio-sr-only");
  fileInput.type = "file";
  fileInput.setAttribute("aria-label", "Import exact audio");
  fileInput.accept = "audio/*,.wav,.wave,.mp3,.flac,.ogg,.oga,.opus,.m4a,.aac,.aif,.aiff,.wma,.caf,.au";
  fileInput.addEventListener("change", async () => {
    const file = fileInput.files?.[0];
    if (!file) return;
    try {
      await importExactAudioFile(file, shot, duration);
      renderShotEditorDialog();
    } catch (error) {
      setStatus(error.message || String(error), "error");
    }
  });
  controls.append(
    button("Add sound", () => {
      const cue = { id: makeId("sound"), text: "New synchronized sound" };
      (shot.sound_cues ||= []).push(cue);
      shot.sounds = shot.sound_cues.map(item => item.text);
      state.shotTimelineSelectionId = cue.id;
      renderShotEditorDialog();
    }),
    audioSelect,
    button("Place audio", () => {
      const reference = exactReferences.find(item => item.id === audioSelect.value);
      if (!reference) return;
      addExactAudioClip(shot, reference, duration, reference.duration_seconds || 1);
      renderShotEditorDialog();
    }),
    button("Import audio", () => fileInput.click(), "psvstudio-button psvstudio-button-primary"),
    fileInput,
  );
  header.append(heading, controls);
  const ruler = el("div", "psvstudio-shot-timeline-ruler");
  ruler.append(el("span", "psvstudio-shot-timeline-ruler-label", ""));
  const rulerTrack = el("div", "psvstudio-shot-timeline-ruler-track");
  const ticks = Math.max(2, Math.min(12, Math.ceil(duration * 2)));
  for (let index = 0; index <= ticks; index += 1) {
    const tick = el("span", index % 2 ? "" : "is-major", `${(duration * index / ticks).toFixed(2)}s`);
    tick.style.left = `${index / ticks * 100}%`;
    rulerTrack.append(tick);
  }
  ruler.append(rulerTrack);
  const timeline = el("div", "psvstudio-shot-timeline-lanes");
  renderShotTimelineLane(timeline, "ACTION", "action", steps.filter(step => step.type === "action"), duration, item => item.text || "Action");
  renderShotTimelineLane(timeline, "DIALOGUE", "dialogue", steps.filter(step => step.type === "dialogue"), duration, item => item.text || "Dialogue");
  renderShotTimelineLane(timeline, "SOUND", "sound", cues, duration, item => item.text || "Sound");
  renderShotTimelineLane(timeline, "EXACT AUDIO", "audio", clips, duration, item => {
    const reference = exactReferences.find(value => value.id === item.reference_id);
    const sourceDuration = Number(reference?.duration_seconds || 0);
    return `${reference?.name || "Audio clip"}${sourceDuration ? ` · ${sourceDuration.toFixed(3)}s source` : ""}`;
  }, true);
  const inspector = el("div", "psvstudio-shot-timeline-inspector");
  renderShotTimelineInspector(inspector, shot, duration);
  wrapper.append(header, ruler, timeline, inspector);
  return wrapper;
}

function shotEditorChanged() {
  return Boolean(state.shotEditorDraft && state.shotEditorOriginal
    && JSON.stringify(state.shotEditorDraft) !== JSON.stringify(state.shotEditorOriginal));
}

function closeShotEditor({ force = false } = {}) {
  const dialog = state.shotEditorDialog;
  if (!dialog) return true;
  if (!force && shotEditorChanged()) {
    const view = state.panel?.ownerDocument.defaultView;
    if (!view?.confirm("Discard the unsaved changes to this shot?")) return false;
  }
  if (dialog.open) dialog.close();
  dialog.remove();
  state.shotEditorDialog = null;
  state.shotEditorDraft = null;
  state.shotEditorOriginal = null;
  state.shotEditorShotId = "";
  state.shotStepDragId = "";
  state.shotEditorExpandedStepIds = new Set();
  state.shotEditorRevealStepId = "";
  state.shotTimelineSelectionId = "";
  return true;
}

function saveShotEditor({ askDirector = false } = {}) {
  const project = activeProject();
  const draft = state.shotEditorDraft;
  const index = project?.document?.shots?.findIndex(shot => shot.id === state.shotEditorShotId) ?? -1;
  if (!project || !draft || index < 0) return;
  const invalidSpeaker = ensureShotSteps(draft).find(step => (
    step.type === "dialogue" && !/^S\d+(?:,S\d+)*$/.test(String(step.speaker_id || "").toUpperCase())
  ));
  if (invalidSpeaker) {
    state.panel.ownerDocument.defaultView?.alert("Speaker IDs must use S1, S2, or a compound ID such as S1,S2.");
    return;
  }
  project.document.shots[index] = clone(draft);
  state.selectedShotId = draft.id;
  closeShotEditor({ force: true });
  markProjectChanged({ render: true });
  setStatus(`Shot ${index + 1} saved with ${draft.steps.length} chronological step${draft.steps.length === 1 ? "" : "s"}.`, "ready");
  if (askDirector) openDirector("shot");
}

function removeShotFromEditor() {
  const project = activeProject();
  if (!project || project.document.shots.length < 2) return;
  const index = project.document.shots.findIndex(shot => shot.id === state.shotEditorShotId);
  if (index < 0) return;
  const view = state.panel.ownerDocument.defaultView;
  if (!view?.confirm(`Remove Shot ${index + 1}?`)) return;
  closeShotEditor({ force: true });
  project.document.shots.splice(index, 1);
  project.document.shots[0].start = 0;
  state.selectedShotId = project.document.shots[Math.max(0, index - 1)]?.id || project.document.shots[0]?.id;
  markProjectChanged({ render: true });
}

function renderShotEditorDialog() {
  const dialog = state.shotEditorDialog;
  const draft = state.shotEditorDraft;
  const project = activeProject();
  const index = project?.document?.shots?.findIndex(shot => shot.id === state.shotEditorShotId) ?? -1;
  if (!dialog || !draft || !project || index < 0) return;
  const previousStepScroll = dialog.querySelector(".psvstudio-step-list")?.scrollTop || 0;
  ensureShotSteps(draft);
  dialog.replaceChildren();

  const end = Number(project.document.shots[index + 1]?.start ?? project.document.duration_seconds);
  const header = el("header", "psvstudio-shot-editor-header");
  const heading = el("div", "psvstudio-shot-editor-heading");
  heading.append(
    el("h2", "", `Edit Shot ${index + 1}`),
    el("small", "", `${Number(draft.start || 0).toFixed(2)}–${end.toFixed(2)}s · ${draft.steps.length} chronological step${draft.steps.length === 1 ? "" : "s"}`),
  );
  const close = button("×", () => closeShotEditor(), "psvstudio-button psvstudio-icon-button");
  close.title = "Close shot editor";
  close.ariaLabel = close.title;
  header.append(heading, close);

  const workspace = el("div", "psvstudio-shot-editor-workspace");
  const setup = el("div", "psvstudio-shot-editor-setup");
  const essentials = inspectorDetails("Shot setup", true);
  if (index > 0) {
    const start = textInput(draft.start, value => {
      if (!Number.isFinite(value)) return;
      const previous = Number(project.document.shots[index - 1].start) + 1 / 24;
      const next = Number(project.document.shots[index + 1]?.start ?? project.document.duration_seconds) - 1 / 24;
      draft.start = Math.max(previous, Math.min(next, Math.round(value * 24) / 24));
    }, "number", PLACEHOLDERS.cutTime);
    start.step = String(1 / 24);
    start.addEventListener("change", () => {
      const shotEnd = Number(project.document.shots[index + 1]?.start ?? project.document.duration_seconds);
      ensureShotTimeline(draft, Math.max(1 / 24, shotEnd - Number(draft.start || 0)));
      renderShotEditorDialog();
    });
    essentials.body.append(field("Cut time", start));
  }
  const firstFrameLocked = index === 0 && (project.document.references || []).some(reference =>
    (reference.roles || []).includes("first_frame"));
  const setupFields = [
    ["Subjects and positions", "subjects"],
    ["Environment", "environment"],
    ["Composition and framing", "composition"],
    ["Lighting", "lighting"],
  ];
  if (firstFrameLocked) {
    essentials.body.append(el(
      "div",
      "psvstudio-help",
      "Picture 1 owns the opening subjects, environment, composition, lighting, and style. These fields are shown for compatibility but are not compiled.",
    ));
    for (const [label, name] of setupFields) {
      const input = textArea(draft[name] || "", () => {}, 3, PLACEHOLDERS[name] || "");
      input.disabled = true;
      essentials.body.append(field(label, input));
    }
  } else {
    for (const [label, name] of setupFields) appendDraftShotTextField(essentials.body, label, draft, name);
  }
  if (index > 0) essentials.body.append(field("Transition", textInput(draft.transition, value => { draft.transition = value; }, "text", PLACEHOLDERS.transition)));
  setup.append(essentials.details);

  const cameraSection = inspectorDetails("Camera", true);
  const camera = draft.camera ||= { type: "Static Shot", amplitude: "default", speed: "default", target: "" };
  cameraSection.body.append(
    field("Movement", selectInput(state.config.camera_types, camera.type, value => { camera.type = value; })),
    field("Strength", selectInput(["default", "small", "large"], camera.amplitude, value => { camera.amplitude = value; })),
    field("Speed", selectInput(["default", "slow", "fast"], camera.speed, value => { camera.speed = value; })),
    field("Target or reveal", textInput(camera.target, value => { camera.target = value; }, "text", PLACEHOLDERS.cameraTarget)),
  );
  setup.append(cameraSection.details);

  const additional = inspectorDetails("Sound, text, and notes");
  additional.body.append(
    field("Visible text", textArea((draft.visible_text || []).join("\n"), value => {
      draft.visible_text = value.split(/\r?\n/).map(item => item.trim()).filter(Boolean);
    }, 3, PLACEHOLDERS.visibleText), "One exact item per line."),
    field("Synchronized sounds", textArea((draft.sounds || []).join("\n"), value => {
      syncShotSoundCues(draft, value.split(/\r?\n/).map(item => item.trim()).filter(Boolean));
    }, 3, PLACEHOLDERS.sounds), "One sound event per line."),
    field("Notes", textArea(draft.notes || "", value => { draft.notes = value; }, 3, "Production notes for this shot.")),
  );
  setup.append(additional.details);

  const sequence = el("section", "psvstudio-shot-sequence");
  const sequenceHeader = el("div", "psvstudio-shot-sequence-header");
  const sequenceTitle = el("div", "");
  sequenceTitle.append(el("h3", "", "Chronological steps"), el("small", "", "Drag or use the arrows. MiniMax receives this exact top-to-bottom order."));
  const addControls = el("div", "psvstudio-inline");
  addControls.append(
    button("Expand all", () => {
      state.shotEditorExpandedStepIds = new Set(draft.steps.map(step => step.id));
      renderShotEditorDialog();
    }),
    button("Collapse all", () => {
      state.shotEditorExpandedStepIds = new Set();
      renderShotEditorDialog();
    }),
    button("Add action", () => {
      const step = newActionStep();
      draft.steps.push(step);
      state.shotEditorExpandedStepIds.add(step.id);
      state.shotEditorRevealStepId = step.id;
      renderShotEditorDialog();
    }),
    button("Add dialogue", () => {
      const step = newDialogueStep(draft);
      draft.steps.push(step);
      state.shotEditorExpandedStepIds.add(step.id);
      state.shotEditorRevealStepId = step.id;
      renderShotEditorDialog();
    }, "psvstudio-button psvstudio-button-primary"),
  );
  sequenceHeader.append(sequenceTitle, addControls);
  const stepList = el("div", "psvstudio-step-list");
  stepList.tabIndex = 0;
  stepList.ariaLabel = "Chronological shot steps";
  renderShotSteps(stepList, draft);
  const detailTimeline = renderShotDetailTimeline(draft, Math.max(1 / 24, end - Number(draft.start || 0)));
  sequence.append(detailTimeline, sequenceHeader, stepList);
  workspace.append(setup, sequence);

  const footer = el("footer", "psvstudio-shot-editor-footer");
  const left = el("div", "psvstudio-inline");
  const remove = button("Remove shot", removeShotFromEditor, "psvstudio-button psvstudio-button-danger");
  remove.disabled = project.document.shots.length < 2;
  const shotDirector = button(
    "✦ Shot director",
    () => saveShotEditor({ askDirector: true }),
    "psvstudio-button psvstudio-button-primary psvstudio-shot-director-button",
  );
  shotDirector.title = "Save this shot and consult the Shot director";
  left.append(remove, shotDirector);
  const right = el("div", "psvstudio-inline");
  right.append(
    button("Cancel", () => closeShotEditor({ force: true })),
    button("Save shot", () => saveShotEditor(), "psvstudio-button psvstudio-button-primary"),
  );
  footer.append(left, right);
  dialog.append(header, workspace, footer);
  const revealStepId = state.shotEditorRevealStepId;
  state.shotEditorRevealStepId = "";
  state.panel.ownerDocument.defaultView?.requestAnimationFrame(() => {
    if (revealStepId) {
      const target = [...stepList.querySelectorAll(".psvstudio-step-card")]
        .find(card => card.dataset.stepId === revealStepId);
      target?.scrollIntoView({ block: "nearest" });
    } else {
      stepList.scrollTop = previousStepScroll;
    }
  });
}

function openShotEditor(shotId = state.selectedShotId) {
  const project = activeProject();
  const shot = project?.document?.shots?.find(item => item.id === shotId);
  if (!project || !shot) return;
  closeShotEditor({ force: true });
  ensureShotSteps(shot);
  state.selectedShotId = shot.id;
  state.shotEditorShotId = shot.id;
  state.shotEditorDraft = clone(shot);
  state.shotEditorOriginal = clone(shot);
  ensureShotTimeline(state.shotEditorDraft, shotLocalDuration(project, shot.id));
  state.shotEditorExpandedStepIds = new Set();
  state.shotEditorRevealStepId = "";
  state.shotTimelineSelectionId = "";
  const dialog = el("dialog", "psvstudio-shot-editor-dialog");
  dialog.setAttribute("aria-label", `Edit Shot ${project.document.shots.indexOf(shot) + 1}`);
  dialog.addEventListener("cancel", event => {
    event.preventDefault();
    closeShotEditor();
  });
  state.shotEditorDialog = dialog;
  state.panel.append(dialog);
  renderShotEditorDialog();
  dialog.showModal();
  renderTimeline();
  renderInspector();
}

function setVideoAdditionalInputSelection(project, workflow, descriptor, value) {
  project.additional_input_selections = normalizePromptStudioInputSelections(project.additional_input_selections);
  const key = promptStudioInputSelectionKey(workflow?.id, descriptor?.id);
  const normalized = promptStudioInputValue(descriptor, value);
  if (normalized === descriptor.defaultValue) delete project.additional_input_selections[key];
  else {
    project.additional_input_selections[key] = {
      value: normalized,
      schemaFingerprint: descriptor.schemaFingerprint,
    };
  }
  markProjectChanged({ project });
}

function videoAdditionalInputControl(project, workflow, descriptor) {
  const row = el("div", "psvstudio-additional-input-row");
  const heading = el("div", "psvstudio-additional-input-heading");
  const copy = el("span");
  copy.append(
    el("strong", "", descriptor.label),
    el("small", "", `${descriptor.targetNodeLabel} · ${descriptor.targetLabel}`),
  );
  const reset = button("Reset", () => {
    delete project.additional_input_selections[promptStudioInputSelectionKey(workflow.id, descriptor.id)];
    markProjectChanged({ project });
    renderInspector();
  }, "psvstudio-button");
  heading.append(copy, reset);
  row.append(heading);
  const selected = selectedPromptStudioInputValue(
    workflow.id,
    descriptor,
    project.additional_input_selections,
  );
  const schema = descriptor.schema;
  let control;
  if (schema.type === "COMBO") {
    control = document.createElement("select");
    schema.options.forEach((optionValue, index) => {
      const option = document.createElement("option");
      option.value = String(index);
      option.textContent = String(optionValue);
      option.selected = optionValue === selected;
      control.append(option);
    });
    control.addEventListener("change", () => {
      setVideoAdditionalInputSelection(project, workflow, descriptor, schema.options[Number(control.value)]);
    });
  } else if (schema.type === "BOOLEAN") {
    const label = el("label", "psvstudio-additional-input-boolean");
    control = document.createElement("input");
    control.type = "checkbox";
    control.checked = Boolean(selected);
    const stateLabel = el("span", "", control.checked ? schema.labelOn : schema.labelOff);
    control.addEventListener("change", () => {
      stateLabel.textContent = control.checked ? schema.labelOn : schema.labelOff;
      setVideoAdditionalInputSelection(project, workflow, descriptor, control.checked);
    });
    control.setAttribute("aria-label", descriptor.label);
    label.append(control, stateLabel);
    row.append(label);
    return row;
  } else if (schema.type === "STRING" && schema.multiline) {
    control = document.createElement("textarea");
    control.rows = 3;
    control.value = String(selected ?? "");
    control.addEventListener("change", () => setVideoAdditionalInputSelection(project, workflow, descriptor, control.value));
  } else {
    control = document.createElement("input");
    if (["INT", "FLOAT"].includes(schema.type)) {
      control.type = "number";
      if (schema.min != null) control.min = String(schema.min);
      if (schema.max != null) control.max = String(schema.max);
      control.step = String(schema.step ?? (schema.type === "INT" ? 1 : "any"));
      control.value = String(selected);
      control.addEventListener("change", () => setVideoAdditionalInputSelection(project, workflow, descriptor, control.valueAsNumber));
    } else {
      control.type = "text";
      control.value = String(selected ?? "");
      control.addEventListener("change", () => setVideoAdditionalInputSelection(project, workflow, descriptor, control.value));
    }
  }
  control.setAttribute("aria-label", descriptor.label);
  row.append(control);
  return row;
}

function renderVideoAdditionalInputs(project) {
  const workflow = selectedWorkflow(project);
  const descriptors = normalizePromptStudioInputDescriptors(workflow?.additionalInputs);
  if (!descriptors.length) return null;
  project.additional_input_selections = normalizePromptStudioInputSelections(project.additional_input_selections);
  const section = inspectorDetails("Additional Inputs", true);
  section.body.classList.add("psvstudio-additional-inputs");
  section.body.append(...descriptors.map(descriptor => (
    videoAdditionalInputControl(project, workflow, descriptor)
  )));
  return section.details;
}

function renderInspector() {
  const content = state.panel?.querySelector("#psvstudio-inspector-content");
  const project = activeProject();
  if (!content) return;
  content.replaceChildren();
  const heading = state.panel.querySelector("#psvstudio-inspector-title");
  if (heading) heading.textContent = "Shots";
  if (!project) {
    content.append(el("div", "psvstudio-empty", "Create a project to begin building shots."));
    return;
  }
  const additionalInputs = renderVideoAdditionalInputs(project);
  if (additionalInputs) content.append(additionalInputs);
  const intro = el("div", "psvstudio-shots-intro");
  intro.append(el("small", "", "Select a shot to locate it on the timeline. Open it to edit setup and chronological steps."));
  content.append(intro);
  const list = el("div", "psvstudio-shot-navigator");
  project.document.shots.forEach((shot, index) => {
    const steps = ensureShotSteps(shot);
    const dialogueCount = steps.filter(step => step.type === "dialogue").length;
    const end = Number(project.document.shots[index + 1]?.start ?? project.document.duration_seconds);
    const card = el("article", `psvstudio-shot-nav-card${shot.id === state.selectedShotId ? " is-selected" : ""}`);
    const select = button("", () => {
      state.selectedShotId = shot.id;
      renderTimeline();
      renderInspector();
    }, "psvstudio-shot-nav-select");
    const head = el("div", "psvstudio-shot-nav-head");
    head.append(el("strong", "", `Shot ${index + 1}`), el("span", "", `${(end - Number(shot.start || 0)).toFixed(2)}s`));
    select.append(
      head,
      el("p", "", shotStepSummary(shot)),
      el("small", "", `${steps.length} step${steps.length === 1 ? "" : "s"} · ${dialogueCount} dialogue · ${shot.camera?.type || "No camera movement"}`),
    );
    select.addEventListener("dblclick", () => openShotEditor(shot.id));
    const actions = el("div", "psvstudio-shot-nav-actions");
    actions.append(button("Edit shot", () => openShotEditor(shot.id), "psvstudio-button psvstudio-button-primary"));
    card.append(select, actions);
    list.append(card);
  });
  content.append(list);
  content.append(button("Add shot", () => {
    const shot = addShot();
    if (shot) openShotEditor(shot.id);
  }, "psvstudio-button"));
}

async function generateRestoredComparison(project) {
  const restored = pendingGenerationRestore(project);
  if (!restored) return;
  const generate = state.panel?.querySelector("#psvstudio-generate");
  if (generate) generate.disabled = true;
  const previousIds = new Set(project.generations.map(item => item.id));
  try {
    await replayGeneration(clone(restored.generation));
    if (project.pending_generation_restore === restored && project.generations.some(item => !previousIds.has(item.id))) {
      delete project.pending_generation_restore;
      markProjectChanged({project,render:true});
      await persistProjects({immediate:true});
    }
  } finally { renderHeader(); }
}

function savedGenerationRestoreState(project) {
  return { document: clone(project.document), workflow_id: project.workflow_id || "",
    additional_input_selections: clone(project.additional_input_selections || {}) };
}

function pendingGenerationRestore(project) {
  const pending = project?.pending_generation_restore;
  if (!pending?.generation?.workflow_snapshot?.output || !pending.fingerprint) return null;
  const canonical = value => Array.isArray(value) ? value.map(canonical)
    : value && typeof value === "object" ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;
  return JSON.stringify(canonical(pending.fingerprint)) === JSON.stringify(canonical(savedGenerationRestoreState(project))) ? pending : null;
}

async function restoreVideoComparisonInputs(project, record) {
  if (activeProject()?.id !== project.id) throw new Error("Return to the original project before restoring its inputs.");
  if (projectPendingGenerationCount(project)) throw new Error("Wait for this project's active generation before restoring inputs.");
  const saved = record.saved;
  if (!saved.document || !saved.workflow_snapshot?.output) throw new Error("This result has no complete saved inputs.");
  if (!closeShotEditor()) throw new Error("The shot editor still has unsaved changes.");
  project.document = clone(saved.document);
  project.brief = project.document.main_description || "";
  project.workflow_id = saved.workflow_id || "";
  project.additional_input_selections = {};
  state.selectedShotId = project.document.shots?.[0]?.id || null;
  project.pending_generation_restore = {version:1,generation:clone(saved),fingerprint:savedGenerationRestoreState(project)};
  markProjectChanged({project,render:true});
  await persistProjects({immediate:true});
  setStatus("Saved inputs restored. Generate will review and use the saved workflow and seed.", "ready");
}

function openVideoResultComparison(generation, trigger) {
  const project = activeProject();
  if (!project) return;
  const comparison = createResultComparison({
    container: state.panel,
    getItems: () => project.generations.filter(item => item.workflow_snapshot).map(videoComparisonRecord),
    mediaUrl: outputUrl,
    onRestore: record => restoreVideoComparisonInputs(project, record),
  });
  comparison.open(generation.id, () => trigger?.isConnected ? trigger
    : [...state.panel.querySelectorAll("[data-generation-id]")].find(card => card.dataset.generationId === generation.id)
      ?.querySelector("[data-result-compare]"));
}

function renderGenerations() {
  const list = state.panel?.querySelector("#psvstudio-generation-list");
  const project = activeProject();
  if (!list) return;
  if (!project?.generations?.length) {
    list.dataset.projectId = project?.id || "";
    list.replaceChildren(el("div", "psvstudio-empty", "Generated videos and immutable replay snapshots will appear here."));
    return;
  }

  if (list.dataset.projectId !== project.id) {
    list.dataset.projectId = project.id;
    list.replaceChildren();
  }
  for (const child of [...list.children]) {
    if (!child.dataset.generationId) child.remove();
  }

  const existingCards = new Map(
    [...list.children]
      .filter(child => child.dataset.generationId)
      .map(child => [child.dataset.generationId, child]),
  );
  let cursor = list.firstElementChild;
  for (const generation of project.generations) {
    const generationId = String(generation.id || generation.prompt_id || "");
    const loopKey = `${project.id}:${generationId}`;
    const card = existingCards.get(generationId) || el("article", "psvstudio-generation-card");
    card.dataset.generationId = generationId;
    const media = card.querySelector(":scope > .psvstudio-generation-media") || el("div", "psvstudio-generation-media");
    const output = generation.outputs?.[0];
    let video = null;
    if (output) {
      const url = outputUrl(output);
      const currentVideo = media.querySelector(":scope > video");
      if (!currentVideo || currentVideo.getAttribute("src") !== url) {
        video = document.createElement("video");
        video.controls = true;
        video.preload = "metadata";
        video.src = url;
        enforceSingleVideoPlayback(video);
        media.replaceChildren(video);
      } else video = currentVideo;
      video.loop = state.loopingGenerations.has(loopKey);
    } else {
      const pendingLabels = {
        validating: "Validating production…",
        compiling: "Compiling prompt…",
        queueing: "Queueing workflow…",
        queued: "Waiting in queue…",
        generating: "Generating video…",
        cancelled: "Cancelled",
        error: "Generation failed",
      };
      const message = pendingLabels[generation.status] || "Preparing video…";
      if (media.childElementCount !== 1 || media.firstElementChild?.tagName !== "SPAN" || media.firstElementChild.textContent !== message) {
        media.replaceChildren(el("span", "", message));
      }
    }
    const body = el("div", "psvstudio-generation-body");
    const head = el("div", "psvstudio-generation-head");
    const isExtension = generation.kind === "extension" || Boolean(generation.parent_generation_id);
    head.append(
      el("strong", "", `${generation.workflow_name || "Video workflow"}${isExtension ? ` · Extension ${Number(generation.depth || 1)}` : ""}`),
      el("span", "psvstudio-status-chip", generation.status),
    );
    head.lastElementChild.dataset.status = generation.status;
    body.append(head, el("small", "psvstudio-help", `${String(generation.resolved_mode || "").toUpperCase()} · ${Number(generation.effective_duration || 0).toFixed(2)}s · ${generation.frame_count || 0} frames`));
    if (isExtension) {
      body.append(el("small", "psvstudio-help", `Continuation ${Number(generation.depth || 1)} · ${Number(generation.total_effective_duration || generation.effective_duration || 0).toFixed(2)}s cumulative duration`));
    }
    if (["validating", "compiling", "queueing", "queued", "generating"].includes(generation.status)) {
      const progress = state.generationProgress.get(String(generation.prompt_id)) || {};
      const percent = Number.isFinite(progress.value) && Number.isFinite(progress.max) && progress.max > 0
        ? Math.max(2, Math.min(100, progress.value / progress.max * 100))
        : 12;
      const bar = el("div", "psvstudio-progress");
      const fill = el("span");
      fill.style.setProperty("--progress", `${percent}%`);
      bar.append(fill);
      body.append(bar);
      if (progress.phase === "finalizing") body.append(el("small", "psvstudio-help", "Finalizing…"));
      if (progress.phase === "assembling") body.append(el(
        "small", "psvstudio-help",
        documentHasExactAudio(generation.document)
          ? "Video rendered · mixing exact timeline audio…"
          : "Extension rendered · assembling the cumulative version…",
      ));
    }
    if (generation.error) body.append(el("div", "psvstudio-help", generation.error));
    const actions = el("div", "psvstudio-inline");
    if (["validating", "compiling", "queueing", "queued", "generating"].includes(generation.status)) {
      actions.append(button("Cancel", () => cancelVideoGeneration(project.id, generation.id), "psvstudio-button psvstudio-button-danger"));
    }
    if (video) {
      const loop = button("", () => {
        video.loop = !video.loop;
        if (video.loop) state.loopingGenerations.add(loopKey);
        else state.loopingGenerations.delete(loopKey);
        loop.textContent = `Loop: ${video.loop ? "On" : "Off"}`;
        loop.setAttribute("aria-pressed", String(video.loop));
      });
      loop.textContent = `Loop: ${video.loop ? "On" : "Off"}`;
      loop.setAttribute("aria-pressed", String(video.loop));
      loop.title = "Toggle continuous playback for this video";
      actions.append(loop);
    }
    if (generation.status === "complete" && output && generation.document && generation.workflow_snapshot) {
      actions.append(button("Continue video", () => showContinueVideo(generation), "psvstudio-button psvstudio-button-primary"));
    }
    if (isExtension && generation.segment_outputs?.[0]) {
      actions.append(button("View extension", () => showGenerationOutput(generation.segment_outputs[0], `Extension ${Number(generation.depth || 1)} segment`)));
    }
    if (isExtension && ["complete", "error", "interrupted", "cancelled"].includes(generation.status)) {
      actions.append(button("Regenerate extension", () => showRegenerateExtension(generation), "psvstudio-button psvstudio-button-primary"));
    }
    actions.append(button("Replay exact", () => replayGeneration(generation)));
    if (generation.workflow_snapshot) {
      const compare = button("Compare saved inputs", () => openVideoResultComparison(generation, compare));
      compare.dataset.resultCompare = "true";
      actions.append(compare);
    }
    if (generation.compiled_prompt) actions.append(button("View prompt", () => showCompiledPrompt(generation.compiled_prompt)));
    body.append(actions);
    const currentBody = card.querySelector(":scope > .psvstudio-generation-body");
    if (currentBody) currentBody.replaceWith(body);
    else card.append(media, body);

    if (card !== cursor) list.insertBefore(card, cursor);
    else cursor = cursor.nextElementSibling;
    existingCards.delete(generationId);
  }
  for (const card of existingCards.values()) card.remove();
}

function showCompiledPrompt(prompt) {
  const dialog = el("dialog", "psvstudio-prompt-dialog");
  dialog.setAttribute("aria-label", "Compiled MiniMax prompt");
  const title = el("h2", "", "Compiled MiniMax prompt");
  const copy = document.createElement("textarea");
  copy.readOnly = true;
  copy.value = prompt;
  copy.rows = 18;
  const actions = el("div", "psvstudio-inline");
  actions.append(
    button("Copy", async () => {
      await navigator.clipboard.writeText(prompt);
      setStatus("Compiled prompt copied.", "ready");
    }),
    button("Close", () => dialog.close(), "psvstudio-button psvstudio-button-primary"),
  );
  dialog.append(title, copy, actions);
  state.panel.ownerDocument.body.append(dialog);
  dialog.addEventListener("close", () => dialog.remove(), { once: true });
  dialog.showModal();
}

function showEditableProjectPrompt(project, { prompt = "", settingsPrompt = "", settingsError = "" } = {}) {
  const dialog = el("dialog", "psvstudio-prompt-dialog");
  dialog.setAttribute("aria-label", "Editable MiniMax prompt");
  const title = el("h2", "", "Editable MiniMax prompt");
  const warning = el("div", "psvstudio-prompt-warning");
  warning.setAttribute("role", "status");
  const editor = document.createElement("textarea");
  editor.value = prompt;
  editor.rows = 18;
  editor.placeholder = "Write a complete MiniMax prompt, or rebuild one from the Shot Composer settings.";
  const rebuild = button("Rebuild from settings", () => {
    if (!settingsPrompt) return;
    editor.value = settingsPrompt;
    project.document.prompt_override = "";
    markProjectChanged();
    renderHeader();
    refreshWarning();
    setStatus("The manual prompt override was removed and rebuilt from Shot Composer settings.", "ready");
  });
  rebuild.disabled = !settingsPrompt;
  rebuild.title = settingsPrompt
    ? "Discard the manual override and compile the current structured settings"
    : (settingsError || "The structured settings cannot currently be compiled");
  const save = button("Save prompt", () => {
    const value = editor.value.trim();
    if (!value) {
      state.panel.ownerDocument.defaultView?.alert("The generation prompt cannot be empty.");
      return;
    }
    const diverged = !settingsPrompt || value !== settingsPrompt.trim();
    project.document.prompt_override = diverged ? value : "";
    markProjectChanged();
    renderHeader();
    setStatus(
      diverged
        ? "Manual prompt saved. It is now used for generation instead of the Shot Composer settings."
        : "Prompt matches the Shot Composer settings; the manual override was removed.",
      diverged ? "warning" : "ready",
    );
    dialog.close();
  }, "psvstudio-button psvstudio-button-primary");
  const refreshWarning = () => {
    const diverged = !settingsPrompt || editor.value.trim() !== settingsPrompt.trim();
    warning.hidden = !diverged;
    warning.textContent = settingsError
      ? `The structured settings cannot currently rebuild a valid prompt: ${settingsError}`
      : "This prompt has manual edits and does not automatically align with the Shot Composer settings. Generation will use this text until you rebuild it from settings.";
  };
  editor.addEventListener("input", refreshWarning);
  const actions = el("div", "psvstudio-inline psvstudio-prompt-actions");
  actions.append(
    rebuild,
    button("Copy", async () => {
      await navigator.clipboard.writeText(editor.value);
      setStatus("Prompt copied.", "ready");
    }),
    button("Close", () => dialog.close()),
    save,
  );
  dialog.append(title, warning, editor, actions);
  state.panel.ownerDocument.body.append(dialog);
  dialog.addEventListener("close", () => dialog.remove(), { once: true });
  refreshWarning();
  dialog.showModal();
  editor.focus();
}

async function compilePreview() {
  if (!state.apiConnected) {
    setStatus("ComfyUI disconnected — Video Studio is frozen.", "error");
    return;
  }
  const project = activeProject();
  if (!project) return;
  try {
    if (isStructuredExtensionProject(project)) {
      const source = project.extension_source;
      const response = await api.fetchApi(CONTINUATION_PREPARE_ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          document: source.source_document,
          extension_document: project.document,
          source: source.source,
          brief: project.brief,
          duration_seconds: Number(project.document.duration_seconds || 5),
          context_frames: CONTINUATION_CONTEXT_FRAMES,
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || "The structured extension could not be compiled.");
      showCompiledPrompt(data.compiled_prompt);
      return;
    }
    const response = await api.fetchApi("/promptstudio-video/document/compile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ document: project.document }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      showEditableProjectPrompt(project, {
        prompt: project.document.prompt_override || "",
        settingsError: data.error || "The Shot Composer settings could not be compiled.",
      });
      return;
    }
    showEditableProjectPrompt(project, {
      prompt: data.compiled_prompt,
      settingsPrompt: data.settings_prompt,
      settingsError: data.settings_prompt_error,
    });
  } catch (error) {
    setStatus(error.message || String(error), "error");
  }
}

function renderHeader() {
  const project = activeProject();
  const title = state.panel?.querySelector("#psvstudio-project-title");
  const generate = state.panel?.querySelector("#psvstudio-generate");
  const duplicate = state.panel?.querySelector("#psvstudio-duplicate");
  const reset = state.panel?.querySelector("#psvstudio-reset");
  const preview = state.panel?.querySelector("#psvstudio-compile-preview");
  const videoDirector = state.panel?.querySelector("#psvstudio-video-director");
  const addMedia = state.panel?.querySelector("#psvstudio-add-media");
  const viewMedia = state.panel?.querySelector("#psvstudio-view-all-media");
  const structuredExtension = isStructuredExtensionProject(project);
  if (title) {
    title.disabled = !project;
    title.value = project?.name || "Prompt Studio Video";
  }
  if (generate) {
    generate.disabled = !project || (!structuredExtension && !state.workflows.length && !pendingGenerationRestore(project));
    generate.textContent = structuredExtension ? "Generate extension" : "Generate";
  }
  if (duplicate) duplicate.disabled = !project;
  if (reset) reset.disabled = !project || structuredExtension || Boolean(projectPendingGenerationCount(project)) || projectHasPendingDirectorJob(project.id) || state.directorBusy;
  if (videoDirector) videoDirector.hidden = structuredExtension;
  if (addMedia) {
    addMedia.disabled = structuredExtension;
    addMedia.title = structuredExtension ? "New media references are not yet supported inside native structured extensions." : "Add project media";
  }
  if (viewMedia) viewMedia.disabled = structuredExtension;
  if (preview) preview.disabled = !project;
  if (preview) {
    const overridden = Boolean(project?.document?.prompt_override);
    preview.textContent = overridden ? "Prompt*" : "Prompt";
    preview.title = structuredExtension
      ? "Preview the context-prefixed extension prompt"
      : overridden
      ? "A manual prompt override is active and may not match the Shot Composer settings"
      : "View or edit the compiled MiniMax prompt";
    preview.dataset.override = overridden ? "true" : "false";
  }
  renderWorkflowSelect();
  renderRunSummary();
}

function renderRunSummary() {
  const summary = state.panel?.querySelector("#psvstudio-run-summary");
  if (!summary) return;
  const project = activeProject();
  if (!project) { summary.textContent = "Create a project, author its shots, then choose a workflow to generate."; return; }
  const restored = pendingGenerationRestore(project);
  if (restored) {
    summary.textContent = `Next generation uses saved inputs from ${restored.generation.workflow_name || restored.generation.id}: saved workflow, prompts and seeds. Generate reviews replay dependencies first. Editing shots, workflow or custom inputs returns to current settings.`;
    return;
  }
  const extension = isStructuredExtensionProject(project);
  const workflow = selectedWorkflow(project);
  const prompt = project.document.prompt_override ? "Manual compiled prompt override" : "Compiled from authored shots";
  const seed = state.panel.querySelector("#psvstudio-new-seed")?.checked !== false ? "New seed" : "Workflow seed";
  const pending = projectPendingGenerationCount(project);
  const inputs = Object.keys(project.additional_input_selections || {}).length;
  summary.textContent = `${pending ? `${pending} generation${pending === 1 ? "" : "s"} pending` : "Ready"} · Generate ${extension ? "queues this extension using the source workflow" : "queues this video"}. ${extension ? "Source render snapshot" : workflow?.name || "Choose a workflow"} · ${seed} · ${prompt}${inputs ? ` · ${inputs} custom workflow inputs` : ""}. The synopsis guides planning; shot edits apply only when saved.`;
}

function renderAll() {
  if (!state.panel) return;
  state.panel.dataset.drawer = state.drawer;
  renderProjectList();
  renderHeader();
  renderPreview();
  renderTimeline();
  renderMediaLane();
  renderInspector();
  renderGenerations();
}

function buildPanel() {
  if (state.panel) return state.panel;
  const llmSettings = directorSettings();
  const initialProvider = normalizeLlmProvider(llmSettings.llm_provider);
  const panel = el("section", "psvstudio-app");
  panel.hidden = true;
  panel.innerHTML = `
    <div id="psvstudio-api-connection" class="psvstudio-api-connection" role="alert" hidden>
      <span aria-hidden="true"></span>
      <strong>ComfyUI disconnected</strong>
      <small>Video Studio is frozen until the API connection returns.</small>
    </div>
    <aside id="psvstudio-projects-drawer" class="psvstudio-sidebar">
      <div class="psvstudio-sidebar-header">
        <h2>Projects</h2>
        <div class="psvstudio-inline"><button id="psvstudio-new-project" class="psvstudio-button psvstudio-button-primary" type="button">New</button><button id="psvstudio-close-projects" class="psvstudio-button psvstudio-icon-button" type="button" title="Close projects" aria-label="Close projects">×</button></div>
      </div>
      <div id="psvstudio-project-list" class="psvstudio-project-list"></div>
    </aside>
    <button class="psvstudio-drawer-scrim" type="button" aria-label="Close open drawer"></button>
    <main class="psvstudio-main">
      <header class="psvstudio-topbar">
        <button id="psvstudio-mobile-projects" class="psvstudio-button psvstudio-icon-button" type="button" title="Projects" aria-label="Projects" aria-controls="psvstudio-projects-drawer" aria-expanded="false">☰</button>
        <div class="psvstudio-studio-switch psvstudio-mobile-studio-switch" role="group" aria-label="Studio mode">
          <button type="button" data-promptstudio-studio-mode="image" aria-pressed="false">Image</button>
          <button type="button" data-promptstudio-studio-mode="video" data-available="true" aria-pressed="true">Video</button>
        </div>
        <input id="psvstudio-project-title" class="psvstudio-title-field" aria-label="Project name" placeholder="${PLACEHOLDERS.projectTitle}" />
        <span id="psvstudio-save-state" class="psvstudio-save-state">Saved</span>
        <span class="psvstudio-topbar-spacer"></span>
        <select id="psvstudio-workflow" class="psvstudio-workflow-select" aria-label="Video workflow"></select>
        <button id="psvstudio-refresh-workflows" class="psvstudio-button psvstudio-icon-button" type="button" title="Refresh [PSV] workflows" aria-label="Refresh [PSV] workflows">↻</button>
        <details id="psvstudio-kobold-control" class="psvstudio-kobold-control" data-state="checking">
          <summary title="System status"><span class="psvstudio-kobold-dot" aria-hidden="true"></span><span id="psvstudio-kobold-status-label">Status: checking</span></summary>
          <div class="psvstudio-kobold-popover">
            <strong class="psvstudio-system-status-heading">System status</strong>
            <section class="psvstudio-system-status-section">
              <strong id="psvstudio-llm-status-heading">${llmProviderDisplayName(initialProvider)}</strong>
              <span id="psvstudio-kobold-status-detail" role="status" aria-live="polite">Checking local status…</span>
              <dl class="psvstudio-kobold-metadata">
                <div><dt>Model</dt><dd id="psvstudio-kobold-model">Checking…</dd></div>
                <div><dt>Vision</dt><dd id="psvstudio-kobold-vision" data-state="unknown">Checking…</dd></div>
              </dl>
              <button id="psvstudio-kobold-stop" class="psvstudio-button psvstudio-button-danger" type="button" disabled ${initialProvider === "ollama" ? "hidden" : ""}>Force stop processing</button>
              <small id="psvstudio-kobold-stop-help" ${initialProvider === "ollama" ? "hidden" : ""}>${initialProvider === "llamacpp" ? "Stops Video Studio text streams only. Manage the Llama.cpp server in Prompt Studio settings." : "Stops LLM processing only. KoboldCpp stays loaded."}</small>
            </section>
            <section class="psvstudio-system-status-section">
              <div class="psvstudio-system-status-row"><strong>ComfyUI</strong><span id="psvstudio-comfy-status-detail" data-state="busy" role="status" aria-live="polite">Checking…</span></div>
              <button id="psvstudio-comfy-restart" class="psvstudio-button" type="button">Restart ComfyUI</button>
            </section>
            <section class="psvstudio-system-status-section">
              <strong>Recent jobs</strong>
              <div id="psvstudio-job-activity">Open activity to check job stages.</div>
              <button id="psvstudio-job-refresh" class="psvstudio-button" type="button">Refresh activity</button>
              <button id="psvstudio-job-diagnostics" class="psvstudio-button" type="button">Export diagnostics</button>
              <small>Local metadata only; prompts, results and credentials are excluded.</small>
              <small>Requires ComfyUI Manager. Running work will be interrupted.</small>
            </section>
          </div>
        </details>
        <label class="psvstudio-inline psvstudio-help"><input id="psvstudio-new-seed" type="checkbox" checked /> New seed</label>
        <button id="psvstudio-compile-preview" class="psvstudio-button" type="button">Prompt</button>
        <button id="psvstudio-duplicate" class="psvstudio-button" type="button">Duplicate</button>
        <button id="psvstudio-reset" class="psvstudio-button psvstudio-button-danger" type="button" title="Reset this project while retaining its reference media">Reset</button>
        <button id="psvstudio-mobile-inspector" class="psvstudio-button psvstudio-icon-button" type="button" title="Shots" aria-label="Shots" aria-controls="psvstudio-shots-drawer" aria-expanded="false">☷</button>
      </header>
      <div class="psvstudio-workspace">
        <div class="psvstudio-editor-scroll">
          <section class="psvstudio-stage">
            <div id="psvstudio-preview" class="psvstudio-preview"></div>
          </section>
          <section class="psvstudio-generations">
            <div class="psvstudio-section-heading"><h2>Generations</h2><small class="psvstudio-help">Each render keeps an exact workflow snapshot.</small></div>
            <div id="psvstudio-generation-list" class="psvstudio-generation-list"></div>
          </section>
        </div>
        <section class="psvstudio-timeline-card">
          <div class="psvstudio-section-heading psvstudio-timeline-heading">
            <div><h2>Timeline</h2><small class="psvstudio-help">Drag shots to reorder · resize boundaries · 24 fps snap</small></div>
            <div class="psvstudio-timeline-tools">
              <label class="psvstudio-zoom-control" title="Timeline zoom"><span>−</span><input id="psvstudio-timeline-zoom" type="range" min="36" max="160" step="4" value="80" aria-label="Timeline zoom" /><span>+</span></label>
              <button id="psvstudio-fit-timeline" class="psvstudio-button" type="button">Fit</button>
              <button id="psvstudio-add-shot" class="psvstudio-button" type="button">Add shot</button>
              <button id="psvstudio-video-director" class="psvstudio-button psvstudio-button-primary psvstudio-timeline-director-button" type="button" title="Consult the Video director about the entire video">✦ Video director</button>
            </div>
          </div>
          <div class="psvstudio-timeline-content">
            <div id="psvstudio-shot-list" class="psvstudio-timeline-viewport"></div>
            <section class="psvstudio-media-panel">
              <div class="psvstudio-media-panel-heading">
                <div><strong>Media</strong><small>Click to preview · drag to reorder</small></div>
                <div class="psvstudio-media-panel-actions">
                  <button id="psvstudio-add-media" class="psvstudio-button" type="button">Add</button>
                  <button id="psvstudio-view-all-media" class="psvstudio-button" type="button">View all</button>
                </div>
              </div>
              <div id="psvstudio-media-lane" class="psvstudio-media-lane" aria-label="Project media"></div>
            </section>
          </div>
          <input id="psvstudio-media-input" class="psvstudio-sr-only" type="file" aria-label="Add image, video, or audio references" accept="image/*,video/*,audio/*" multiple />
        </section>
      </div>
      <footer class="psvstudio-action-footer">
        <div class="psvstudio-action-copy">
        <div id="psvstudio-status" class="psvstudio-status" role="status" aria-live="polite">Loading Video Studio…</div>
        <div id="psvstudio-run-summary" class="studio-run-summary" aria-label="What will run"></div>
        </div>
        <button id="psvstudio-generate" class="psvstudio-button psvstudio-button-primary" type="button">Generate</button>
      </footer>
    </main>
    <aside id="psvstudio-shots-drawer" class="psvstudio-inspector">
      <div class="psvstudio-brand psvstudio-inspector-brand">
        <img class="psvstudio-brand-mark" src="${VIDEO_ICON_URL}" alt="" aria-hidden="true">
        <div><strong>Prompt Studio Video</strong><small>by tiko13</small></div>
        <div class="psvstudio-studio-switch" role="group" aria-label="Studio mode">
          <button type="button" data-promptstudio-studio-mode="image" aria-pressed="false">Image</button>
          <button type="button" data-promptstudio-studio-mode="video" data-available="true" aria-pressed="true">Video</button>
        </div>
      </div>
      <div class="psvstudio-inspector-header"><h2 id="psvstudio-inspector-title">Shots</h2><button id="psvstudio-close-inspector" class="psvstudio-button psvstudio-icon-button" type="button" aria-label="Close shots">×</button></div>
      <div id="psvstudio-inspector-content" class="psvstudio-inspector-content"></div>
    </aside>`;

  panel.querySelector("#psvstudio-new-project").addEventListener("click", newProject);
  panel.querySelectorAll('[data-promptstudio-studio-mode="image"], #psvstudio-close-inspector, #psvstudio-close-projects, .psvstudio-drawer-scrim, #psvstudio-comfy-restart').forEach(control => {
    control.dataset.psvstudioAllowDisconnected = "true";
  });
  panel.querySelector("#psvstudio-add-shot").addEventListener("click", addShot);
  panel.querySelector("#psvstudio-video-director").addEventListener("click", () => openDirector("project"));
  panel.querySelector("#psvstudio-add-media").addEventListener("click", () => panel.querySelector("#psvstudio-media-input").click());
  panel.querySelector("#psvstudio-view-all-media").addEventListener("click", () => showMediaLibrary());
  panel.querySelector("#psvstudio-media-input").addEventListener("change", async event => {
    await addMediaFiles(event.target.files);
    event.target.value = "";
  });
  panel.querySelector("#psvstudio-fit-timeline").addEventListener("click", fitTimeline);
  panel.querySelector("#psvstudio-timeline-zoom").addEventListener("input", event => {
    state.timelineZoom = Number(event.target.value) || 80;
    renderTimeline();
  });
  panel.querySelector("#psvstudio-generate").addEventListener("click", generateProject);
  panel.addEventListener("change", renderRunSummary);
  panel.querySelector("#psvstudio-duplicate").addEventListener("click", duplicateProject);
  panel.querySelector("#psvstudio-reset").addEventListener("click", resetProject);
  panel.querySelector("#psvstudio-compile-preview").addEventListener("click", compilePreview);
  panel.querySelector("#psvstudio-refresh-workflows").addEventListener("click", () => refreshWorkflows());
  panel.querySelector("#psvstudio-kobold-stop").addEventListener("click", stopLlmGeneration);
  panel.querySelector("#psvstudio-comfy-restart").addEventListener("click", restartComfyUIFromStatus);
  panel.querySelector("#psvstudio-job-refresh").addEventListener("click", async () => {
    const target = panel.querySelector("#psvstudio-job-activity");
    try {
      const snapshot = await fetchJobActivity((...args) => api.fetchApi(...args));
      target.replaceChildren();
      for (const job of snapshot.jobs.slice(0, 8)) {
        const item = el("p", "", jobActivityText(job, { projects: state.projects }));
        if (["failed", "cancelled", "interrupted"].includes(job.state)) item.append(el("small", "", ` ${jobRetryText(job)}`));
        target.append(item);
      }
      if (!snapshot.jobs.length) target.textContent = "No recorded jobs.";
      if (!snapshot.durable) target.append(el("p", "", "Job metadata could not be saved locally; restart recovery is unavailable."));
    } catch (error) { target.textContent = error.message; }
  });
  panel.querySelector("#psvstudio-job-diagnostics").addEventListener("click", async () => {
    try { await downloadJobDiagnostics({ fetchApi: (...args) => api.fetchApi(...args), document: panel.ownerDocument }); }
    catch (error) { panel.querySelector("#psvstudio-job-activity").textContent = error.message; }
  });
  panel.querySelector("#psvstudio-workflow").addEventListener("change", event => {
    const project = activeProject();
    if (!project) return;
    project.workflow_id = event.target.value;
    markProjectChanged();
    renderPreview();
  });
  panel.querySelector("#psvstudio-project-title").addEventListener("input", event => {
    const project = activeProject();
    if (!project) return;
    project.name = event.target.value;
    markProjectChanged();
    renderProjectList();
  });
  panel.querySelector("#psvstudio-mobile-projects").addEventListener("click", () => {
    state.drawer = state.drawer === "projects" ? "" : "projects";
    panel.dataset.drawer = state.drawer;
    panel.querySelector("#psvstudio-mobile-projects").setAttribute("aria-expanded", String(state.drawer === "projects"));
    panel.querySelector("#psvstudio-mobile-inspector").setAttribute("aria-expanded", "false");
  });
  panel.querySelector("#psvstudio-mobile-inspector").addEventListener("click", () => {
    state.drawer = state.drawer === "inspector" ? "" : "inspector";
    panel.dataset.drawer = state.drawer;
    panel.querySelector("#psvstudio-mobile-inspector").setAttribute("aria-expanded", String(state.drawer === "inspector"));
    panel.querySelector("#psvstudio-mobile-projects").setAttribute("aria-expanded", "false");
  });
  panel.querySelector("#psvstudio-close-inspector").addEventListener("click", () => {
    closeVideoDrawer();
  });
  panel.querySelector("#psvstudio-close-projects").addEventListener("click", () => closeVideoDrawer());
  panel.querySelector(".psvstudio-drawer-scrim").addEventListener("click", () => closeVideoDrawer());
  state.panel = panel;
  document.body.append(panel);
  installMediaDrop(document);
  installTransientUiDismissal(document);
  return panel;
}

let videoGenerationProgressController = null;
function setupProgressEvents() {
  videoGenerationProgressController ||= createVideoGenerationProgressController({ state, markGenerationExecuting,
    renderGenerations, failGeneration, executionFailureMessage, setApiConnected,
    renderSystemStatusSummary, freezeDisconnectedControls, isVideoStudioControl, isDisconnectedAllowedControl });
  videoGenerationProgressController.mount(api);
}

function setStandaloneVisibility(visible) {
  state.standaloneVisible = Boolean(visible);
  if (state.panel?.ownerDocument === state.popup?.document) {
    if (!state.standaloneVisible) {
      closeSystemStatus();
      closeVideoDrawer();
    }
    state.panel.hidden = !state.standaloneVisible;
  }
  postVideoStudioPresence();
}

const videoPopupController = createFeatureController({
  mount(popup, scope) {
    const ownerDocument = popup.document;
    const onPageHide = () => {
      if (state.popup !== popup) return;
      if (state.panel?.ownerDocument !== document) movePanelPreservingFocus(state.panel, document.body, { visible: false });
      state.panel.hidden = true;
      state.standaloneAttached = false;
      state.standaloneVisible = false;
      state.studioOpenedAt = 0;
      clearMediaDrag(ownerDocument);
      state.popup = null;
      videoPopupController.dispose();
      installMediaDrop(document);
      postVideoStudioPresence();
      persistProjects();
    };
    // Only committed navigation ends attachment, never cancelled beforeunload.
    popup.addEventListener("pagehide", onPageHide, { once: true });
    scope.own(() => popup.removeEventListener("pagehide", onPageHide));
    scope.interval(() => { if (state.popup === popup && popup.closed) onPageHide(); }, 250);
  },
});

async function attachStandalone(popup, { unified = false } = {}) {
  if (!popup || popup.closed) return false;
  try {
    if (popup.location.origin !== window.location.origin) return false;
  } catch (_) {
    return false;
  }
  const mount = popup.document.querySelector("#promptstudio-video-mount");
  if (!mount || !state.panel) return false;
  state.popup = popup;
  movePanelPreservingFocus(state.panel, mount, { visible: !unified });
  state.standaloneAttached = true;
  state.standaloneVisible = !unified;
  state.panel.hidden = !state.standaloneVisible;
  state.studioOpenedAt = Date.now();
  installMediaDrop(popup.document);
  installTransientUiDismissal(popup.document);
  // Adopt the current editor and media nodes without resetting focus/playback.
  postVideoStudioPresence();
  videoPopupController.mount(popup);
  return true;
}

function setupStandaloneBridge() {
  globalThis.__promptstudioVideoStudioHost = {
    attach: attachStandalone,
    setStandaloneVisibility,
    status: videoStudioStatus,
    handoffImage: handoffPromptStudioImage,
  };
  if (typeof BroadcastChannel !== "function") return;
  state.bridgeChannel?.close();
  const channel = new BroadcastChannel(CHANNEL_NAME);
  state.bridgeChannel = channel;
  channel.addEventListener("message", async event => {
    const data = event.data;
    if (data?.type === "studio-probe") {
      postVideoStudioPresence();
      return;
    }
    if (data?.type !== "handoff-image" || data.targetInstanceId !== STUDIO_INSTANCE_ID || !data.requestId) return;
    try {
      const result = await handoffPromptStudioImage(data.image);
      channel.postMessage({ type: "handoff-result", requestId: data.requestId, ok: true, result });
    } catch (error) {
      const message = error.message || String(error);
      setStatus(message, "error");
      channel.postMessage({ type: "handoff-result", requestId: data.requestId, ok: false, error: message });
    }
  });
  state.bridgePresenceTimer?.();
  state.bridgePresenceTimer = studioPollingScope().add(postVideoStudioPresence,{interval:2500,hiddenInterval:2500});
}

function resumeGenerationPolling() {
  for (const project of state.projects) {
    for (const generation of project.generations || []) {
      if (["queued", "generating"].includes(generation.status) && generation.prompt_id) pollGeneration(generation.prompt_id);
    }
  }
}

function resumeVideoPreparations() {
  if (state.projectConflicts.length) return;
  for (const project of state.projects) {
    for (const operation of project.generations || []) {
      if (!operation.id || operation.prompt_id || !["validating", "compiling", "queueing"].includes(operation.status)) continue;
      const workflow = state.workflows.find(item => item.id === operation.workflow_id) || (
        operation.workflow_snapshot ? {
          id: operation.workflow_id,
          name: operation.workflow_name,
          director_node_id: operation.workflow_director_node_id,
          result_node_ids: clone(operation.result_node_ids || []),
          result_fields: clone(operation.result_fields || []),
          snapshot: clone(operation.workflow_snapshot),
        } : null
      );
      if (!workflow) {
        Object.assign(operation, {
          status: "error",
          error: "The workflow needed to resume this operation is unavailable.",
          updated_at: Date.now(),
        });
        markProjectChanged({ project, render: project.id === state.activeProjectId });
        persistProjects();
        continue;
      }
      if (state.generationControllers.has(operation.id)) continue;
      const controller = new AbortController();
      state.generationControllers.set(operation.id, controller);
      (async () => {
        try {
          if (operation.preparation_kind === "replay") {
            if (!operation.workflow_snapshot) throw new Error("The saved replay workflow is unavailable.");
            Object.assign(operation, { status: "queueing", updated_at: Date.now() });
            markProjectChanged({ project, render: project.id === state.activeProjectId });
            await queueSnapshot(project, workflow, clone(operation.workflow_snapshot), {
              document: operation.document,
              compiled_prompt: operation.compiled_prompt,
              resolved_mode: operation.resolved_mode,
              frame_count: operation.frame_count,
              effective_duration: operation.effective_duration,
              kind: operation.kind,
              parent_generation_id: operation.parent_generation_id,
              root_generation_id: operation.root_generation_id,
              depth: operation.depth,
              total_effective_duration: operation.total_effective_duration,
              continuation: operation.continuation,
            }, operation);
            return;
          }
          if (operation.preparation_kind === "continuation") {
            const request = operation.continuation_request;
            if (!request || !operation.workflow_snapshot) {
              throw new Error("The continuation preparation data is unavailable.");
            }
            Object.assign(operation, { status: "compiling", updated_at: Date.now() });
            markProjectChanged({ project, render: project.id === state.activeProjectId });
            const response = await api.fetchApi(CONTINUATION_PREPARE_ENDPOINT, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              signal: controller.signal,
              body: JSON.stringify({
                document: request.source_document || operation.document,
                extension_document: request.extension_document,
                source: request.source,
                brief: request.brief,
                duration_seconds: request.duration_seconds,
                context_frames: request.context_frames || CONTINUATION_CONTEXT_FRAMES,
              }),
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(data.error || "The continuation prompt could not be prepared.");
            const snapshot = clone(operation.workflow_snapshot);
            const directorId = String(operation.workflow_director_node_id || workflow.director_node_id || "");
            const director = snapshot.output?.[directorId];
            if (!director) throw new Error("The saved continuation workflow has no Director node.");
            director.inputs ||= {};
            director.inputs.document_json = JSON.stringify(data.document);
            if (operation.new_seed !== false) randomizeSnapshotSeeds(snapshot);
            Object.assign(operation, { status: "queueing", document: clone(data.document), updated_at: Date.now() });
            markProjectChanged({ project, render: project.id === state.activeProjectId });
            await queueSnapshot(project, {
              ...workflow,
              director_node_id: directorId,
              result_node_ids: clone(operation.result_node_ids || workflow.result_node_ids || []),
              result_fields: clone(operation.result_fields || workflow.result_fields || []),
            }, snapshot, {
              ...data,
              kind: "extension",
              parent_generation_id: operation.parent_generation_id,
              root_generation_id: operation.root_generation_id,
              depth: operation.depth,
              total_effective_duration: Number(operation.continuation_base_duration || 0)
                + Number(data.effective_duration || 0),
              continuation: {
                ...data.continuation,
                source_generation_id: operation.parent_generation_id,
                source_segments: clone(request.source_segments || []),
                parent_context_latent_path: request.parent_context_latent_path || "",
                brief: request.brief,
                structured: Boolean(request.extension_document),
                source_assembly_segments: clone(request.source_assembly_segments || request.source_segments || []),
              },
            }, operation);
            return;
          }
          Object.assign(operation, { status: "validating", updated_at: Date.now() });
          markProjectChanged({ project, render: project.id === state.activeProjectId });
          const response = await api.fetchApi("/promptstudio-video/document/compile", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            signal: controller.signal,
            body: JSON.stringify({ document: operation.document || project.document }),
          });
          const data = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(data.error || "The video document is invalid.");
          Object.assign(operation, { status: "compiling", updated_at: Date.now() });
          project.document = data.document;
          project.brief = project.document.main_description || "";
          const snapshot = clone(workflow.snapshot);
          const director = snapshot.output?.[workflow.director_node_id];
          if (!director) throw new Error("The selected workflow no longer contains its Director node.");
          director.inputs ||= {};
          director.inputs.document_json = JSON.stringify(data.document);
          if (operation.new_seed !== false) randomizeSnapshotSeeds(snapshot);
          Object.assign(operation, { status: "queueing", updated_at: Date.now() });
          markProjectChanged({ project, render: project.id === state.activeProjectId });
          await queueSnapshot(project, workflow, snapshot, data, operation);
        } catch (error) {
          const cancelled = controller.signal.aborted;
          Object.assign(operation, {
            status: cancelled ? "cancelled" : "error",
            error: cancelled ? "Cancelled." : (error.message || String(error)),
            updated_at: Date.now(),
          });
          markProjectChanged({ project, render: project.id === state.activeProjectId });
          await persistProjects();
        } finally {
          state.generationControllers.delete(operation.id);
        }
      })();
    }
  }
}

app.registerExtension({
  name: EXTENSION_NAME,
  async setup() {
    buildPanel();
    setupProgressEvents();
    startKoboldStatusMonitor();
    try {
      await loadConfig();
      await Promise.all([loadProjects(), loadWorkflowCache()]);
      state.ready = true;
      renderAll();
      await refreshWorkflows({ announce: false });
      if (!state.projects.length) setStatus("Create a video project to begin.", "ready");
      else if (!state.workflows.length) setStatus("No [PSV] workflow found. Save the working workflow with a [PSV] filename prefix, then refresh.", "warning");
      else setStatus("Video Studio is ready.", "ready");
      if (!state.workflows.length) await offerDefaultWorkflowSetup();
      else await resumeDefaultSetupMonitor();
      resumeGenerationPolling();
      resumeVideoPreparations();
      resumeDirectorJobs();
    } catch (error) {
      setStatus(error.message || String(error), "error");
    }
    setupStandaloneBridge();
  },
});
