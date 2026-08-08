import { app } from "/scripts/app.js";
import { api } from "/scripts/api.js";

const EXTENSION_NAME = "PromptStudio.Video.Standalone";
const CHANNEL_NAME = "promptstudio.video.standalone.v1";
const DIRECTOR_TYPE = "PSV_MiniMaxH3Director";
const WORKFLOW_PREFIX = "[PSV]";
const PROJECTS_ENDPOINT = "/promptstudio-video/projects";
const WORKFLOWS_ENDPOINT = "/promptstudio-video/workflows";
const DIRECTOR_CHAT_ENDPOINT = "/promptstudio-video/director/chat";
const DIRECTOR_PREVIEW_ENDPOINT = "/promptstudio-video/director/preview";
const KOBOLD_STATUS_ENDPOINT = "/promptstudio-video/kobold/status";
const KOBOLD_ABORT_ENDPOINT = "/promptstudio-video/kobold/abort";
const DIRECTOR_JOB_POLL_MS = 1000;
const DIRECTOR_STATUS_RETRY_LIMIT = 3;
const KOBOLD_STATUS_POLL_MS = 3000;
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
const DISCONNECTED_GENERATION_GRACE_MS = 15 * 1000;
const GENERATION_STALL_TIMEOUT_MS = 5 * 60 * 1000;
const STUDIO_INSTANCE_ID = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;

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
  relayedHandoffs: new Set(),
  studioOpenedAt: 0,
  config: null,
  projects: [],
  activeProjectId: null,
  projectRevision: 0,
  projectMutation: 0,
  projectSavedMutation: 0,
  projectSaveTimer: null,
  projectSaveChain: Promise.resolve(),
  workflows: [],
  workflowRevision: 0,
  selectedShotId: null,
  generationProgress: new Map(),
  generationPollers: new Map(),
  generationActivity: new Map(),
  generationFailures: new Map(),
  loopingGenerations: new Set(),
  promptWorkerSeenAlive: false,
  promptWorkerHealthCheckedAt: 0,
  promptWorkerHealthRequest: null,
  apiConnected: true,
  disconnectedGenerationTimer: null,
  timelineZoom: 80,
  shotDrag: null,
  shotStepDragId: "",
  shotEditorDialog: null,
  shotEditorDraft: null,
  shotEditorOriginal: null,
  shotEditorShotId: "",
  shotEditorExpandedStepIds: new Set(),
  shotEditorRevealStepId: "",
  mediaDragId: "",
  mediaDropDocuments: new WeakSet(),
  mediaDropDepth: new WeakMap(),
  mediaDimensionLoads: new Set(),
  drawer: "",
  directorDialog: null,
  directorBusy: false,
  directorPendingText: "",
  directorSessions: null,
  directorScope: "shot",
  koboldStatusRequest: null,
  koboldStatusTimer: null,
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

function directorSettings() {
  const studio = storedObject(IMAGE_STUDIO_SETTINGS_KEY);
  const consult = storedObject(IMAGE_CONSULT_SETTINGS_KEY);
  const video = storedObject(DIRECTOR_SETTINGS_KEY);
  const storedResponseTokens = Number(video.max_response_tokens);
  const storedTimeout = Number(video.request_timeout);
  const responseTokens = !Object.hasOwn(video, "max_response_tokens") || storedResponseTokens === 700
    ? 0
    : Math.max(0, Math.min(131072, Number.isFinite(storedResponseTokens) ? storedResponseTokens : 0));
  return {
    llm_provider: video.llm_provider || studio.llm_provider || "koboldcpp",
    kobold_url: video.kobold_url || studio.kobold_url || "http://localhost:5001",
    ollama_url: video.ollama_url || studio.ollama_url || "http://localhost:11434",
    ollama_model: video.ollama_model || studio.ollama_model || "",
    thinking_mode: video.thinking_mode || consult.thinking_mode || "Disabled",
    max_response_tokens: responseTokens,
    context_budget_chars: Math.max(4000, Math.min(32000, Number(video.context_budget_chars || 8000))),
    temperature: Number(video.temperature ?? consult.temperature ?? 0.7),
    top_p: Number(video.top_p ?? consult.top_p ?? 0.9),
    top_k: Number(video.top_k ?? consult.top_k ?? 100),
    min_p: Number(video.min_p ?? consult.min_p ?? 0),
    rep_pen: Number(video.rep_pen ?? consult.rep_pen ?? 1.05),
    rep_pen_range: Number(video.rep_pen_range ?? consult.rep_pen_range ?? 360),
    sampler_seed: Number(video.sampler_seed ?? consult.sampler_seed ?? -1),
    request_timeout: !Object.hasOwn(video, "request_timeout") || storedTimeout === 120
      ? 600
      : Math.max(5, Math.min(3600, Number.isFinite(storedTimeout) ? storedTimeout : 600)),
  };
}

function directorControlValue(dialog, id) {
  return dialog?.querySelector(`#${id}`)?.value;
}

function saveDirectorSettings(dialog = state.directorDialog) {
  if (!dialog) return directorSettings();
  const settings = {
    llm_provider: directorControlValue(dialog, "psvstudio-director-provider") || "koboldcpp",
    kobold_url: directorControlValue(dialog, "psvstudio-director-kobold-url") || "http://localhost:5001",
    ollama_url: directorControlValue(dialog, "psvstudio-director-ollama-url") || "http://localhost:11434",
    ollama_model: directorControlValue(dialog, "psvstudio-director-ollama-model") || "",
    thinking_mode: directorControlValue(dialog, "psvstudio-director-thinking") || "Disabled",
    max_response_tokens: Math.max(0, Math.min(131072, Number(directorControlValue(dialog, "psvstudio-director-max-tokens") || 0))),
    context_budget_chars: Number(directorControlValue(dialog, "psvstudio-director-context-budget") || 8000),
    temperature: 0.7,
    top_p: 0.9,
    top_k: 100,
    min_p: 0,
    rep_pen: 1.05,
    rep_pen_range: 360,
    sampler_seed: -1,
    request_timeout: Math.max(5, Math.min(3600, Number(directorControlValue(dialog, "psvstudio-director-timeout") || 600))),
  };
  try {
    localStorage.setItem(DIRECTOR_SETTINGS_KEY, JSON.stringify(settings));
  } catch (_) {
    // The active request can still use these settings when storage is unavailable.
  }
  return settings;
}

function renderKoboldStatus(status = {}) {
  const control = state.panel?.querySelector("#psvstudio-kobold-control");
  const label = control?.querySelector("#psvstudio-kobold-status-label");
  const detail = control?.querySelector("#psvstudio-kobold-status-detail");
  const stop = control?.querySelector("#psvstudio-kobold-stop");
  if (!control || !label || !detail || !stop) return;
  const reachable = status.reachable === true;
  const busy = reachable && status.busy === true;
  const stateName = busy ? "busy" : (reachable ? "idle" : (status.checking ? "checking" : "offline"));
  control.dataset.state = stateName;
  label.textContent = busy ? "Kobold busy" : (reachable ? "Kobold idle" : (status.checking ? "Kobold…" : "Kobold offline"));
  const characters = Number(status.generated_characters);
  detail.textContent = status.message || (busy
    ? `Generation active${Number.isFinite(characters) && characters > 0 ? ` · ${characters.toLocaleString()} characters` : ""}`
    : (reachable ? "Ready for local requests." : "KoboldCpp could not be reached."));
  stop.disabled = !busy || state.koboldAbortBusy;
  stop.textContent = state.koboldAbortBusy ? "Stopping…" : "Stop generation";
}

