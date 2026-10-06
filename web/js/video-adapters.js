/** Saved execution choices, independent of the Turbo sampling policy. */
export const REFERENCE_ADAPTER_TYPE = "PSV_MiniMaxH3ReferenceAdapters";
export const CONTENT_LORA_TYPE = "KCPP_PromptStudioLoraLoader";

export function adapterMatchesType(name, type) {
  const parts = String(name || "").replaceAll("\\", "/").replace(/^\/+|\/+$/g, "").split("/");
  type = typeof type === "string" ? type.trim() : "";
  return Boolean(type && ![".", ".."].includes(type) && !/[\\/]/.test(type)
    && !parts.at(-1).startsWith("_") && (type === "*" || (parts.length > 1 && parts[0].toLowerCase() === type.toLowerCase())));
}

/** Empty workflow loaders own the Studio stacks; fixed graph adapters stay fixed. */
export function videoAdapterLoaders(workflow, snapshot = workflow?.snapshot) {
  const output = snapshot?.output || {};
  const directorId = String(workflow?.director_node_id);
  const downstream = (id, seen = new Set()) => {
    if (id === directorId) return true;
    if (seen.has(id)) return false;
    seen.add(id);
    const source = output[id]?.inputs?.model;
    return Array.isArray(source) && source[1] === 0 && downstream(String(source[0]), seen);
  };
  const result = {};
  for (const [key, nodeType, field, stack] of [
    ["content_loras", CONTENT_LORA_TYPE, "lora_type", "lora_stack_json"],
    ["reference_adapters", REFERENCE_ADAPTER_TYPE, "adapter_type", "reference_stack_json"],
  ]) {
    const nodes = Object.entries(output).filter(([id, node]) => node.class_type === nodeType && downstream(id)
      && [undefined, "", "[]"].includes(node.inputs?.[stack])
      && (!node.inputs?.adapter || node.inputs.adapter === "None"));
    const [id, node] = nodes[0] || [];
    const type = node ? (node.inputs?.[field] ?? (key === "reference_adapters" ? "*" : "")) : "MiniMax3";
    result[key] = {id, type: typeof type === "string" ? type.trim() : "", field, stack,
      error: nodes.length > 1 ? `More than one empty ${nodeType} is connected. Use one Studio loader per adapter kind.` : ""};
  }
  return result;
}

export function hasReferenceAdapters(document) {
  return (document?.reference_adapters || []).some(row =>
    (row.components !== "audio" && Number(row.visual_strength ?? 1) > 0)
    || (row.components !== "visual" && Number(row.audio_strength ?? 1) > 0));
}

function nextId(snapshot) {
  const used = new Set([...Object.keys(snapshot.output || {}), ...(snapshot.workflow?.nodes || []).map(n => String(n.id))]);
  let id = 1;
  while (used.has(String(id))) id++;
  return String(id);
}

function appendNode(snapshot, id, type, inputs, outputs, widgets, title) {
  snapshot.output[id] = { class_type: type, inputs, _meta: { title } };
  // The API snapshot is authoritative, including nested workflows. Root-level
  // graphs also carry the inserted nodes/links for opening the saved workflow.
  const graph = snapshot.workflow;
  if (!Array.isArray(graph?.nodes) || !Array.isArray(graph?.links)) return;
  const links = [];
  const descriptors = [];
  let nextLink = Math.max(Number(graph.last_link_id) || 0, ...graph.links.map(link => Number(link[0]) || 0)) + 1;
  for (const [name, value] of Object.entries(inputs)) {
    if (!Array.isArray(value)) continue;
    const origin = graph.nodes.find(n => String(n.id) === String(value[0]));
    if (!origin) continue;
    const dataType = name === "model" ? "MODEL" : name === "positive" ? "CONDITIONING" : "STRING";
    const linkId = nextLink++;
    links.push([linkId, origin.id, value[1], Number(id), descriptors.length, dataType]);
    descriptors.push({name, type:dataType, link:linkId});
    if (origin.outputs?.[value[1]]) (origin.outputs[value[1]].links ||= []).push(linkId);
  }
  graph.links.push(...links);
  graph.nodes.push({id:Number(id), type, title, pos:[900, 200 + 190 * graph.nodes.filter(n => n.type === type).length],
    size:[340,180], flags:{}, order:graph.nodes.length, mode:0, inputs:descriptors,
    outputs:outputs.map(([name, type]) => ({name,type,links:[]})),
    properties:{"Node name for S&R":type}, widgets_values:widgets});
  graph.last_node_id = Math.max(Number(graph.last_node_id) || 0, Number(id));
  graph.last_link_id = nextLink - 1;
}

