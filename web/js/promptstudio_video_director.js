import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";

const NODE_TYPE = "PSV_MiniMaxH3Director";
const MODES = ["auto", "t2va", "i2va", "fl2va", "l2va", "ref2va"];
const CAMERA_TYPES = ["", "Zoom In", "Zoom Out", "Push In", "Pull Out", "Pan Left", "Pan Right", "Truck Left", "Truck Right", "Tilt Up", "Tilt Down", "Pedestal Up", "Pedestal Down", "Arc Shot", "Tracking Shot", "Static Shot", "Shake Slightly", "Shake Strongly", "POV", "Roll Clockwise", "Roll Counterclockwise"];
const ROLE_LABELS = {
  first_frame: "First frame",
  last_frame: "Last frame",
  subject: "Subject",
  scene: "Scene",
  style: "Style",
  action: "Action",
  pose: "Pose",
  camera: "Camera",
  storyboard: "Storyboard",
  video_edit: "Edit source",
  video_continue: "Continuation",
  audio_copy: "Copy audio",
  audio_reference: "Audio reference",
};
let stylesLoaded = false;

function loadStyles() {
  if (stylesLoaded) return;
  stylesLoaded = true;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = new URL("../css/promptstudio_video_director.css", import.meta.url).href;
  document.head.append(link);
}