async function refreshKoboldStatus() {
  if (state.koboldStatusRequest) return state.koboldStatusRequest;
  const settings = directorSettings();
  const control = state.panel?.querySelector("#psvstudio-kobold-control");
  if (!control || control.dataset.state === "checking") renderKoboldStatus({ checking: true });
  state.koboldStatusRequest = (async () => {
    try {
      const response = await api.fetchApi(KOBOLD_STATUS_ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kobold_url: settings.kobold_url }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || `KoboldCpp status failed (${response.status}).`);
      renderKoboldStatus(data);
      return data;
    } catch (error) {
      renderKoboldStatus({ reachable: false, message: error.message || String(error) });
      return null;
    } finally {
      state.koboldStatusRequest = null;
    }
  })();
  return state.koboldStatusRequest;
}

async function stopKoboldGeneration() {
  if (state.koboldAbortBusy) return;
  state.koboldAbortBusy = true;
  renderKoboldStatus({ reachable: true, busy: true, message: "Sending stop signal…" });
  try {
    const response = await api.fetchApi(KOBOLD_ABORT_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kobold_url: directorSettings().kobold_url }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `KoboldCpp stop failed (${response.status}).`);
    renderKoboldStatus({
      reachable: true,
      busy: data.success !== true,
      message: data.success ? "Stop signal accepted." : "KoboldCpp reported no abortable generation.",
    });
  } catch (error) {
    renderKoboldStatus({ reachable: false, message: error.message || String(error) });
  } finally {
    state.koboldAbortBusy = false;
    window.setTimeout(refreshKoboldStatus, 500);
  }
}

function startKoboldStatusMonitor() {
  if (state.koboldStatusTimer) return;
  refreshKoboldStatus();
  state.koboldStatusTimer = window.setInterval(refreshKoboldStatus, KOBOLD_STATUS_POLL_MS);
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
  if (sessions[id].pending_plan?.clarification_id === "proposal-validation") {
    sessions[id].pending_plan = null;
  }
  return sessions[id];
}

function persistDirectorSessions() {
  const sessions = directorSessions();
  for (const session of Object.values(sessions)) {
    if (!Array.isArray(session?.messages)) continue;
    session.messages = session.messages.slice(-30).map(message => {
      if (message?.role === "assistant") ensureDirectorVariants(message);
      return {
        ...message,
        text: String(message?.text || "").slice(0, 4000),
        variants: Array.isArray(message?.variants) ? message.variants.map(variant => ({
          ...variant,
          text: String(variant?.text || "").slice(0, 4000),
        })) : undefined,
      };
    });
  }
  const retained = Object.fromEntries(
    Object.entries(sessions)
      .sort((a, b) => Number(b[1]?.updated_at || 0) - Number(a[1]?.updated_at || 0))
      .slice(0, 20)
      .map(([id, session]) => [id, {
        scope: session?.scope === "project" ? "project" : "shot",
        updated_at: Number(session?.updated_at || Date.now()),
        last_context_usage: session?.last_context_usage || null,
        pending_plan: session?.pending_plan || null,
        messages: Array.isArray(session?.messages) ? session.messages : [],
      }]),
  );
  try {
    localStorage.setItem(DIRECTOR_SESSIONS_KEY, JSON.stringify(retained));
  } catch (_) {
    // Chat remains available for the current page when storage is unavailable.
  }
}

function boundedDirectorMessages(messages, maximumChars = 6500) {
  const normalized = (messages || [])
    .filter(message => ["user", "assistant"].includes(message?.role) && String(message?.text || "").trim())
    .map(message => ({ role: message.role, content: String(message.text).trim() }));
  const retained = [];
  let used = 0;
  for (const message of normalized.slice().reverse()) {
    const cost = message.content.length + 32;
    if (retained.length && used + cost > maximumChars) break;
    retained.push(message);
    used += cost;
    if (retained.length >= 10) break;
  }
  return retained.reverse();
}

function directorShotLabel(project = activeProject(), shot = selectedShot(project)) {
  const index = project?.document?.shots?.indexOf(shot) ?? -1;
  return index >= 0 ? `Shot ${index + 1}` : "Selected shot";
}

function directorProposalFields(proposal) {
  const rows = [];
  for (const operation of proposal?.operations || []) {
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
  const references = project.document.references || [];
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
    ? "Grand Director · Entire video"
    : `Shot Director · ${directorShotLabel(project, shot)}`;
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
  history.replaceChildren();
  const session = directorSession(project.id);
  if (session.pending_plan) {
    input.placeholder = "Answer the Director's clarification to continue the pending plan…";
  }
  if (!session.messages.length) {
    history.append(el("div", "psvstudio-director-empty", projectScope
      ? "Ask for a full-video critique, alternatives, or a multi-shot composition. Only approved proposals can change the project."
      : "Ask for advice, alternatives, or a concrete revision. Only approved proposals can change the selected shot."));
  }
  for (const [messageIndex, message] of session.messages.entries()) {
    if (message.role === "assistant") ensureDirectorVariants(message);
    const card = el("article", `psvstudio-director-message is-${message.role}`);
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
        apply.disabled = state.directorBusy;
        discard.disabled = state.directorBusy;
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
        choose.disabled = state.directorBusy;
        choices.append(choose);
      }
      if (choices.childElementCount) clarification.append(choices);
      clarification.append(el("small", "psvstudio-help", "Your answer continues the existing request; no proposal has been discarded or applied."));
      card.append(clarification);
    } else if (message.proposal_error) {
      card.append(el("small", "psvstudio-director-error", `Proposal not applied: ${message.proposal_error}`));
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
        previous.disabled = state.directorBusy;
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
      next.disabled = state.directorBusy;
      controls.append(next);
      card.append(controls);
    }
    history.append(card);
  }
  if (state.directorBusy && state.directorPendingText) {
    const pending = el("article", "psvstudio-director-message is-assistant is-pending");
    const pendingText = el("div", "psvstudio-director-message-text", state.directorPendingText);
    pendingText.dataset.directorPending = "true";
    pendingText.setAttribute("role", "status");
    pendingText.setAttribute("aria-live", "polite");
    pending.append(el("small", "", "Director"), pendingText);
    history.append(pending);
  }
  const usage = session.last_context_usage;
  const status = dialog.querySelector("#psvstudio-director-status");
  if (!state.directorBusy && usage) {
    const omitted = Number(usage.omitted_messages || 0);
      status.textContent = `Sent ${usage.history_messages} recent messages · ${usage.context_chars + usage.history_chars} context characters${omitted ? ` · omitted ${omitted} older messages` : ""}`;
  } else if (!state.directorBusy) {
    status.textContent = projectScope
      ? "The full video is in context; project changes require approval."
      : "Only the selected shot is in write scope.";
  }
  const send = dialog.querySelector("#psvstudio-director-send");
  if (send) send.disabled = state.directorBusy;
  const clear = dialog.querySelector("#psvstudio-director-clear");
  if (clear) clear.disabled = state.directorBusy;
  renderDirectorAttachments();
  history.scrollTop = history.scrollHeight;
}

