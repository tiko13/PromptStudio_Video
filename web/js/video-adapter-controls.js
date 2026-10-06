import { createSearchableSelect } from "/extensions/ComfyUI_PromptStudio/js/prompt-studio/ui/searchable-select.js";
import { videoAdapterLoaders, adapterMatchesType } from "./video-adapters.js";

export function createVideoAdapterControls({ project, workflow, catalog, disabled = false, onChange, onRefresh }) {
  const loaders = videoAdapterLoaders(workflow);
  const root = document.createElement("details");
  root.className = "psvstudio-adapters";
  root.open = true;
  const summary = document.createElement("summary"); summary.textContent = "LoRAs & reference adapters";
  root.append(summary);
  const body = document.createElement("div"); body.className = "promptstudio-lora-groups"; root.append(body);
  const make = (tag, text) => { const node = document.createElement(tag); node.textContent = text; return node; };
  const refresh = make("button", "↻"); refresh.type = "button"; refresh.disabled = disabled;
  refresh.className = "psvstudio-button psvstudio-adapter-refresh";
  refresh.title = "Refresh adapters"; refresh.setAttribute("aria-label", refresh.title);
  refresh.onclick = event => { event.preventDefault(); onRefresh(); }; summary.append(refresh);
  body.append(make("small", disabled ? "This render uses its saved adapter selections." : "Use H3 LoRAs matching the active FL2VA or Ref2VA model. Turbo remains controlled by the sampling profile."));
  if (!catalog) { body.append(make("p", "Load the adapter catalog to browse installed files.")); return root; }
  if (catalog.error) body.append(make("p", catalog.error));
  function render() {
    body.querySelectorAll(".psvstudio-adapter-group").forEach(node => node.remove());
    for (const [key, title, kinds] of [["content_loras", "LoRAs", ["lora"]], ["reference_adapters", "RefMods / RefLoRAs", ["refmod", "reflora"]]]) {
      const group = make("section", ""); group.className = "psvstudio-adapter-group promptstudio-lora-group";
      const heading = make("div", ""); heading.className = "promptstudio-lora-group-heading";
      const loader = loaders[key];
      heading.append(make("strong", `${title} Type: ${loader.type || "not set"}`), make("small", loader.id ? `Node ${loader.id}` : "Studio loader")); group.append(heading);
      if (loader.error) group.append(make("small", loader.error));
      const rows = project.document[key] || [];
      const available = (catalog.adapters || []).filter(item => kinds.includes(item.kind) && adapterMatchesType(item.name, loader.type));
      const id = item => `${item.category}:${item.name.replaceAll("\\", "/").toLowerCase()}`;
      const items = available.filter(item => !rows.some(row => id({...row, category:row.category || "loras"}) === id(item)))
        .map(item => ({name:id(item), label:`${item.name} (${item.kind})`}));
      const add = make("div", ""); add.className = "promptstudio-lora-add";
      if (!disabled && !loader.error && rows.length < 8) add.append(createSearchableSelect({items, label:`Add ${title}`,
        placeholder:key === "content_loras" ? "Add a LoRA" : "Add a reference adapter", emptyText:`No ${title} available`, onSelect: selected => {
          const item = available.find(item => id(item) === selected);
          if (!item) return;
          const row = key === "content_loras" ? {name:item.name, strength:1} : {
            name:item.name, category:item.category, kind:item.kind, lora_strength:1,
            visual_strength:1, audio_strength:1,
            components:item.modalities.every(kind => kind === "audio") ? "audio" : item.modalities.includes("audio") ? "all" : "visual"};
          (project.document[key] ||= []).push(row); onChange(); render();
        }}).element);
      for (const row of rows) {
        const card = make("div", ""); card.className = "psvstudio-adapter-row";
        const rowHeading = make("div", ""); rowHeading.className = "promptstudio-lora-row";
        card.append(rowHeading);
        const info = available.find(item => id(item) === id({...row,category:row.category || "loras"}));
        const name = make("span", `${row.name.replaceAll("\\", "/").split("/").slice(-1)[0]}${info ? "" : " · unavailable"}`);
        name.className = "promptstudio-lora-name"; name.title = row.name;
        rowHeading.append(name);
        function numeric(field, label, min, max, compact = false) {
          const wrapper = make("label", label);
          const input = document.createElement("input"); input.type = "number"; input.min = min; input.max = max;
          input.step = "0.05"; input.value = row[field] ?? 1; input.disabled = disabled;
          input.setAttribute("aria-label", `${row.name} ${label}`);
          input.onchange = () => {
            if (!input.checkValidity() || !Number.isFinite(input.valueAsNumber)) { input.reportValidity(); input.value = row[field] ?? 1; return; }
            row[field] = input.valueAsNumber; onChange();
          };
          input.title = label;
          if (compact) rowHeading.append(input);
          else { wrapper.append(input); card.append(wrapper); }
        }
        if (key === "content_loras") numeric("strength", "LoRA strength", -100, 100, true);
        else {
          if (row.kind === "reflora") numeric("lora_strength", "LoRA strength", -100, 100, true);
          else rowHeading.classList.add("psvstudio-adapter-reference-heading");
          const label = make("label", "References"); const select = document.createElement("select");
          for (const [value, text] of [["all","Visual + audio"],["visual","Visual only"],["audio","Audio only"]]) {
            const option = make("option", text); option.value = value; select.append(option);
          }
          select.value = row.components || "all"; select.disabled = disabled;
          select.setAttribute("aria-label", `${row.name} reference components`);
          select.onchange = () => { row.components = select.value; onChange(); render(); };
          label.append(select); card.append(label);
          if (row.components !== "audio") numeric("visual_strength", "Visual retention", 0, 1);
          if (row.components !== "visual") numeric("audio_strength", "Audio retention", 0, 1);
          if (info) card.append(make("small", `${info.tokens.toLocaleString()} reference tokens in file`));
        }
        const remove = make("button", "×"); remove.type = "button"; remove.disabled = disabled;
        remove.className = "psvstudio-button promptstudio-lora-remove"; remove.title = `Remove ${row.name}`;
        remove.setAttribute("aria-label", `Remove ${row.name}`);
        remove.onclick = () => { project.document[key] = rows.filter(item => item !== row); onChange(); render(); };
        rowHeading.append(remove); group.append(card);
      }
      group.append(add); body.append(group);
    }
  }
  render();
  if (catalog.errors?.length) body.append(make("small", `${catalog.errors.length} unreadable adapter file(s). ${catalog.errors.map(item => `${item.name}: ${item.error}`).join("; ")}`));
  return root;
}
