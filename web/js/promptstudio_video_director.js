import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";

const NODE_TYPE = "PSV_MiniMaxH3Director";
const MODES = ["auto", "t2va", "i2va", "fl2va", "l2va", "ref2va"];
const FPS = 24;
const MIN_SHOT_SECONDS = 0.25;
const MAX_DURATION_SECONDS = 150;
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
  const moduleUrl = new URL(import.meta.url);
  const stylesheetUrl = new URL("../css/promptstudio_video_director.css", moduleUrl);
  stylesheetUrl.search = moduleUrl.search;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = stylesheetUrl.href;
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
  return { version: 1, mode: "auto", duration_seconds: 5, width: 1344, height: 768, ref_image_size: "match", main_description: "", style: "Live-action, cinematic", shots: [defaultShot()], references: [], overall_soundscape: "", non_diegetic_music: "N/A", complete_silence: false, task_types: [], subject_definitions: [], summary: "", retention_analysis: [] };
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
  const subtype = String(file?.type || "").split("/")[1]?.split(/[;+]/)[0];
  const extension = subtype === "jpeg" ? "jpg" : subtype || "bin";
  const filename = file?.name || `pasted-media-${identifier("upload")}.${extension}`;
  const form = new FormData();
  form.append("image", file, filename);
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
  const extension = file.name.split(".").pop()?.toLowerCase();
  if (["png", "jpg", "jpeg", "webp", "gif", "bmp", "tif", "tiff"].includes(extension)) return "image";
  if (["mp4", "webm", "mov", "mkv", "avi", "m4v"].includes(extension)) return "video";
  if (["wav", "mp3", "flac", "ogg", "m4a", "aac"].includes(extension)) return "audio";
  return "";
}

function referenceDisplayLabel(references, reference) {
  const typeName = reference.kind === "image" ? "Picture" : reference.kind === "video" ? "Video" : "Audio";
  const ordinal = references.filter(item => item.kind === reference.kind).findIndex(item => item.id === reference.id) + 1;
  return `${typeName} ${Math.max(1, ordinal)}`;
}

function isFileDrag(event) {
  return Array.from(event.dataTransfer?.types || []).includes("Files");
}

function clipboardImageFiles(event) {
  const itemFiles = Array.from(event.clipboardData?.items || [])
    .filter(item => item.kind === "file" && item.type?.startsWith("image/"))
    .map(item => item.getAsFile())
    .filter(Boolean);
  if (itemFiles.length) return itemFiles;
  return Array.from(event.clipboardData?.files || []).filter(file => mediaKind(file) === "image");
}

function roundTime(value) {
  return Math.round(value * 1000) / 1000;
}