function clearDirectorSession() {
  const dialog = state.directorDialog;
  const project = activeProject();
  if (!dialog || !project || state.directorBusy) return;
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
      <textarea id="psvstudio-director-input" rows="3" placeholder="Ask about this shot or request a concrete revision…"></textarea>
      <div class="psvstudio-director-composer-actions"><small id="psvstudio-director-status">Only the selected shot is in write scope.</small><div class="psvstudio-inline"><button id="psvstudio-director-add-image" class="psvstudio-button" type="button">Add image</button><button id="psvstudio-director-send" class="psvstudio-button psvstudio-button-primary" type="button">Ask Director</button></div></div>
      <input id="psvstudio-director-image-input" class="psvstudio-sr-only" type="file" accept="image/*" multiple />
      </div>
    </div>
    </div>
    <details class="psvstudio-director-settings"><summary>Local LLM and context settings</summary><div class="psvstudio-director-settings-grid">
      <label><span>Provider</span><select id="psvstudio-director-provider"><option value="koboldcpp">KoboldCpp</option><option value="ollama">Ollama</option></select></label>
      <label><span>Thinking</span><select id="psvstudio-director-thinking">${["Disabled", "Minimal", "Low", "Medium", "High"].map(value => `<option value="${value}">${value}</option>`).join("")}</select></label>
      <label><span>KoboldCpp URL</span><input id="psvstudio-director-kobold-url" /></label>
      <label><span>Ollama URL</span><input id="psvstudio-director-ollama-url" /></label>
      <label><span>Ollama model</span><input id="psvstudio-director-ollama-model" placeholder="model:tag" /></label>
      <label><span>Response tokens · 0 = full available context</span><input id="psvstudio-director-max-tokens" type="number" min="0" max="131072" step="128" /></label>
      <label><span>Context characters</span><input id="psvstudio-director-context-budget" type="number" min="4000" max="32000" step="1000" /></label>
      <label><span>Request timeout seconds</span><input id="psvstudio-director-timeout" type="number" min="5" max="3600" step="30" /></label>
    </div><small>Approved document state plus at most ten recent messages are sent. Older conversation stays stored locally.</small></details>`;
  dialog.querySelector("#psvstudio-director-provider").value = settings.llm_provider;
  dialog.querySelector("#psvstudio-director-thinking").value = settings.thinking_mode;
  dialog.querySelector("#psvstudio-director-kobold-url").value = settings.kobold_url;
  dialog.querySelector("#psvstudio-director-ollama-url").value = settings.ollama_url;
  dialog.querySelector("#psvstudio-director-ollama-model").value = settings.ollama_model;
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

async function requestDirectorResponse(project, shot, scope, attachments, messages) {
  const normalizedScope = scope === "project" ? "project" : "shot";
  const projectScope = normalizedScope === "project";
  const settings = saveDirectorSettings(state.directorDialog);
  const response = await api.fetchApi(DIRECTOR_CHAT_ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...settings,
      async: true,
      project_name: project.name,
      brief: project.brief,
      document: project.document,
      scope: normalizedScope,
      selected_shot_id: projectScope ? "" : (shot?.id || ""),
      attachments,
      pending_plan: directorSession(project.id, normalizedScope).pending_plan || null,
      messages: boundedDirectorMessages(messages),
    }),
  });
  let data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Director request failed (${response.status}).`);
  if (response.status === 202 && data.job_id) data = await pollDirectorJob(data.job_id);
  if (data.scope && data.scope !== normalizedScope) {
    throw new Error(`Director scope mismatch: requested ${normalizedScope}, received ${data.scope}. Refresh Video Studio and try again.`);
  }
  return data;
}

function directorJobStatusText(job) {
  if (job.status === "queued") return "Director request is queued…";
  const progress = job.director_progress || {};
  if (progress.phase === "proposal_correction") {
    const attempt = Math.max(1, Number(progress.attempt) || 1);
    const maximum = Math.max(attempt, Number(progress.maximum_attempts) || attempt);
    const activity = job.provider_status?.busy ? "generating" : "queued";
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
    if (provider.busy && Number.isFinite(characters) && characters > 0) {
      return `${prefix}Director is generating · ${characters.toLocaleString()} characters received…`;
    }
    return `${prefix}Director is generating the response…`;
  }
  if (provider.provider !== "koboldcpp") return "The local Director is still working…";
  if (provider.reachable === false) return "Director is running; KoboldCpp status is temporarily unavailable…";
  if (provider.busy) {
    const characters = Number(provider.generated_characters);
    return Number.isFinite(characters) && characters > 0
      ? `KoboldCpp is still generating · ${characters.toLocaleString()} characters received…`
      : "KoboldCpp is still processing the prompt…";
  }
  return "Director is validating KoboldCpp's response…";
}

async function pollDirectorJob(jobId) {
  let statusFailures = 0;
  while (true) {
    await new Promise(resolve => setTimeout(resolve, DIRECTOR_JOB_POLL_MS));
    const response = await api.fetchApi(`${DIRECTOR_CHAT_ENDPOINT}/${encodeURIComponent(jobId)}`);
    const job = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (response.status >= 500 && statusFailures < DIRECTOR_STATUS_RETRY_LIMIT) {
        statusFailures += 1;
        setDirectorProgress("Director status was temporarily unavailable; retrying…");
        continue;
      }
      throw new Error(job.error || `Director status check failed (${response.status}).`);
    }
    statusFailures = 0;
    if (job.status === "complete") return job.result || {};
    if (job.status === "failed") throw new Error(job.error || "Director request failed.");
    setDirectorProgress(directorJobStatusText(job));
  }
}

function setDirectorProgress(text) {
  state.directorPendingText = String(text || "");
  const dialog = state.directorDialog;
  const status = dialog?.querySelector("#psvstudio-director-status");
  const pending = dialog?.querySelector("[data-director-pending]");
  if (status) status.textContent = state.directorPendingText;
  if (pending) pending.textContent = state.directorPendingText;
}

async function sendDirectorMessage() {
  const dialog = state.directorDialog;
  const project = activeProject();
  const shot = selectedShot(project);
  const input = dialog?.querySelector("#psvstudio-director-input");
  let text = String(input?.value || "").trim();
  const directorScope = state.directorScope === "project" ? "project" : "shot";
  const projectScope = directorScope === "project";
  if (!dialog || !project || (!projectScope && !shot) || state.directorBusy) return;
  const session = directorSession(project.id, directorScope);
  const attachments = directorRequestAttachments(project, session.draft_attachments);
  if (!text && !attachments.length) return;
  if (!text) text = projectScope
    ? "Inspect the attached image in the context of the entire video. Suggest concrete production improvements and propose multi-shot changes when useful."
    : "Inspect the attached image and suggest concrete improvements for the selected shot. Propose descriptive shot changes when useful.";
  commitDirectorImageReferences(project, attachments);
  session.messages.push({
    id: makeId("director-message"), role: "user", text,
    attachments,
    created_at: Date.now(),
  });
  session.updated_at = Date.now();
  input.value = "";
  persistDirectorSessions();
  state.directorBusy = true;
  setDirectorProgress("Consulting the local Director…");
  renderDirectorDialog();
  const projectId = project.id;
  try {
    const data = await requestDirectorResponse(project, shot, directorScope, attachments, session.messages);
    const groundingChanged = cacheDirectorVisionObservations(project, attachments, data.vision_observations);
    const assistant = {
      id: makeId("director-message"), role: "assistant", text: String(data.message || "").trim() || "No response.",
      proposal: data.proposal || null, proposal_error: String(data.proposal_error || ""),
      status: String(data.status || "ready"), clarification: data.clarification || null,
      pending_plan: data.pending_plan || null,
      context_usage: data.context_usage || null, created_at: Date.now(),
    };
    ensureDirectorVariants(assistant);
    session.messages.push(assistant);
    session.last_context_usage = data.context_usage || null;
    session.pending_plan = data.pending_plan || null;
    if (data.status !== "needs_clarification") session.draft_attachments = [];
    session.updated_at = Date.now();
    if (groundingChanged) markProjectChanged();
    persistDirectorSessions();
  } catch (error) {
    const assistant = { id: makeId("director-message"), role: "assistant", text: `Director request failed: ${error.message || String(error)}`, proposal_error: "", created_at: Date.now() };
    ensureDirectorVariants(assistant);
    session.messages.push(assistant);
    session.updated_at = Date.now();
    persistDirectorSessions();
  } finally {
    state.directorBusy = false;
    state.directorPendingText = "";
    if (activeProject()?.id === projectId) renderDirectorDialog();
  }
}