function identifier(prefix) {
  return `${prefix}-${crypto.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`}`;
}

function defaultShot(index = 0) {
  return {
    id: identifier("shot"), start: index ? index * 2 : 0, transition: "the camera cuts to",
    composition: index ? "" : "A medium-wide shot establishes the scene.", subjects: "", environment: "",
    lighting: "", action: "", camera: { type: "Static Shot", amplitude: "default", speed: "default", target: "" },
    dialogue: [], visible_text: [], sounds: [], notes: "",
  };
}

function defaultDocument() {
  return { version: 1, mode: "auto", duration_seconds: 5, width: 1344, height: 768, ref_image_size: "match", style: "Live-action, cinematic", shots: [defaultShot()], references: [], overall_soundscape: "", non_diegetic_music: "N/A", complete_silence: false, task_types: [], subject_definitions: [], summary: "", retention_analysis: [] };
}

function parseDocument(value) {
  try {
    const parsed = JSON.parse(value || "{}");
    const base = defaultDocument();
    return { ...base, ...parsed, shots: Array.isArray(parsed.shots) && parsed.shots.length ? parsed.shots : base.shots, references: Array.isArray(parsed.references) ? parsed.references : [] };
  } catch {
    return defaultDocument();
  }
}

function resolveMode(document) {
  if (document.mode && document.mode !== "auto") return document.mode;
  const refs = document.references || [];
  const roles = new Set(refs.flatMap(ref => ref.roles || []));
  if (refs.some(ref => ref.kind === "video" || ref.kind === "audio")) return "ref2va";
  if ([...roles].some(role => !["first_frame", "last_frame"].includes(role))) return "ref2va";
  const first = roles.has("first_frame");
  const last = roles.has("last_frame");
  return first && last ? "fl2va" : first ? "i2va" : last ? "l2va" : "t2va";
}

function el(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function button(text, handler, className = "") {
  const result = el("button", className, text);
  result.type = "button";
  result.addEventListener("click", handler);
  return result;
}

function field(label, control) {
  const wrapper = el("label", "psv-field");
  wrapper.append(el("span", "", label), control);
  return wrapper;
}

function input(value, onInput, type = "text") {
  const control = document.createElement("input");
  control.type = type;
  control.value = value ?? "";
  control.addEventListener("input", () => onInput(type === "number" ? control.valueAsNumber : control.value));
  return control;
}

function textarea(value, onInput) {
  const control = document.createElement("textarea");
  control.value = value || "";
  control.addEventListener("input", () => onInput(control.value));
  return control;
}

function select(options, value, onChange) {
  const control = document.createElement("select");
  options.forEach(optionValue => {
    const option = document.createElement("option");
    option.value = optionValue;
    option.textContent = optionValue || "None";
    control.append(option);
  });
  control.value = value;
  control.addEventListener("change", () => onChange(control.value));
  return control;
}

async function uploadFile(file, status) {
  const form = new FormData();
  form.append("image", file, file.name);
  form.append("type", "input");
  form.append("overwrite", "false");
  status.textContent = `Uploading ${file.name}…`;
  const response = await api.fetchApi("/upload/image", { method: "POST", body: form });
  if (!response.ok) throw new Error(`Upload failed (${response.status})`);
  const result = await response.json();
  return result.subfolder ? `${result.subfolder}/${result.name}` : result.name;
}

function mediaKind(file) {
  if (file.type.startsWith("image/")) return "image";
  if (file.type.startsWith("video/")) return "video";
  if (file.type.startsWith("audio/")) return "audio";
  return "";
}

function install(node) {
  if (node.__psvInstalled) return;
  node.__psvInstalled = true;
  loadStyles();
  const dataWidget = node.widgets?.find(widget => widget.name === "document_json");
  if (!dataWidget) return;
  dataWidget.hidden = true;
  dataWidget.draw = () => {};
  dataWidget.computeSize = () => [0, -4];

  const summary = el("div", "psv-director-summary");
  const summaryRow = el("div", "psv-director-summary-row");
  const summaryTitle = el("strong", "", "MiniMax H3 Director");
  const modeBadge = el("span", "psv-mode-badge");
  summaryRow.append(summaryTitle, modeBadge);
  const detail = el("div", "psv-help");
  const open = button("Open Director Canvas", () => openEditor());
  summary.append(summaryRow, detail, open);
  let domWidget;

  const updateSummary = () => {
    const current = parseDocument(dataWidget.value);
    modeBadge.textContent = resolveMode(current).toUpperCase();
    detail.textContent = `${current.shots.length} shot${current.shots.length === 1 ? "" : "s"} · ${current.references.length} reference${current.references.length === 1 ? "" : "s"} · ${Number(current.duration_seconds || 5).toFixed(2)}s requested`;
  };

  function openEditor() {
    let draft = structuredClone(parseDocument(dataWidget.value));
    let selectedShotId = draft.shots[0]?.id;
    let selectedReferenceId = null;
    const dialog = el("dialog", "psv-dialog");
    const shell = el("div", "psv-shell");
    const header = el("header", "psv-header");
    const title = el("h2", "", "Prompt Studio Video Director");
    const status = el("span", "psv-status");
    const body = el("div", "psv-body");
    const referencesPane = el("aside", "psv-pane");
    const shotsPane = el("main", "psv-pane");
    const inspectorPane = el("aside", "psv-pane psv-inspector");
    const footer = el("footer", "psv-footer");

    const modeControl = select(MODES, draft.mode || "auto", value => { draft.mode = value; render(); });
    const durationControl = input(draft.duration_seconds, value => { if (Number.isFinite(value) && value > 0) draft.duration_seconds = value; renderTimeline(); }, "number");
    durationControl.min = "0.25"; durationControl.max = "150"; durationControl.step = "0.25";
    const widthControl = input(draft.width, value => { if (value > 0) draft.width = Math.round(value / 32) * 32; }, "number");
    const heightControl = input(draft.height, value => { if (value > 0) draft.height = Math.round(value / 32) * 32; }, "number");
    [widthControl, heightControl].forEach(control => { control.min = "32"; control.step = "32"; });
    header.append(title, field("Mode", modeControl), field("Duration", durationControl), field("Width", widthControl), field("Height", heightControl));

    const fileInput = document.createElement("input");
    fileInput.type = "file"; fileInput.multiple = true; fileInput.accept = "image/*,video/*,audio/*"; fileInput.hidden = true;
    fileInput.addEventListener("change", async () => {
      for (const file of fileInput.files || []) {
        const kind = mediaKind(file);
        if (!kind) continue;
        try {
          const path = await uploadFile(file, status);
          draft.references.push({ id: identifier("reference"), kind, path, name: file.name, roles: kind === "image" && !draft.references.some(ref => (ref.roles || []).includes("first_frame")) ? ["first_frame"] : [kind === "audio" ? "audio_reference" : "subject"], prompt: "", trim_start: 0, trim_end: null, use_embedded_audio: false });
          selectedReferenceId = draft.references.at(-1).id;
          status.textContent = `${file.name} added.`;
          render();
        } catch (error) { status.textContent = error.message; }
      }
      fileInput.value = "";
    });

    function renderReferences() {
      referencesPane.replaceChildren();
      referencesPane.append(el("h3", "", "References"), button("Add media", () => fileInput.click()));
      const stack = el("div", "psv-stack");
      if (!draft.references.length) stack.append(el("div", "psv-empty", "No references. Auto mode resolves to T2VA."));
      draft.references.forEach(reference => {
        const card = el("div", `psv-reference${reference.id === selectedReferenceId ? " selected" : ""}`);
        const head = el("div", "psv-reference-head");
        head.append(el("strong", "", reference.name || reference.path), el("span", "", reference.kind));
        card.append(head, el("div", "psv-help", (reference.roles || []).map(role => ROLE_LABELS[role] || role).join(" · ") || "No role"));
        card.addEventListener("click", () => { selectedReferenceId = reference.id; renderInspector(); renderReferences(); });
        stack.append(card);
      });
      referencesPane.append(stack, fileInput);
    }

    function renderTimeline() {
      const old = shotsPane.querySelector(".psv-timeline");
      if (old) old.remove();
      const timeline = el("div", "psv-timeline");
      const ruler = el("div", "psv-ruler");
      const duration = Math.max(1, Number(draft.duration_seconds) || 5);
      const scale = Math.max(45, 680 / duration);
      for (let second = 0; second <= Math.ceil(duration); second += 1) {
        const tick = el("span", "", `${second}s`); tick.style.left = `${second * scale}px`; ruler.append(tick);
      }
      const track = el("div", "psv-track"); track.style.width = `${Math.max(520, duration * scale)}px`;
      draft.shots.forEach((shot, index) => {
        const end = draft.shots[index + 1]?.start ?? duration;
        const block = el("div", `psv-shot-block${shot.id === selectedShotId ? " selected" : ""}`);
        block.style.left = `${Math.max(0, shot.start) * scale}px`;
        block.style.width = `${Math.max(70, (end - shot.start) * scale - 4)}px`;
        block.append(el("strong", "", `Shot ${index + 1}`), el("small", "", `${Number(shot.start).toFixed(2)}–${Number(end).toFixed(2)}s`), el("small", "", shot.camera?.type || "No camera motion"));
        block.addEventListener("click", () => { selectedShotId = shot.id; selectedReferenceId = null; render(); });
        track.append(block);
      });
      timeline.append(ruler, track);
      shotsPane.prepend(timeline);
    }

    function renderShots() {
      shotsPane.replaceChildren();
      const heading = el("div", "psv-row");
      heading.append(el("h3", "", "Shots"), button("Add shot", () => {
        const last = draft.shots.at(-1);
        const start = Math.min((Number(last?.start) || 0) + 2, Math.max(0.25, Number(draft.duration_seconds) - 0.25));
        const shot = { ...defaultShot(draft.shots.length), start };
        draft.shots.push(shot); selectedShotId = shot.id; render();
      }));
      shotsPane.append(heading);
      renderTimeline();
      const list = el("div", "psv-stack");
      draft.shots.forEach((shot, index) => {
        const card = el("div", `psv-shot-card${shot.id === selectedShotId ? " selected" : ""}`);
        const head = el("div", "psv-shot-head");
        head.append(el("strong", "", `Shot ${index + 1}`), el("span", "psv-help", index ? `Cut ${Number(shot.start).toFixed(3)}s` : "Opening"));
        card.append(head, el("div", "psv-help", shot.composition || shot.action || "Empty shot"));
        card.addEventListener("click", () => { selectedShotId = shot.id; selectedReferenceId = null; render(); });
        list.append(card);
      });
      shotsPane.append(list);
    }

    function renderReferenceInspector(reference) {
      inspectorPane.append(el("h3", "", "Reference roles"), el("div", "psv-help", reference.name || reference.path));
      const available = reference.kind === "image" ? ["first_frame", "last_frame", "subject", "scene", "style", "action", "pose", "camera", "storyboard"] : reference.kind === "video" ? ["subject", "scene", "style", "action", "pose", "camera", "video_edit", "video_continue"] : ["audio_copy", "audio_reference"];
      const grid = el("div", "psv-role-grid");
      available.forEach(role => {
        const check = document.createElement("input"); check.type = "checkbox"; check.checked = (reference.roles || []).includes(role);
        check.addEventListener("change", () => {
          reference.roles ||= [];
          if (check.checked) {
            if (["first_frame", "last_frame"].includes(role)) draft.references.forEach(ref => { if (ref !== reference) ref.roles = (ref.roles || []).filter(item => item !== role); });
            if (!reference.roles.includes(role)) reference.roles.push(role);
          } else reference.roles = reference.roles.filter(item => item !== role);
          render();
        });
        const label = document.createElement("label"); label.append(check, ROLE_LABELS[role]); grid.append(label);
      });
      inspectorPane.append(grid, field("Reference guidance", textarea(reference.prompt, value => { reference.prompt = value; })));
      if (reference.kind !== "image") {
        const trim = el("div", "psv-row");
        trim.append(field("Trim start", input(reference.trim_start || 0, value => { reference.trim_start = value; }, "number")), field("Trim end", input(reference.trim_end ?? "", value => { reference.trim_end = Number.isFinite(value) ? value : null; }, "number")));
        inspectorPane.append(trim);
      }
      inspectorPane.append(button("Remove reference", () => { draft.references = draft.references.filter(ref => ref.id !== reference.id); selectedReferenceId = null; render(); }, "psv-danger"));
    }

    function renderDialogue(shot) {
      inspectorPane.append(el("h4", "", "Dialogue and voice"));
      const list = el("div", "psv-stack");
      (shot.dialogue || []).forEach((event, index) => {
        const card = el("div", "psv-dialogue");
        const row = el("div", "psv-row");
        row.append(field("Speaker", input(event.speaker || "", value => { event.speaker = value; })), field("ID", input(event.speaker_id || `S${index + 1}`, value => { event.speaker_id = value.toUpperCase(); })));
        const language = input(event.language || "English", value => { event.language = value; });
        const exact = textarea(event.text || "", value => { event.text = value; });
        const flags = el("div", "psv-role-grid");
        [["voiceover", "Voiceover"], ["offscreen", "Off-screen"], ["crosses_cut", "Crosses cut"], ["cutoff", "Cut off"]].forEach(([name, labelText]) => {
          const check = document.createElement("input"); check.type = "checkbox"; check.checked = Boolean(event[name]); check.addEventListener("change", () => { event[name] = check.checked; });
          const label = document.createElement("label"); label.append(check, labelText); flags.append(label);
        });
        const actions = el("div", "psv-dialogue-actions"); actions.append(button("Remove", () => { shot.dialogue.splice(index, 1); renderInspector(); }, "psv-danger"));
        card.append(row, field("Language", language), field("Exact dialogue (never rewritten)", exact), field("Delivery outside the dialogue tag", input(event.delivery || "", value => { event.delivery = value; })), flags, actions);
        list.append(card);
      });
      list.append(button("Add dialogue", () => { shot.dialogue ||= []; shot.dialogue.push({ id: identifier("dialogue"), speaker: "The speaker", speaker_id: `S${shot.dialogue.length + 1}`, language: "English", text: "", delivery: "", voiceover: false, offscreen: false, crosses_cut: false, cutoff: false }); renderInspector(); }));
      inspectorPane.append(list);
    }

    function renderShotInspector(shot) {
      const index = draft.shots.indexOf(shot);
      inspectorPane.append(el("h3", "", `Shot ${index + 1}`));
      if (index) {
        const start = input(shot.start, value => { if (Number.isFinite(value)) shot.start = value; renderTimeline(); }, "number"); start.min = "0.001"; start.step = "0.001";
        inspectorPane.append(field("Cut time", start), field("Transition", input(shot.transition || "the camera cuts to", value => { shot.transition = value; })));
      }
      [["Composition and framing", "composition"], ["Subjects and positions", "subjects"], ["Environment", "environment"], ["Lighting", "lighting"], ["Actions and state changes", "action"]].forEach(([label, name]) => inspectorPane.append(field(label, textarea(shot[name], value => { shot[name] = value; }))));
      inspectorPane.append(el("h4", "", "Camera movement"));
      const camera = shot.camera ||= { type: "", amplitude: "default", speed: "default", target: "" };
      inspectorPane.append(field("Motion", select(CAMERA_TYPES, camera.type || "", value => { camera.type = value; renderTimeline(); })), field("Amplitude", select(["default", "small", "large"], camera.amplitude || "default", value => { camera.amplitude = value; })), field("Speed", select(["default", "slow", "fast"], camera.speed || "default", value => { camera.speed = value; })), field("Target or reveal", input(camera.target || "", value => { camera.target = value; })));
      renderDialogue(shot);
      inspectorPane.append(field("Visible text (one exact item per line)", textarea((shot.visible_text || []).join("\n"), value => { shot.visible_text = value.split(/\r?\n/).map(item => item.trim()).filter(Boolean); })), field("Synchronized sounds (one per line)", textarea((shot.sounds || []).join("\n"), value => { shot.sounds = value.split(/\r?\n/).map(item => item.trim()).filter(Boolean); })));
      if (draft.shots.length > 1) inspectorPane.append(button("Remove shot", () => { draft.shots = draft.shots.filter(item => item.id !== shot.id); selectedShotId = draft.shots[Math.max(0, index - 1)].id; render(); }, "psv-danger"));
    }

    function renderInspector() {
      inspectorPane.replaceChildren();
      const reference = draft.references.find(item => item.id === selectedReferenceId);
      if (reference) { renderReferenceInspector(reference); return; }
      const shot = draft.shots.find(item => item.id === selectedShotId) || draft.shots[0];
      renderShotInspector(shot);
      inspectorPane.append(el("h4", "", "Whole-video sound"), field("Overall soundscape", textarea(draft.overall_soundscape, value => { draft.overall_soundscape = value; })), field("Non-diegetic music", textarea(draft.non_diegetic_music, value => { draft.non_diegetic_music = value; })));
    }

    function render() {
      modeControl.value = draft.mode || "auto";
      status.textContent = `Resolved mode: ${resolveMode(draft).toUpperCase()}`;
      renderReferences(); renderShots(); renderInspector();
    }

    footer.append(status, button("Cancel", () => dialog.close()), button("Apply", () => {
      dataWidget.value = JSON.stringify(draft);
      dataWidget.callback?.(dataWidget.value);
      node.graph?.setDirtyCanvas(true, true);
      updateSummary();
      dialog.close();
    }, "psv-primary"));
    shell.append(header, body, footer); body.append(referencesPane, shotsPane, inspectorPane); dialog.append(shell);
    document.body.append(dialog);
    dialog.addEventListener("close", () => dialog.remove(), { once: true });
    render(); dialog.showModal();
  }

  if (node.addDOMWidget) {
    domWidget = node.addDOMWidget("promptstudio_video_director", "custom", summary, { serialize: false, hideOnZoom: false, getHeight: () => 150 });
    domWidget.computeSize = width => [Math.max(420, width), 150];
  }
  const oldConfigure = node.onConfigure;
  node.onConfigure = function (...args) { oldConfigure?.apply(this, args); requestAnimationFrame(updateSummary); };
  node.setSize?.([Math.max(480, node.size?.[0] || 480), Math.max(260, node.size?.[1] || 260)]);
  updateSummary();
}

app.registerExtension({
  name: "PromptStudio.Video.Director",
  nodeCreated(node) { if (node.comfyClass === NODE_TYPE) install(node); },
  loadedGraphNode(node) { if (node.comfyClass === NODE_TYPE) install(node); },
});