function snapToFrame(value) {
  return roundTime(Math.round(value * FPS) / FPS);
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
    let shotDrag = null;
    let mediaDragDepth = 0;
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
    const durationControl = input(draft.duration_seconds, value => {
      if (!Number.isFinite(value) || value <= 0) return;
      const minimum = (Number(draft.shots.at(-1)?.start) || 0) + MIN_SHOT_SECONDS;
      draft.duration_seconds = roundTime(Math.min(MAX_DURATION_SECONDS, Math.max(minimum, value)));
      durationControl.value = draft.duration_seconds;
      renderTimeline();
    }, "number");
    durationControl.min = String(MIN_SHOT_SECONDS); durationControl.max = String(MAX_DURATION_SECONDS); durationControl.step = "0.25";
    const widthControl = input(draft.width, value => { if (value > 0) draft.width = Math.round(value / 32) * 32; }, "number");
    const heightControl = input(draft.height, value => { if (value > 0) draft.height = Math.round(value / 32) * 32; }, "number");
    [widthControl, heightControl].forEach(control => { control.min = "32"; control.step = "32"; });
    header.append(title, field("Mode", modeControl), field("Duration", durationControl), field("Width", widthControl), field("Height", heightControl));

    const fileInput = document.createElement("input");
    fileInput.type = "file"; fileInput.multiple = true; fileInput.accept = "image/*,video/*,audio/*"; fileInput.hidden = true;
    async function addMediaFiles(fileList) {
      const files = Array.from(fileList || []);
      const supported = files.filter(mediaKind);
      if (!supported.length) {
        status.textContent = files.length ? "Drop images, video, or audio files." : "No media files were dropped.";
        return;
      }
      let added = 0;
      const errors = [];
      for (const file of supported) {
        const kind = mediaKind(file);
        try {
          const path = await uploadFile(file, status);
          const firstFrameTaken = draft.references.some(ref => (ref.roles || []).includes("first_frame"));
          const roles = kind === "image" && !firstFrameTaken
            ? ["first_frame"]
            : [kind === "audio" ? "audio_reference" : "subject"];
          draft.references.push({ id: identifier("reference"), kind, path, name: file.name || path.split("/").pop(), roles, prompt: "", trim_start: 0, trim_end: null, use_embedded_audio: false });
          selectedReferenceId = draft.references.at(-1).id;
          added += 1;
        } catch (error) {
          errors.push(`${file.name}: ${error.message}`);
        }
      }
      if (added) render();
      status.textContent = errors.length
        ? `${added} media file${added === 1 ? "" : "s"} added. ${errors.join(" ")}`
        : `${added} media file${added === 1 ? "" : "s"} added.`;
    }

    fileInput.addEventListener("change", async () => {
      await addMediaFiles(fileInput.files);
      fileInput.value = "";
    });

    const clearMediaDrag = () => {
      mediaDragDepth = 0;
      dialog.classList.remove("psv-media-drag-active");
    };
    const captureMediaDrag = event => {
      if (!isFileDrag(event)) return;

      // ComfyUI also accepts files on its graph canvas. Capture native file
      // drags at the window boundary while this modal is open so the graph's
      // document-level handler cannot consume the drop before the Director.
      event.preventDefault();
      event.stopImmediatePropagation();

      if (event.type === "dragenter") {
        mediaDragDepth += 1;
        dialog.classList.add("psv-media-drag-active");
      } else if (event.type === "dragover") {
        dialog.classList.add("psv-media-drag-active");
        if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
      } else if (event.type === "dragleave") {
        if (!event.relatedTarget) {
          clearMediaDrag();
        } else {
          mediaDragDepth = Math.max(0, mediaDragDepth - 1);
          if (!mediaDragDepth) dialog.classList.remove("psv-media-drag-active");
        }
      } else if (event.type === "drop") {
        const files = Array.from(event.dataTransfer?.files || []);
        clearMediaDrag();
        void addMediaFiles(files);
      }
    };
    const mediaDragEvents = ["dragenter", "dragover", "dragleave", "drop"];
    mediaDragEvents.forEach(type => window.addEventListener(type, captureMediaDrag, true));
    const captureMediaPaste = event => {
      const files = clipboardImageFiles(event);
      if (!files.length) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      void addMediaFiles(files);
    };
    window.addEventListener("paste", captureMediaPaste, true);

    function renderReferences() {
      referencesPane.replaceChildren();
      const heading = el("div", "psv-row");
      heading.append(el("h3", "", "References"), button("Add media", () => fileInput.click()));
      const dropZone = button("Drop media or paste an image here", () => fileInput.click(), "psv-media-dropzone");
      dropZone.title = "Choose files, drop media, or paste an image anywhere on the Director Canvas";
      const stack = el("div", "psv-stack");
      if (!draft.references.length) stack.append(el("div", "psv-empty", "No references. Auto mode resolves to T2VA."));
      draft.references.forEach(reference => {
        const card = el("div", `psv-reference${reference.id === selectedReferenceId ? " selected" : ""}`);
        const head = el("div", "psv-reference-head");
        head.append(
          el("strong", "", referenceDisplayLabel(draft.references, reference)),
          el("span", "", reference.name || reference.path || reference.kind),
        );
        card.append(head, el("div", "psv-help", (reference.roles || []).map(role => ROLE_LABELS[role] || role).join(" · ") || "No role"));
        card.addEventListener("click", () => { selectedReferenceId = reference.id; renderInspector(); renderReferences(); });
        stack.append(card);
      });
      referencesPane.append(heading, dropZone, stack, fileInput);
    }

    function documentDuration() {
      return Math.max(MIN_SHOT_SECONDS, Number(draft.duration_seconds) || 5);
    }

    function captureShotDurations() {
      const duration = documentDuration();
      return new Map(draft.shots.map((shot, index) => [
        shot.id,
        Math.max(MIN_SHOT_SECONDS, (Number(draft.shots[index + 1]?.start) || duration) - (Number(shot.start) || 0)),
      ]));
    }

    function clearDropMarkers() {
      dialog.querySelectorAll(".psv-drop-marker,.psv-list-drop-marker").forEach(marker => marker.remove());
      dialog.querySelectorAll(".psv-dragging").forEach(item => item.classList.remove("psv-dragging"));
    }

    function beginShotDrag(event, shot) {
      if (event.target.closest(".psv-trim-handle")) { event.preventDefault(); return; }
      shotDrag = { id: shot.id, durations: captureShotDurations() };
      selectedShotId = shot.id;
      selectedReferenceId = null;
      event.currentTarget.classList.add("psv-dragging");
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/x-promptstudio-shot", shot.id);
      event.dataTransfer.setData("text/plain", shot.id);
    }

    function reorderShot(insertIndex) {
      if (!shotDrag) return;
      const shot = draft.shots.find(item => item.id === shotDrag.id);
      const remaining = draft.shots.filter(item => item.id !== shotDrag.id);
      if (!shot) return;
      remaining.splice(Math.max(0, Math.min(insertIndex, remaining.length)), 0, shot);
      let start = 0;
      remaining.forEach(item => {
        item.start = roundTime(start);
        start += shotDrag.durations.get(item.id) || MIN_SHOT_SECONDS;
      });
      draft.shots = remaining;
      draft.duration_seconds = roundTime(start);
      durationControl.value = draft.duration_seconds;
      selectedShotId = shot.id;
      selectedReferenceId = null;
    }

    function timelineInsertionIndex(track, clientX) {
      const blocks = Array.from(track.querySelectorAll(".psv-shot-block"))
        .filter(block => block.dataset.shotId !== shotDrag?.id);
      return blocks.filter(block => {
        const rect = block.getBoundingClientRect();
        return clientX >= rect.left + rect.width / 2;
      }).length;
    }

    function showTimelineDropMarker(track, insertIndex) {
      track.querySelector(".psv-drop-marker")?.remove();
      const blocks = Array.from(track.querySelectorAll(".psv-shot-block"))
        .filter(block => block.dataset.shotId !== shotDrag?.id);
      const marker = el("div", "psv-drop-marker");
      const left = insertIndex < blocks.length
        ? blocks[insertIndex].offsetLeft - 3
        : blocks.length ? blocks.at(-1).offsetLeft + blocks.at(-1).offsetWidth + 1 : 0;
      marker.style.left = `${Math.max(0, left)}px`;
      track.append(marker);
    }

    function currentBoundary(boundaryIndex) {
      return boundaryIndex < draft.shots.length
        ? Number(draft.shots[boundaryIndex].start) || 0
        : documentDuration();
    }

    function setBoundary(boundaryIndex, requestedTime) {
      if (boundaryIndex < 1 || boundaryIndex > draft.shots.length) return;
      const previousStart = Number(draft.shots[boundaryIndex - 1].start) || 0;
      const minimum = previousStart + MIN_SHOT_SECONDS;
      const maximum = boundaryIndex < draft.shots.length
        ? (Number(draft.shots[boundaryIndex + 1]?.start) || documentDuration()) - MIN_SHOT_SECONDS
        : MAX_DURATION_SECONDS;
      const time = roundTime(Math.max(minimum, Math.min(maximum, requestedTime)));
      if (boundaryIndex < draft.shots.length) draft.shots[boundaryIndex].start = time;
      else {
        draft.duration_seconds = time;
        durationControl.value = time;
      }
    }

    function refreshTimelineGeometry(track, scale) {
      const duration = documentDuration();
      track.style.width = `${Math.max(520, duration * scale)}px`;
      draft.shots.forEach((shot, index) => {
        const end = Number(draft.shots[index + 1]?.start) || duration;
        const block = track.querySelector(`[data-shot-id="${CSS.escape(shot.id)}"]`);
        if (!block) return;
        block.style.left = `${Math.max(0, Number(shot.start) || 0) * scale}px`;
        block.style.width = `${Math.max(14, (end - (Number(shot.start) || 0)) * scale - 2)}px`;
        const range = block.querySelector(".psv-shot-range");
        if (range) range.textContent = `${Number(shot.start).toFixed(2)}–${Number(end).toFixed(2)}s`;
      });
    }

    function beginBoundaryResize(event, index, edge, track, scale) {
      event.preventDefault();
      event.stopPropagation();
      const boundaryIndex = edge === "left" ? index : index + 1;
      if (!boundaryIndex) return;
      selectedShotId = draft.shots[index].id;
      selectedReferenceId = null;
      document.body.classList.add("psv-resizing");
      const move = moveEvent => {
        const trackRect = track.getBoundingClientRect();
        const rawTime = (moveEvent.clientX - trackRect.left) / scale;
        setBoundary(boundaryIndex, moveEvent.altKey ? roundTime(rawTime) : snapToFrame(rawTime));
        refreshTimelineGeometry(track, scale);
        const start = Number(draft.shots[index].start) || 0;
        const end = Number(draft.shots[index + 1]?.start) || documentDuration();
        status.textContent = `Shot ${index + 1}: ${(end - start).toFixed(2)}s${moveEvent.altKey ? "" : " · snapped to 24 fps"}`;
      };
      const finish = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", finish);
        window.removeEventListener("pointercancel", finish);
        document.body.classList.remove("psv-resizing");
        render();
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", finish, { once: true });
      window.addEventListener("pointercancel", finish, { once: true });
    }

    function trimHandle(index, edge, track, scale) {
      const handle = el("div", `psv-trim-handle psv-trim-${edge}`);
      const boundaryIndex = edge === "left" ? index : index + 1;
      handle.role = "separator";
      handle.tabIndex = boundaryIndex ? 0 : -1;
      handle.ariaLabel = edge === "left" ? `Trim the start of shot ${index + 1}` : `Trim the end of shot ${index + 1}`;
      handle.title = boundaryIndex ? "Drag to trim. Hold Alt for sub-frame precision." : "The video starts at 0 seconds.";
      if (!boundaryIndex) handle.classList.add("disabled");
      handle.addEventListener("pointerdown", event => beginBoundaryResize(event, index, edge, track, scale));
      handle.addEventListener("dragstart", event => event.preventDefault());
      handle.addEventListener("keydown", event => {
        if (!boundaryIndex || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
        event.preventDefault();
        const step = event.shiftKey ? 1 : 1 / FPS;
        setBoundary(boundaryIndex, currentBoundary(boundaryIndex) + (event.key === "ArrowLeft" ? -step : step));
        render();
      });
      return handle;
    }

    function renderTimeline() {
      const old = shotsPane.querySelector(".psv-timeline");
      if (old) old.remove();
      const timeline = el("div", "psv-timeline");
      timeline.append(el("div", "psv-timeline-help", "Drag shots to reorder · drag either edge to trim · 24 fps snap"));
      const ruler = el("div", "psv-ruler");
      const duration = documentDuration();
      const scale = Math.max(56, 680 / duration);
      for (let second = 0; second <= Math.ceil(duration); second += 1) {
        const tick = el("span", "", `${second}s`); tick.style.left = `${second * scale}px`; ruler.append(tick);
      }
      const track = el("div", "psv-track"); track.style.width = `${Math.max(520, duration * scale)}px`;
      draft.shots.forEach((shot, index) => {
        const end = draft.shots[index + 1]?.start ?? duration;
        const block = el("div", `psv-shot-block${shot.id === selectedShotId ? " selected" : ""}`);
        block.dataset.shotId = shot.id;
        block.draggable = true;
        block.title = "Drag to rearrange this shot";
        block.style.left = `${Math.max(0, shot.start) * scale}px`;
        block.style.width = `${Math.max(14, (end - shot.start) * scale - 2)}px`;
        block.append(
          trimHandle(index, "left", track, scale),
          el("strong", "", `Shot ${index + 1}`),
          el("small", "psv-shot-range", `${Number(shot.start).toFixed(2)}–${Number(end).toFixed(2)}s`),
          el("small", "", shot.camera?.type || "No camera motion"),
          trimHandle(index, "right", track, scale),
        );
        block.addEventListener("click", () => { selectedShotId = shot.id; selectedReferenceId = null; render(); });
        block.addEventListener("dragstart", event => beginShotDrag(event, shot));
        block.addEventListener("dragend", () => { shotDrag = null; clearDropMarkers(); render(); });
        track.append(block);
      });
      track.addEventListener("dragover", event => {
        if (!shotDrag || isFileDrag(event)) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
        const insertIndex = timelineInsertionIndex(track, event.clientX);
        showTimelineDropMarker(track, insertIndex);
      });
      track.addEventListener("drop", event => {
        if (!shotDrag || isFileDrag(event)) return;
        event.preventDefault();
        const insertIndex = timelineInsertionIndex(track, event.clientX);
        reorderShot(insertIndex);
        shotDrag = null;
        clearDropMarkers();
        render();
      });
      timeline.append(ruler, track);
      shotsPane.prepend(timeline);
    }

    function renderShots() {
      shotsPane.replaceChildren();
      const heading = el("div", "psv-row");
      heading.append(el("h3", "", "Shots"), button("Add shot", () => {
        const last = draft.shots.at(-1);
        const duration = documentDuration();
        const lastStart = Number(last?.start) || 0;
        let start = snapToFrame(lastStart + (duration - lastStart) / 2);
        if (duration - lastStart < MIN_SHOT_SECONDS * 2) {
          if (duration >= MAX_DURATION_SECONDS) {
            status.textContent = "The final shot is too short to split and the video is already at 150 seconds.";
            return;
          }
          start = duration;
          draft.duration_seconds = Math.min(MAX_DURATION_SECONDS, roundTime(duration + MIN_SHOT_SECONDS));
          durationControl.value = draft.duration_seconds;
        }
        const shot = { ...defaultShot(draft.shots.length), start };
        draft.shots.push(shot); selectedShotId = shot.id; render();
      }));
      shotsPane.append(heading);
      renderTimeline();
      const list = el("div", "psv-stack psv-shot-list");
      draft.shots.forEach((shot, index) => {
        const card = el("div", `psv-shot-card${shot.id === selectedShotId ? " selected" : ""}`);
        card.dataset.shotId = shot.id;
        card.draggable = true;
        card.title = "Drag to rearrange this shot";
        const head = el("div", "psv-shot-head");
        head.append(el("strong", "", `Shot ${index + 1}`), el("span", "psv-help", index ? `Cut ${Number(shot.start).toFixed(3)}s` : "Opening"));
        card.append(head, el("div", "psv-help", shot.composition || shot.action || "Empty shot"));
        card.addEventListener("click", () => { selectedShotId = shot.id; selectedReferenceId = null; render(); });
        card.addEventListener("dragstart", event => beginShotDrag(event, shot));
        card.addEventListener("dragend", () => { shotDrag = null; clearDropMarkers(); render(); });
        list.append(card);
      });
      list.addEventListener("dragover", event => {
        if (!shotDrag || isFileDrag(event)) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
        list.querySelector(".psv-list-drop-marker")?.remove();
        const cards = Array.from(list.querySelectorAll(".psv-shot-card"))
          .filter(card => card.dataset.shotId !== shotDrag.id);
        const insertIndex = cards.filter(card => {
          const rect = card.getBoundingClientRect();
          return event.clientY >= rect.top + rect.height / 2;
        }).length;
        const marker = el("div", "psv-list-drop-marker");
        if (insertIndex < cards.length) list.insertBefore(marker, cards[insertIndex]);
        else list.append(marker);
        marker.dataset.insertIndex = String(insertIndex);
      });
      list.addEventListener("drop", event => {
        if (!shotDrag || isFileDrag(event)) return;
        event.preventDefault();
        const marker = list.querySelector(".psv-list-drop-marker");
        const insertIndex = Number(marker?.dataset.insertIndex ?? draft.shots.length - 1);
        reorderShot(insertIndex);
        shotDrag = null;
        clearDropMarkers();
        render();
      });
      shotsPane.append(list);
    }

    function renderReferenceInspector(reference) {
      inspectorPane.append(
        el("h3", "", `${referenceDisplayLabel(draft.references, reference)} roles`),
        el("div", "psv-help", reference.name || reference.path),
      );
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
      const shotEnd = Number(draft.shots[index + 1]?.start) || documentDuration();
      inspectorPane.append(el("div", "psv-shot-timing", `${(shotEnd - (Number(shot.start) || 0)).toFixed(2)}s · ${Number(shot.start).toFixed(2)}–${shotEnd.toFixed(2)}s`));
      if (index) {
        const start = input(shot.start, value => { if (Number.isFinite(value)) setBoundary(index, value); renderTimeline(); }, "number"); start.min = String(MIN_SHOT_SECONDS); start.step = String(1 / FPS);
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
      inspectorPane.append(
        el("h4", "", "Whole video"),
        field("Production brief (planning only)", textarea(draft.main_description, value => { draft.main_description = value; })),
        field("Overall soundscape", textarea(draft.overall_soundscape, value => { draft.overall_soundscape = value; })),
        field("Non-diegetic music", textarea(draft.non_diegetic_music, value => { draft.non_diegetic_music = value; })),
      );
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
    dialog.addEventListener("close", () => {
      clearMediaDrag();
      mediaDragEvents.forEach(type => window.removeEventListener(type, captureMediaDrag, true));
      window.removeEventListener("paste", captureMediaPaste, true);
      dialog.remove();
    }, { once: true });
    render(); dialog.showModal();
  }

  if (node.addDOMWidget) {
    domWidget = node.addDOMWidget("promptstudio_video_director", "custom", summary, { serialize: false, hideOnZoom: false, getHeight: () => 150 });
    domWidget.computeSize = width => [width, 150];
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