function selectDirectorResponse(messageId, variantIndex) {
  if (state.directorBusy) return;
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

async function regenerateDirectorResponse(messageId) {
  if (state.directorBusy) return;
  const dialog = state.directorDialog;
  const project = activeProject();
  const shot = selectedShot(project);
  const directorScope = state.directorScope === "project" ? "project" : "shot";
  const projectScope = directorScope === "project";
  const session = directorSession(project?.id, directorScope);
  const messageIndex = session?.messages?.findIndex(message => message.id === messageId) ?? -1;
  const message = session?.messages?.[messageIndex];
  const userMessage = session?.messages?.[messageIndex - 1];
  if (
    !dialog || !project || (!projectScope && !shot)
    || messageIndex !== session.messages.length - 1
    || message?.role !== "assistant" || userMessage?.role !== "user"
  ) return;

  const attachments = directorAttachmentMetadata(userMessage.attachments).filter(attachment => attachment.path);
  const requestMessages = session.messages.slice(0, messageIndex);
  session.messages.splice(messageIndex, 1);
  state.directorBusy = true;
  setDirectorProgress("Regenerating the Director answer…");
  renderDirectorDialog();
  const projectId = project.id;
  let failure = "";
  try {
    const data = await requestDirectorResponse(
      project,
      shot,
      directorScope,
      attachments,
      requestMessages,
    );
    const groundingChanged = cacheDirectorVisionObservations(project, attachments, data.vision_observations);
    const variants = ensureDirectorVariants(message);
    variants.push({
      id: makeId("director-response"),
      text: String(data.message || "").trim() || "No response.",
      proposal: data.proposal || null,
      proposal_error: String(data.proposal_error || ""),
      proposal_state: "",
      status: String(data.status || "ready"),
      clarification: data.clarification || null,
      pending_plan: data.pending_plan || null,
      context_usage: data.context_usage || null,
      created_at: Date.now(),
    });
    syncDirectorVariant(message, variants.length - 1);
    message.created_at ||= Date.now();
    session.last_context_usage = data.context_usage || null;
    session.pending_plan = data.pending_plan || null;
    if (data.status !== "needs_clarification") {
      const regeneratedPaths = new Set(attachments.map(attachment => attachment.path));
      session.draft_attachments = session.draft_attachments.filter(attachment => !regeneratedPaths.has(attachment.path));
    }
    if (groundingChanged) markProjectChanged();
    session.updated_at = Date.now();
  } catch (error) {
    failure = error.message || String(error);
    const variants = ensureDirectorVariants(message);
    variants.push({
      id: makeId("director-response"),
      text: `Director request failed: ${failure}`,
      proposal: null,
      proposal_error: "",
      proposal_state: "",
      status: "error",
      clarification: null,
      pending_plan: null,
      context_usage: null,
      created_at: Date.now(),
    });
    syncDirectorVariant(message, variants.length - 1);
  } finally {
    if (!session.messages.includes(message)) {
      session.messages.splice(Math.min(messageIndex, session.messages.length), 0, message);
    }
    session.updated_at = Date.now();
    persistDirectorSessions();
    state.directorBusy = false;
    state.directorPendingText = "";
    if (activeProject()?.id === projectId) {
      renderDirectorDialog();
      if (failure) dialog.querySelector("#psvstudio-director-status").textContent = failure;
    }
  }
}

async function applyDirectorProposal(messageId) {
  const project = activeProject();
  const session = directorSession(project?.id);
  const message = session.messages.find(item => item.id === messageId);
  if (!project || !message?.proposal || state.directorBusy) return;
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
  if (["wav", "mp3", "flac", "ogg", "m4a", "aac"].includes(extension)) return "audio";
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
  ];
}

function setReferenceRole(project, reference, role) {
  const roleChanged = reference.roles?.length !== 1 || reference.roles[0] !== role;
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
  const typeName = reference.kind === "image" ? "Picture" : reference.kind === "video" ? "Video" : "Audio";
  const ordinal = references.filter(item => item.kind === reference.kind).findIndex(item => item.id === reference.id) + 1;
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
  project.document.references = (project.document.references || []).filter(item => item.id !== reference.id);
  project.document.references.forEach(item => { item.label = ""; });
  invalidateReferenceSemantics(project);
  if (project.document.canvas_reference_id === reference.id) synchronizeGeometryCanvas(project);
  markProjectChanged({ render: true });
  setStatus(`${displayLabel} removed from project references.`, "ready");
}

function mediaLimit(project, kind) {
  const limits = state.config?.reference_limits || {};
  const key = kind === "image" ? "images" : kind === "video" ? "videos" : "audio_tracks";
  const references = project.document.references || [];
  if (references.length >= Number(limits.active_items || 12)) return "The project already has the maximum number of active references.";
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
      };
      project.document.references.push(reference);
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