function reroute(snapshot, sourceId, sourceSlot, targetId, targetSlot, excluded) {
  for (const [id, node] of Object.entries(snapshot.output)) {
    if (excluded.has(id)) continue;
    for (const [key, value] of Object.entries(node.inputs || {})) {
      if (Array.isArray(value) && String(value[0]) === sourceId && value[1] === sourceSlot) node.inputs[key] = [targetId, targetSlot];
    }
  }
  const graph = snapshot.workflow;
  if (!Array.isArray(graph?.links)) return;
  const source = graph.nodes?.find(n => String(n.id) === sourceId);
  const target = graph.nodes?.find(n => String(n.id) === targetId);
  if (!source || !target) return;
  for (const link of graph.links) {
    if (String(link[1]) !== sourceId || link[2] !== sourceSlot || excluded.has(String(link[3]))) continue;
    link[1] = target.id; link[2] = targetSlot;
    source.outputs[sourceSlot].links = (source.outputs[sourceSlot].links || []).filter(id => id !== link[0]);
    target.outputs[targetSlot].links.push(link[0]);
  }
}

export function applyVideoAdapters(snapshot, workflow, document) {
  const directorId = String(workflow.director_node_id);
  if (snapshot.output?.[directorId]?.class_type !== "PSV_MiniMaxH3Director") throw new Error("The workflow has no H3 Director.");
  const loras = structuredClone(document.content_loras || []);
  const references = structuredClone(document.reference_adapters || []);
  const loaders = videoAdapterLoaders(workflow, snapshot);
  for (const [key, rows] of [["content_loras", loras], ["reference_adapters", references]]) {
    const loader = loaders[key];
    if (rows.length && loader.error) throw new Error(loader.error);
    for (const row of rows) {
      if (!adapterMatchesType(row.name, loader.type)) throw new Error(`Adapter '${row.name}' is outside workflow Type '${loader.type}'. Remove it or change the loader Type.`);
    }
  }
  if (hasReferenceAdapters(document) && !snapshot.output[directorId].inputs?.ref2va_model) {
    throw new Error("This workflow needs a Ref2VA model input to use RefMods or RefLoRA references.");
  }
  const activeNames = new Set(loras.map(row => row.name.replaceAll("\\", "/").toLowerCase()));
  if (references.some(row => row.kind === "reflora" && row.lora_strength !== 0 && activeNames.has(row.name.replaceAll("\\", "/").toLowerCase()))) {
    throw new Error("A RefLoRA cannot also be selected as an ordinary LoRA; it would apply its weights twice.");
  }
  let modelId = directorId, modelSlot = 0;
  if (Object.values(snapshot.output).some(node => /Video Studio (Content LoRAs|RefMods \/ RefLoRAs)/.test(node?._meta?.title || ""))) {
    throw new Error("Adapter nodes are already frozen in this snapshot; use replay without reapplying selections.");
  }
  const inserted = new Set();
  function setStack(loader, rows) {
    const json = JSON.stringify(rows);
    snapshot.output[loader.id].inputs[loader.stack] = json;
    const node = snapshot.workflow?.nodes?.find(node => String(node.id) === loader.id);
    if (node) {
      (node.widgets_values_named ||= {})[loader.stack] = json;
      if (Array.isArray(node.widgets_values)) node.widgets_values[loader.stack === "lora_stack_json" ? 1 : 0] = json;
    }
  }
  if (loras.length && loaders.content_loras.id) {
    setStack(loaders.content_loras, loras);
  } else if (loras.length) {
    const id = nextId(snapshot); inserted.add(id);
    appendNode(snapshot, id, CONTENT_LORA_TYPE,
      {model:[modelId, modelSlot], lora_type:loaders.content_loras.type, lora_stack_json:JSON.stringify(loras)}, [["model","MODEL"]],
      [loaders.content_loras.type], "Video Studio Content LoRAs");
    reroute(snapshot, directorId, 0, id, 0, inserted);
    modelId = id; modelSlot = 0;
  }
  if (references.length && loaders.reference_adapters.id) {
    setStack(loaders.reference_adapters, references);
  } else if (references.length) {
    const id = nextId(snapshot); inserted.add(id);
    appendNode(snapshot, id, REFERENCE_ADAPTER_TYPE,
      {model:[modelId, modelSlot], positive:[directorId,1], mode:[directorId,6],
        reference_stack_json:JSON.stringify(references), max_reference_tokens:16384, adapter_type:loaders.reference_adapters.type},
      [["model","MODEL"],["positive","CONDITIONING"]], [JSON.stringify(references),16384,"None",1,1,1,"all",loaders.reference_adapters.type], "Video Studio RefMods / RefLoRAs");
    reroute(snapshot, modelId, modelSlot, id, 0, inserted);
    reroute(snapshot, directorId, 1, id, 1, inserted);
  }
  return snapshot;
}