function videoStudioStatus() {
  const project = activeProject();
  const open = Boolean(state.ready && state.panel && !state.panel.hidden);
  return {
    instanceId: STUDIO_INSTANCE_ID,
    open,
    openedAt: state.studioOpenedAt,
    activeProjectId: open ? project?.id || "" : "",
    projectName: open && project ? project.name || "Untitled video" : "",
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
  const project = activeProject();
  if (!status.open || !project) throw new Error("Video Studio no longer has an open project.");
  if (value?.targetProjectId && value.targetProjectId !== project.id) {
    throw new Error("The open Video Studio project changed before the image handoff started.");
  }
  const sourceUrl = new URL(String(value?.url || ""), window.location.href);
  if (sourceUrl.origin !== window.location.origin) throw new Error("Video Studio only accepts same-origin Prompt Studio images.");
  const limitError = mediaLimit(project, "image");
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
  if (activeProject()?.id !== project.id) {
    throw new Error("The open Video Studio project changed during the image handoff. Try again in the intended project.");
  }
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

function installMediaDrop(doc) {
  if (!doc || state.mediaDropDocuments.has(doc)) return;
  state.mediaDropDocuments.add(doc);
  const activeHere = () => state.panel?.ownerDocument === doc && !state.panel.hidden;
  const targetsDirector = event => event.composedPath().some(target =>
    target?.classList?.contains("psvstudio-director-dialog") && target.open
  );
  doc.addEventListener("dragenter", event => {
    if (!activeHere() || !isFileDrag(event) || targetsDirector(event)) return;
    event.preventDefault();
    state.mediaDropDepth.set(doc, (state.mediaDropDepth.get(doc) || 0) + 1);
    doc.body?.classList.add("psvstudio-media-drag-active");
  }, true);
  doc.addEventListener("dragover", event => {
    if (!activeHere() || !isFileDrag(event) || targetsDirector(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  }, true);
  doc.addEventListener("dragleave", event => {
    if (!activeHere() || !isFileDrag(event) || targetsDirector(event)) return;
    const depth = Math.max(0, (state.mediaDropDepth.get(doc) || 0) - 1);
    state.mediaDropDepth.set(doc, depth);
    if (!depth || !event.relatedTarget) clearMediaDrag(doc);
  }, true);
  doc.addEventListener("drop", event => {
    if (!activeHere() || !isFileDrag(event) || targetsDirector(event)) return;
    event.preventDefault();
    event.stopPropagation();
    clearMediaDrag(doc);
    addMediaFiles(event.dataTransfer.files);
  }, true);
  doc.addEventListener("paste", event => {
    if (!activeHere() || targetsDirector(event)) return;
    const files = clipboardImageFiles(event);
    if (!files.length) return;
    event.preventDefault();
    event.stopPropagation();
    addMediaFiles(files);
  }, true);
}

function activeProject() {
  return state.projects.find(project => project.id === state.activeProjectId) || null;
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
  const references = document?.references || [];
  const roles = new Set(references.flatMap(reference => reference.roles || []));
  if (references.some(reference => ["video", "audio"].includes(reference.kind))) return "ref2va";
  if ([...roles].some(role => !["first_frame", "last_frame"].includes(role))) return "ref2va";
  if (roles.has("first_frame") && roles.has("last_frame")) return "fl2va";
  if (roles.has("first_frame")) return "i2va";
  if (roles.has("last_frame")) return "l2va";
  return "t2va";
}

function setStatus(message, kind = "") {
  const status = state.panel?.querySelector("#psvstudio-status");
  if (!status) return;
  status.textContent = message;
  status.dataset.kind = kind;
}

function setSaveState(message) {
  const status = state.panel?.querySelector("#psvstudio-save-state");
  if (status) status.textContent = message;
}

function markProjectChanged({ render = false } = {}) {
  const project = activeProject();
  if (project) project.updated_at = Date.now();
  state.projectMutation += 1;
  setSaveState("Saving…");
  if (state.projectSaveTimer) clearTimeout(state.projectSaveTimer);
  state.projectSaveTimer = setTimeout(() => persistProjects(), 450);
  if (render) renderAll();
}

async function persistProjects({ immediate = false } = {}) {
  if (state.projectSaveTimer) clearTimeout(state.projectSaveTimer);
  state.projectSaveTimer = null;
  const mutation = state.projectMutation;
  if (!immediate && mutation === state.projectSavedMutation) return;
  const operation = state.projectSaveChain.catch(() => {}).then(async () => {
    const payload = {
      version: 2,
      revision: state.projectRevision,
      active_project_id: state.activeProjectId,
      projects: clone(state.projects),
    };
    const response = await api.fetchApi(PROJECTS_ENDPOINT, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Projects could not be saved (${response.status}).`);
    state.projectRevision = Number(data.revision || state.projectRevision);
    state.projectSavedMutation = Math.max(state.projectSavedMutation, mutation);
    setSaveState(state.projectSavedMutation === state.projectMutation ? "Saved" : "Saving…");
    if (state.projectSavedMutation !== state.projectMutation) persistProjects();
  });
  state.projectSaveChain = operation;
  try {
    await operation;
  } catch (error) {
    setSaveState("Save failed");
    setStatus(error.message || String(error), "error");
  }
}

async function loadConfig() {
  const response = await api.fetchApi("/promptstudio-video/config");
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "Video Studio configuration could not be loaded.");
  state.config = data;
}

async function loadProjects() {
  const response = await api.fetchApi(PROJECTS_ENDPOINT);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "Video projects could not be loaded.");
  state.projects = Array.isArray(data.projects) ? data.projects : [];
  state.projectRevision = Number(data.revision || 0);
  state.activeProjectId = data.active_project_id || state.projects[0]?.id || null;
  state.selectedShotId = activeProject()?.document?.shots?.[0]?.id || null;
  state.projectMutation = state.projectSavedMutation = 0;
}

function newProject() {
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
    generations: [],
    created_at: now,
    updated_at: now,
  };
  state.projects.unshift(project);
  state.activeProjectId = project.id;
  state.selectedShotId = project.document.shots[0].id;
  markProjectChanged({ render: true });
  postVideoStudioPresence();
  setStatus("New video project created.", "ready");
}

function selectProject(projectId) {
  closeShotEditor({ force: true });
  state.activeProjectId = projectId;
  state.selectedShotId = activeProject()?.document?.shots?.[0]?.id || null;
  state.projectMutation += 1;
  persistProjects();
  renderAll();
  postVideoStudioPresence();
}

function projectPendingGenerationCount(project) {
  return (project?.generations || []).filter(generation => (
    ["queued", "generating"].includes(generation.status)
  )).length;
}

function deleteProject(projectId) {
  const index = state.projects.findIndex(project => project.id === projectId);
  if (index < 0) return;
  const project = state.projects[index];
  const pendingCount = projectPendingGenerationCount(project);
  if (pendingCount || (state.directorBusy && projectId === state.activeProjectId)) return;
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
  persistProjects({ immediate: true });
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
    observed_visual_facts: "",
    subject_candidates: [],
  };
}

function resetProject() {
  const project = activeProject();
  if (!project || projectPendingGenerationCount(project) || state.directorBusy) return;
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
  for (const reference of references) state.mediaDimensionLoads.delete(reference.id);
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
    project.document.duration_seconds = Math.min(150, Math.ceil((duration + 1) * 4) / 4);
    start = duration;
  }
  const shot = {
    id: makeId("shot"), start, transition: "the camera cuts to", composition: "", subjects: "",
    environment: "", lighting: "", camera: { type: "Static Shot", amplitude: "default", speed: "default", target: "" },
    steps: [], visible_text: [], sounds: [], notes: "",
  };
  shots.push(shot);
  state.selectedShotId = shot.id;
  markProjectChanged({ render: true });
  return shot;
}

function removeSelectedShot() {
  const project = activeProject();
  const shot = selectedShot(project);
  if (!project || !shot || project.document.shots.length < 2) return;
  const index = project.document.shots.indexOf(shot);
  project.document.shots.splice(index, 1);
  project.document.shots[0].start = 0;
  state.selectedShotId = project.document.shots[Math.max(0, index - 1)].id;
  markProjectChanged({ render: true });
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

async function buildWorkflowTemplate(file, workflowData) {
  const Graph = app.rootGraph?.constructor || app.graph?.constructor;
  if (typeof Graph !== "function") throw new Error("ComfyUI's workflow graph is not ready.");
  const graph = new Graph();
  const configureErrors = graph.configure(clone(workflowData));
  if (Array.isArray(configureErrors) && configureErrors.length) {
    throw new Error(`ComfyUI could not configure ${configureErrors.length} workflow node${configureErrors.length === 1 ? "" : "s"}.`);
  }
  const snapshot = clone(await app.graphToPrompt(graph));
  const output = snapshot.output || {};
  const directors = Object.entries(output).filter(([, node]) => node?.class_type === DIRECTOR_TYPE);
  if (directors.length !== 1) {
    throw new Error(`Workflow needs exactly one executable Prompt Studio MiniMax H3 Director; found ${directors.length}.`);
  }
  const saveVideoNodes = Object.entries(output).filter(([, node]) => node?.class_type === "SaveVideo");
  if (saveVideoNodes.length !== 1) {
    throw new Error(`Workflow needs exactly one executable native Save Video node; found ${saveVideoNodes.length}.`);
  }
  return {
    id: file.path,
    path: file.path,
    name: workflowNameFromPath(file.path),
    adapter: "minimax_h3",
    director_node_id: String(directors[0][0]),
    result_node_ids: [String(saveVideoNodes[0][0])],
    result_fields: ["videos", "gifs", "images"],
    snapshot,
    source_modified: Number(file.modified || 0),
    updated_at: Date.now(),
    stale: false,
    error: "",
  };
}

async function loadWorkflowCache() {
  const response = await api.fetchApi(WORKFLOWS_ENDPOINT);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "Video workflow cache could not be loaded.");
  state.workflows = Array.isArray(data.templates) ? data.templates : [];
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

async function refreshWorkflows({ announce = true } = {}) {
  const previous = state.workflows;
  const cached = new Map(previous.map(workflow => [workflow.path, workflow]));
  const next = [];
  const issues = [];
  try {
    const response = await api.fetchApi("/userdata?dir=workflows&recurse=true&full_info=true");
    if (!response.ok) throw new Error(`ComfyUI workflows could not be listed (${response.status}).`);
    const files = (await response.json())
      .filter(file => file && typeof file.path === "string" && isVideoWorkflowPath(file.path))
      .sort((left, right) => left.path.localeCompare(right.path));
    for (const file of files) {
      const old = cached.get(file.path);
      if (old && !old.stale && Number(old.source_modified || 0) === Number(file.modified || 0)) {
        next.push(old);
        continue;
      }
      try {
        const userDataPath = `workflows/${file.path}`;
        const workflowResponse = typeof api.getUserData === "function"
          ? await api.getUserData(userDataPath)
          : await api.fetchApi(`/userdata/${encodeURIComponent(userDataPath)}`);
        if (!workflowResponse.ok) throw new Error(`ComfyUI could not read the workflow (${workflowResponse.status}).`);
        next.push(await buildWorkflowTemplate(file, await workflowResponse.json()));
      } catch (error) {
        const message = error.message || String(error);
        issues.push(`${workflowNameFromPath(file.path)}: ${message}`);
        if (old?.snapshot?.output) next.push({ ...old, stale: true, error: message });
      }
    }
    state.workflows = next;
    if (JSON.stringify(previous) !== JSON.stringify(next)) await saveWorkflowCache();
    for (const project of state.projects) {
      if (!state.workflows.some(workflow => workflow.id === project.workflow_id)) {
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

function collectHistoryOutputs(historyItem, resultNodeIds = [], resultFields = []) {
  const outputs = [];
  const selected = new Set(resultNodeIds.map(String));
  for (const [nodeId, value] of Object.entries(historyItem?.outputs || {})) {
    if (selected.size && !selected.has(String(nodeId))) continue;
    const fields = resultFields.length ? resultFields : Object.keys(value || {});
    for (const name of fields) {
      const records = value?.[name];
      if (Array.isArray(records)) {
        for (const record of records) if (record && typeof record === "object" && record.filename) outputs.push(record);
      } else if (records && typeof records === "object" && records.filename) {
        outputs.push(records);
      }
    }
  }
  return outputs;
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
  markProjectChanged();
  if (record.project.id === state.activeProjectId) renderGenerations();
}

function stopGenerationPolling(promptId) {
  const id = String(promptId || "");
  const timer = state.generationPollers.get(id);
  if (timer && timer !== true) clearTimeout(timer);
  state.generationPollers.delete(id);
  state.generationActivity.delete(id);
  state.generationProgress.delete(id);
}

function touchGeneration(promptId) {
  const id = String(promptId || "");
  if (id) state.generationActivity.set(id, Date.now());
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
  setStatus(error, "error");
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
  state.apiConnected = Boolean(connected);
  if (state.apiConnected) {
    if (state.disconnectedGenerationTimer) clearTimeout(state.disconnectedGenerationTimer);
    state.disconnectedGenerationTimer = null;
    return;
  }
  if (state.disconnectedGenerationTimer || !activeGenerationPromptIds().length) return;
  state.disconnectedGenerationTimer = setTimeout(() => {
    state.disconnectedGenerationTimer = null;
    if (state.apiConnected) return;
    const message = "Generation failed: ComfyUI disconnected while processing and did not reconnect. The prompt worker may have stopped, for example after a CUDA out-of-memory error.";
    activeGenerationPromptIds().forEach(promptId => failGeneration(promptId, message));
  }, DISCONNECTED_GENERATION_GRACE_MS);
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
    const lastActivity = state.generationActivity.get(id);
    if (
      record.generation.status === "generating"
      && lastActivity
      && Date.now() - lastActivity > GENERATION_STALL_TIMEOUT_MS
    ) {
      failGeneration(id, "Generation failed: ComfyUI stopped reporting progress for five minutes. The prompt worker may have stopped after an execution or CUDA memory error.");
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
            updateGeneration(id, {
              status: outputs.length ? "complete" : "error",
              outputs,
              error: outputs.length ? "" : "Generation completed without a saved video output.",
            });
            setStatus(outputs.length ? "Video generation completed." : "Generation completed without a saved video output.", outputs.length ? "ready" : "error");
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

async function queueSnapshot(project, workflow, snapshot, metadata) {
  const queued = await api.queuePrompt(-1, snapshot);
  const promptId = queued?.prompt_id;
  if (!promptId) throw new Error("ComfyUI did not return a prompt ID.");
  const generation = {
    id: makeId("generation"),
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
    workflow_snapshot: clone(snapshot),
    result_node_ids: clone(workflow.result_node_ids),
    result_fields: clone(workflow.result_fields),
    outputs: [],
    created_at: Date.now(),
    updated_at: Date.now(),
  };
  project.generations.unshift(generation);
  project.generations = project.generations.slice(0, 200);
  markProjectChanged({ render: true });
  await persistProjects({ immediate: true });
  state.generationProgress.set(String(promptId), { phase: "queued" });
  touchGeneration(promptId);
  const reportedFailure = state.generationFailures.get(String(promptId));
  if (reportedFailure) {
    state.generationFailures.delete(String(promptId));
    failGeneration(promptId, reportedFailure);
  } else {
    pollGeneration(promptId);
    setStatus(`Queued with ComfyUI (${String(promptId).slice(0, 8)}).`, "ready");
  }
}

async function generateProject() {
  const project = activeProject();
  if (!project) return;
  const workflow = state.workflows.find(item => item.id === project.workflow_id);
  if (!workflow) {
    setStatus("Select a compatible [PSV] workflow before generating.", "error");
    return;
  }
  const generate = state.panel.querySelector("#psvstudio-generate");
  generate.disabled = true;
  setStatus("Validating and compiling the production…");
  try {
    const response = await api.fetchApi("/promptstudio-video/document/compile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ document: project.document }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "The video document is invalid.");
    project.document = data.document;
    project.brief = project.document.main_description || "";
    const snapshot = clone(workflow.snapshot);
    const director = snapshot.output?.[workflow.director_node_id];
    if (!director) throw new Error("The selected workflow no longer contains its Director node.");
    director.inputs ||= {};
    director.inputs.document_json = JSON.stringify(data.document);
    if (state.panel.querySelector("#psvstudio-new-seed")?.checked !== false) randomizeSnapshotSeeds(snapshot);
    await queueSnapshot(project, workflow, snapshot, data);
  } catch (error) {
    setStatus(error.message || String(error), "error");
  } finally {
    generate.disabled = false;
  }
}

async function replayGeneration(generation) {
  const project = activeProject();
  const workflow = state.workflows.find(item => item.id === generation.workflow_id) || {
    id: generation.workflow_id,
    name: generation.workflow_name,
    result_node_ids: generation.result_node_ids,
    result_fields: generation.result_fields,
  };
  if (!project || !generation.workflow_snapshot) return;
  try {
    setStatus("Queueing the exact saved workflow snapshot…");
    await queueSnapshot(project, workflow, clone(generation.workflow_snapshot), {
      document: generation.document,
      compiled_prompt: generation.compiled_prompt,
      resolved_mode: generation.resolved_mode,
      frame_count: generation.frame_count,
      effective_duration: generation.effective_duration,
    });
  } catch (error) {
    setStatus(error.message || String(error), "error");
  }
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
    control.append(
      el("strong", "", project.name || "Untitled video"),
      el("small", "", `${project.document?.shots?.length || 0} shot${project.document?.shots?.length === 1 ? "" : "s"} · ${project.generations?.length || 0} render${project.generations?.length === 1 ? "" : "s"}`),
    );
    control.addEventListener("click", () => selectProject(project.id));
    const remove = el("button", "psvstudio-project-delete", "Delete");
    remove.type = "button";
    remove.disabled = pendingCount > 0 || (state.directorBusy && project.id === state.activeProjectId);
    remove.title = pendingCount
      ? "Wait for this session's queued video work to finish before deleting it."
      : state.directorBusy && project.id === state.activeProjectId
        ? "Wait for Video Director to finish before deleting this session."
        : `Delete video session ${project.name || "Untitled video"}`;
    remove.setAttribute("aria-label", remove.title);
    remove.addEventListener("click", () => deleteProject(project.id));
    row.append(control, remove);
    list.append(row);
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
  select.disabled = !project;
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
  preview.append(el("span", "psvstudio-mode-badge", localResolvedMode(project.document).toUpperCase()));
  preview.append(button("Global settings", () => showGlobalSettings(project), "psvstudio-button psvstudio-global-settings-button"));
  const output = latestOutput(project);
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
      el("strong", "", "Your production starts here"),
      el("span", "", "Describe the video, refine the selected shot, then generate an immutable workflow snapshot."),
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
  const references = project.document.references || [];
  if (!references.length) {
    section.body.append(el("small", "psvstudio-help", "Add media references to configure REF2VA source semantics here."));
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
  container.append(row);

  const production = inspectorDetails("Production settings");
  production.body.append(
    field("Visual style", textArea(project.document.style, value => { project.document.style = value; markProjectChanged(); }, 2, PLACEHOLDERS.style)),
    field("Overall soundscape", textArea(project.document.overall_soundscape, value => { project.document.overall_soundscape = value; markProjectChanged(); }, 3, PLACEHOLDERS.soundscape)),
    field("Non-diegetic music", textArea(project.document.non_diegetic_music, value => { project.document.non_diegetic_music = value; markProjectChanged(); }, 2, PLACEHOLDERS.music)),
    checkControl("Complete silence", project.document.complete_silence, value => { project.document.complete_silence = value; markProjectChanged(); }),
    field("Mode override", selectInput(state.config.modes.map(value => ({ value, label: value === "auto" ? "Automatic (recommended)" : value.toUpperCase() })), project.document.mode, value => {
      project.document.mode = value;
      markProjectChanged({ render: true });
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
  section.body.append(field("Video overview", brief, "Planning synopsis for you and the Grand Director. It is not sent to MiniMax; the Director translates it into the required shot timeline."));

  const command = textArea("", () => {}, 2, PLACEHOLDERS.directorCommand);
  const ask = button(
    "Ask Grand Director",
    () => openDirector("project", command.value.trim()),
    "psvstudio-button psvstudio-button-primary psvstudio-director-launch-button",
  );
  ask.title = "Consult about the entire video and review any proposed multi-shot changes before applying them.";
  const directorActions = el("div", "psvstudio-director-launch");
  directorActions.append(
    field("Grand Director instruction", command),
    ask,
    el("small", "psvstudio-help", `Full-video scope · ${project.document.shots.length} shot${project.document.shots.length === 1 ? "" : "s"} in context`),
  );
  section.body.append(directorActions);
  container.append(section.details);
}

function showGlobalSettings(project = activeProject()) {
  if (!project) return;
  const dialog = el("dialog", "psvstudio-global-settings-dialog");
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
    : 150;
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
  const director = state.panel?.querySelector("#psvstudio-grand-director");
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
  header.append(heading, button("×", () => closeShotEditor(), "psvstudio-button psvstudio-icon-button"));

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
      draft.sounds = value.split(/\r?\n/).map(item => item.trim()).filter(Boolean);
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
  sequence.append(sequenceHeader, stepList);
  workspace.append(setup, sequence);

  const footer = el("footer", "psvstudio-shot-editor-footer");
  const left = el("div", "psvstudio-inline");
  const remove = button("Remove shot", removeShotFromEditor, "psvstudio-button psvstudio-button-danger");
  remove.disabled = project.document.shots.length < 2;
  left.append(remove, button("Save & ask Director", () => saveShotEditor({ askDirector: true })));
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
  state.shotEditorExpandedStepIds = new Set();
  state.shotEditorRevealStepId = "";
  const dialog = el("dialog", "psvstudio-shot-editor-dialog");
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
      const message = generation.status === "error" ? "Generation failed" : generation.status === "queued" ? "Waiting in queue…" : "Generating video…";
      if (media.childElementCount !== 1 || media.firstElementChild?.tagName !== "SPAN" || media.firstElementChild.textContent !== message) {
        media.replaceChildren(el("span", "", message));
      }
    }
    const body = el("div", "psvstudio-generation-body");
    const head = el("div", "psvstudio-generation-head");
    head.append(
      el("strong", "", generation.workflow_name || "Video workflow"),
      el("span", "psvstudio-status-chip", generation.status),
    );
    head.lastElementChild.dataset.status = generation.status;
    body.append(head, el("small", "psvstudio-help", `${String(generation.resolved_mode || "").toUpperCase()} · ${Number(generation.effective_duration || 0).toFixed(2)}s · ${generation.frame_count || 0} frames`));
    if (["queued", "generating"].includes(generation.status)) {
      const progress = state.generationProgress.get(String(generation.prompt_id)) || {};
      const percent = Number.isFinite(progress.value) && Number.isFinite(progress.max) && progress.max > 0
        ? Math.max(2, Math.min(100, progress.value / progress.max * 100))
        : 12;
      const bar = el("div", "psvstudio-progress");
      const fill = el("span");
      fill.style.setProperty("--progress", `${percent}%`);
      bar.append(fill);
      body.append(bar);
    }
    if (generation.error) body.append(el("div", "psvstudio-help", generation.error));
    const actions = el("div", "psvstudio-inline");
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
    actions.append(button("Replay exact", () => replayGeneration(generation)));
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
  const project = activeProject();
  if (!project) return;
  try {
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
  if (title) {
    title.disabled = !project;
    title.value = project?.name || "Prompt Studio Video";
  }
  if (generate) generate.disabled = !project || !state.workflows.length;
  if (duplicate) duplicate.disabled = !project;
  if (reset) reset.disabled = !project || Boolean(projectPendingGenerationCount(project)) || state.directorBusy;
  if (preview) preview.disabled = !project;
  if (preview) {
    const overridden = Boolean(project?.document?.prompt_override);
    preview.textContent = overridden ? "Prompt*" : "Prompt";
    preview.title = overridden
      ? "A manual prompt override is active and may not match the Shot Composer settings"
      : "View or edit the compiled MiniMax prompt";
    preview.dataset.override = overridden ? "true" : "false";
  }
  renderWorkflowSelect();
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
  const panel = el("section", "psvstudio-app");
  panel.hidden = true;
  panel.innerHTML = `
    <aside class="psvstudio-sidebar">
      <div class="psvstudio-brand">
        <img class="psvstudio-brand-mark" src="./prompt-studio-video-icon.svg" alt="" aria-hidden="true">
        <div><strong>Prompt Studio Video</strong><small>MiniMax H3 Director</small></div>
      </div>
      <div class="psvstudio-sidebar-header">
        <h2>Projects</h2>
        <button id="psvstudio-new-project" class="psvstudio-button psvstudio-button-primary" type="button">New</button>
      </div>
      <div id="psvstudio-project-list" class="psvstudio-project-list"></div>
    </aside>
    <main class="psvstudio-main">
      <header class="psvstudio-topbar">
        <button id="psvstudio-mobile-projects" class="psvstudio-button psvstudio-icon-button" type="button" title="Projects" aria-label="Projects">☰</button>
        <input id="psvstudio-project-title" class="psvstudio-title-field" aria-label="Project name" placeholder="${PLACEHOLDERS.projectTitle}" />
        <span id="psvstudio-save-state" class="psvstudio-save-state">Saved</span>
        <span class="psvstudio-topbar-spacer"></span>
        <select id="psvstudio-workflow" class="psvstudio-workflow-select" aria-label="Video workflow"></select>
        <button id="psvstudio-refresh-workflows" class="psvstudio-button psvstudio-icon-button" type="button" title="Refresh [PSV] workflows" aria-label="Refresh [PSV] workflows">↻</button>
        <details id="psvstudio-kobold-control" class="psvstudio-kobold-control" data-state="checking">
          <summary title="KoboldCpp status and emergency stop"><span class="psvstudio-kobold-dot" aria-hidden="true"></span><span id="psvstudio-kobold-status-label">Kobold…</span></summary>
          <div class="psvstudio-kobold-popover">
            <strong>KoboldCpp</strong>
            <span id="psvstudio-kobold-status-detail" role="status" aria-live="polite">Checking local status…</span>
            <button id="psvstudio-kobold-stop" class="psvstudio-button psvstudio-button-danger" type="button" disabled>Stop generation</button>
            <small>Stops text generation only. KoboldCpp stays loaded.</small>
          </div>
        </details>
        <label class="psvstudio-inline psvstudio-help"><input id="psvstudio-new-seed" type="checkbox" checked /> New seed</label>
        <button id="psvstudio-compile-preview" class="psvstudio-button" type="button">Prompt</button>
        <button id="psvstudio-duplicate" class="psvstudio-button" type="button">Duplicate</button>
        <button id="psvstudio-reset" class="psvstudio-button psvstudio-button-danger" type="button" title="Reset this project while retaining its reference media">Reset</button>
        <button id="psvstudio-mobile-inspector" class="psvstudio-button psvstudio-icon-button" type="button" title="Shots" aria-label="Shots">☷</button>
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
              <button id="psvstudio-grand-director" class="psvstudio-button psvstudio-button-primary psvstudio-timeline-director-button" type="button" title="Consult the Grand Director about the entire video">✦ Grand Director</button>
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
          <input id="psvstudio-media-input" class="psvstudio-sr-only" type="file" accept="image/*,video/*,audio/*" multiple />
        </section>
      </div>
      <footer class="psvstudio-action-footer">
        <div id="psvstudio-status" class="psvstudio-status" role="status" aria-live="polite">Loading Video Studio…</div>
        <button id="psvstudio-generate" class="psvstudio-button psvstudio-button-primary" type="button">Generate</button>
      </footer>
    </main>
    <aside class="psvstudio-inspector">
      <div class="psvstudio-inspector-header"><h2 id="psvstudio-inspector-title">Shots</h2><button id="psvstudio-close-inspector" class="psvstudio-button psvstudio-icon-button" type="button" aria-label="Close shots">×</button></div>
      <div id="psvstudio-inspector-content" class="psvstudio-inspector-content"></div>
    </aside>`;

  panel.querySelector("#psvstudio-new-project").addEventListener("click", newProject);
  panel.querySelector("#psvstudio-add-shot").addEventListener("click", addShot);
  panel.querySelector("#psvstudio-grand-director").addEventListener("click", () => openDirector("project"));
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
  panel.querySelector("#psvstudio-duplicate").addEventListener("click", duplicateProject);
  panel.querySelector("#psvstudio-reset").addEventListener("click", resetProject);
  panel.querySelector("#psvstudio-compile-preview").addEventListener("click", compilePreview);
  panel.querySelector("#psvstudio-refresh-workflows").addEventListener("click", () => refreshWorkflows());
  panel.querySelector("#psvstudio-kobold-stop").addEventListener("click", stopKoboldGeneration);
  panel.querySelector("#psvstudio-workflow").addEventListener("change", event => {
    const project = activeProject();
    if (!project) return;
    project.workflow_id = event.target.value;
    markProjectChanged();
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
  });
  panel.querySelector("#psvstudio-mobile-inspector").addEventListener("click", () => {
    state.drawer = state.drawer === "inspector" ? "" : "inspector";
    panel.dataset.drawer = state.drawer;
  });
  panel.querySelector("#psvstudio-close-inspector").addEventListener("click", () => {
    state.drawer = "";
    panel.dataset.drawer = "";
  });
  state.panel = panel;
  document.body.append(panel);
  installMediaDrop(document);
  return panel;
}

function setupProgressEvents() {
  const promptId = event => String(event?.detail?.prompt_id || event?.detail?.promptId || "");
  api.addEventListener("execution_start", event => {
    const id = promptId(event);
    if (!generationByPromptId(id)) return;
    touchGeneration(id);
    updateGeneration(id, { status: "generating" });
    state.generationProgress.set(id, { phase: "generating" });
  });
  api.addEventListener("progress", event => {
    const id = promptId(event);
    if (!generationByPromptId(id)) return;
    touchGeneration(id);
    state.generationProgress.set(id, {
      phase: "generating",
      value: Number(event.detail?.value),
      max: Number(event.detail?.max),
    });
    renderGenerations();
  });
  for (const eventName of ["execution_error", "execution_interrupted"]) {
    api.addEventListener(eventName, event => {
      const id = promptId(event);
      failGeneration(id, executionFailureMessage(eventName, event?.detail));
    });
  }
  api.addEventListener("executing", event => {
    const id = promptId(event);
    if (generationByPromptId(id)) touchGeneration(id);
  });
  api.addEventListener("progress_state", event => {
    const id = promptId(event);
    if (generationByPromptId(id)) touchGeneration(id);
  });
  api.addEventListener("reconnecting", () => setApiConnected(false));
  api.addEventListener("reconnected", () => setApiConnected(true));
  api.addEventListener("status", event => setApiConnected(event.detail !== null));
  setApiConnected(api.socket ? api.socket.readyState === WebSocket.OPEN : true);
}

async function attachStandalone(popup) {
  if (!popup || popup.closed) return false;
  try {
    if (popup.location.origin !== window.location.origin) return false;
  } catch (_) {
    return false;
  }
  const mount = popup.document.querySelector("#promptstudio-video-mount");
  if (!mount || !state.panel) return false;
  state.popup = popup;
  mount.replaceChildren(state.panel);
  state.panel.hidden = false;
  state.studioOpenedAt = Date.now();
  installMediaDrop(popup.document);
  renderAll();
  postVideoStudioPresence();
  popup.addEventListener("beforeunload", () => {
    if (state.panel?.ownerDocument !== document) document.body.append(state.panel);
    state.panel.hidden = true;
    state.studioOpenedAt = 0;
    clearMediaDrag(popup.document);
    state.popup = null;
    postVideoStudioPresence();
    persistProjects({ immediate: true });
  }, { once: true });
  return true;
}

function setupStandaloneBridge() {
  globalThis.__promptstudioVideoStudioHost = {
    attach: attachStandalone,
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
  if (state.bridgePresenceTimer) window.clearInterval(state.bridgePresenceTimer);
  state.bridgePresenceTimer = window.setInterval(postVideoStudioPresence, 2500);
  postVideoStudioPresence();
}

function resumeGenerationPolling() {
  for (const project of state.projects) {
    for (const generation of project.generations || []) {
      if (["queued", "generating"].includes(generation.status) && generation.prompt_id) pollGeneration(generation.prompt_id);
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
      resumeGenerationPolling();
    } catch (error) {
      setStatus(error.message || String(error), "error");
    }
    setupStandaloneBridge();
  },
});
