import {validateStoreResponse} from "../../ComfyUI_PromptStudio/web/js/prompt-studio/chat/store-response.js";
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import {createDraftScheduler} from "../../ComfyUI_PromptStudio/web/js/prompt-studio/chat/draft-outbox.js";
import {tagSubmission} from "../../ComfyUI_PromptStudio/web/js/prompt-studio/generation/recovery.js";
import {archiveDraft} from "../../ComfyUI_PromptStudio/web/js/prompt-studio/chat/draft-review.js";

const source = fs.readFileSync(new URL("../web/js/promptstudio_video_studio.js", import.meta.url), "utf8");
function functionSource(name) {
  const start = source.search(new RegExp(`^(?:async )?function ${name}\\(`, "m"));
  assert.notEqual(start, -1, name);
  const tail = source.slice(start);
  const next = tail.slice(1).search(/\n(?:async )?function \w+\(/);
  return next < 0 ? tail : tail.slice(0, next + 1);
}
const copy = value => structuredClone(value);
const plain = value => JSON.parse(JSON.stringify(value));
function project(id, action = "base") {
  return { id, name: id, updated_at: 1, document: { shots: [{ id: `${id}-shot`, action }] }, generations: [] };
}
function storage() {
  const values = new Map();
  return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key), values };
}
function harness(server, shared = {}) {
  const state = { projects: copy(server.projects), projectBase: copy(server.projects), activeProjectId: server.projects[0]?.id,
    projectRevision: server.revision, projectMutation: 0, projectSavedMutation: 0, projectSaveChain: Promise.resolve(),
    projectSaveTimer: null, projectConflicts: [], projectDraftKey: "", panel: null };
  const localStorage = shared.localStorage || storage();
  const sessionStorage = shared.sessionStorage || storage();
  const outboxRecords = shared.outboxRecords || new Map();
  const videoDraftOutbox = {
    async put(key,record) { outboxRecords.set(key,copy(record)); },
    async get(key) { return copy(outboxRecords.get(key) || null); },
    async acknowledge(key,mutation) { if (outboxRecords.get(key)?.mutation <= mutation) outboxRecords.delete(key); },
  };
  const calls = [];
  let fail = false;
  let loseAcknowledgement = false;
  let onPut = null;
  const context = vm.createContext({ validateStoreResponse, archiveDraft, showRecoveredProjectDrafts: async () => {}, state, structuredClone, localStorage, sessionStorage, Map, Set, console,
    videoDraftOutbox, videoDraftPending:Promise.resolve(true), draftTabKey:product=>product, showDraftStorageFailure() {},
    projectDraftScheduler: createDraftScheduler(() => {}),
    captureRuntimeProvenance:async()=>({version:1}),
    tagSubmission,
    markProjectChanged() { state.projectMutation++; },
    directorSettings:()=>({keep_models_loaded:true}),
    workflowReferenceController:null,
    PROJECTS_ENDPOINT: "/projects", URLSearchParams, requireHistoryIndex() {}, async prepareHistoryIndex() { return false; }, clearTimeout, setTimeout, clone: copy, makeId: () => "tab-one",
    activeProject: () => state.projects.find(item => item.id === state.activeProjectId),
    renderAll() {}, setSaveState() {}, setStatus() {},
    api: { async fetchApi(url, options = {}) {
      calls.push(options.method || "GET");
      if (fail) throw new Error("offline");
      if (!options.method) return response(200, server);
      const payload = JSON.parse(options.body);
      if (onPut) { const callback = onPut; onPut = null; await callback(); }
      if (payload.revision !== server.revision) return response(409, { error: "conflict" });
      const records = payload.partial
        ? [...new Map([...server.projects,...payload.projects].map(project=>[project.id,project])).values()].filter(project=>!payload.deletedProjectIds?.includes(project.id))
        : payload.projects;
      Object.assign(server, copy(payload), {projects:copy(records), revision: server.revision + 1 });
      if (loseAcknowledgement) { loseAcknowledgement = false; throw new Error("response lost"); }
      return response(200, { revision: server.revision });
    } },
  });
  const names = ["mergeProjectVersions", "projectDraftKey", "writeProjectDraft", "applyProjectMerge", "showProjectSaveFailure", "fetchProjectStore", "persistProjects", "loadProjects"];
  vm.runInContext(names.map(functionSource).join("\n"), context);
  return { state, context, calls, localStorage, sessionStorage, outboxRecords,
    merge: context.mergeProjectVersions, save: options => context.persistProjects(options),
    edit(fn) { fn(state.projects); state.projectMutation++; context.writeProjectDraft(); },
    fail(value) { fail = value; }, loseAck() { loseAcknowledgement = true; }, onPut(callback) { onPut = callback; },
  };
}
function response(status, data) { return { ok: status < 300, status, async json() { return copy(data); } }; }

test("clients editing different projects reconcile 409 without losing either edit", async () => {
  const server = { revision: 1, projects: [project("a"), project("b")] };
  const a = harness(server), b = harness(server);
  a.edit(projects => { projects[0].document.shots[0].action = "from a"; });
  b.edit(projects => { projects[1].document.shots[0].action = "from b"; });
  await a.save({ immediate: true });
  assert.equal((await b.save({ immediate: true })).ok, true);
  assert.deepEqual(server.projects.map(item => item.document.shots[0].action), ["from a", "from b"]);
  assert.deepEqual(b.calls, ["PUT", "GET", "PUT"]);
});

test("competing same-shot edits block subsequent writes and preserve a durable draft", async () => {
  const server = { revision: 1, projects: [project("a")] };
  const a = harness(server), b = harness(server);
  a.edit(projects => { projects[0].document.shots[0].action = "server"; });
  b.edit(projects => { projects[0].document.shots[0].action = "local"; });
  await a.save({ immediate: true });
  await assert.rejects(b.save({ immediate: true }), /Competing edits/);
  await assert.rejects(b.save({ immediate: true }), /Review competing/);
  assert.equal(server.projects[0].document.shots[0].action, "server");
  assert.equal(b.state.projects[0].document.shots[0].action, "local");
  assert.equal(b.state.projectConflicts[0].path, "projects[a].document.shots[a-shot]");
  assert.ok(b.localStorage.getItem(b.state.projectDraftKey).includes('"local"'));
});

test("network recovery retries only persistence; lost success acknowledgement reconciles", async () => {
  const server = { revision: 1, projects: [project("a")] };
  const client = harness(server);
  client.edit(projects => { projects[0].name = "edited"; });
  client.fail(true);
  assert.equal((await client.save()).ok, false);
  client.fail(false);
  client.loseAck();
  await assert.rejects(client.save({ immediate: true }), /response lost/);
  assert.equal((await client.save({ immediate: true })).ok, true);
  assert.equal(server.projects.length, 1);
  assert.equal(server.projects[0].name, "edited");
  assert.equal(client.localStorage.getItem(client.state.projectDraftKey), null);
});

test("edits made during a request survive and are saved by the chained followup", async () => {
  const server = { revision: 1, projects: [project("a")] };
  const client = harness(server);
  client.edit(projects => { projects[0].name = "first"; });
  client.onPut(() => client.edit(projects => { projects[0].name = "second"; }));
  await client.save({ immediate: true });
  await client.state.projectSaveChain;
  assert.equal(server.projects[0].name, "second");
  assert.equal(client.state.projectSavedMutation, client.state.projectMutation);
});

test("reload archives a stale draft for review and leaves server projects and uncertain preparations untouched", async () => {
  const server = { revision: 1, projects: [project("a")] };
  const client = harness(server);
  client.edit(projects => {
    projects[0].document.shots[0].action = "draft";
    projects[0].generations.push({ id: "g", status: "queueing", prompt_id: "" });
  });
  server.projects[0].document.shots[0].action = "remote";
  server.revision++;
  const reloaded = harness(server, client);
  await reloaded.context.loadProjects();
  assert.equal(reloaded.state.projects[0].document.shots[0].action, "remote");
  assert.equal(reloaded.state.projects[0].generations.length, 0);
  const archive = [...reloaded.outboxRecords.entries()].find(([key]) => key.startsWith('video:review:'))[1];
  assert.equal(archive.projects[0].document.shots[0].action, 'draft');
  assert.equal(archive.projects[0].generations[0].status, 'queueing');
  assert.equal(reloaded.localStorage.getItem(reloaded.state.projectDraftKey), null);
  assert.deepEqual(reloaded.calls, ["GET"], "unreviewed drafts do not write or execute work");
});

test("record deletion versus editing conflicts and explicit choices preserve IDs", () => {
  const base = [project("a")], remote = copy(base);
  remote[0].name = "remote edit";
  const client = harness({ revision: 1, projects: base });
  const conflicts = [];
  client.merge(base, [], remote, conflicts);
  assert.equal(conflicts[0].path, "projects[a]");
  assert.deepEqual(plain(client.merge(base, [], remote, [], "projects", new Map([["projects[a]", "remote"]]))), remote);
  assert.deepEqual(plain(client.merge(base, [], remote, [], "projects", new Map([["projects[a]", "local"]]))), []);
});

test("generation envelope stays atomic; independent generation appends merge", () => {
  const base = [project("a")];
  base[0].generations = [{ id: "g", workflow_snapshot: { workflow: { nodes: [] }, output: { one: {} } }, status: "queued" }];
  const local = copy(base), remote = copy(base);
  local[0].generations.push({ id: "local-generation" });
  remote[0].generations.push({ id: "remote-generation" });
  const client = harness({ revision: 1, projects: base });
  assert.equal(client.merge(base, local, remote)[0].generations.length, 3);
  local[0].generations[0].status = "complete";
  remote[0].generations[0].workflow_snapshot.output.one.changed = true;
  const conflicts = [];
  const merged = client.merge(base, local, remote, conflicts);
  assert.equal(conflicts[0].path, "projects[a].generations[g]");
  assert.deepEqual(plain(merged[0].generations[0].workflow_snapshot), base[0].generations[0].workflow_snapshot);
});

test("merging keeps project and generation references used by active pollers", () => {
  const server = { revision: 1, projects: [project("a")] };
  server.projects[0].generations.push({ id: "g", status: "queued" });
  const client = harness(server);
  const projectRef = client.state.projects[0], generationRef = projectRef.generations[0];
  const merged = copy(client.state.projects);
  merged[0].name = "remote";
  client.context.applyProjectMerge(merged);
  assert.equal(client.state.projects[0], projectRef);
  assert.equal(projectRef.generations[0], generationRef);
});

test("mandatory persistence failure prevents the common queue path from submitting", async () => {
  const client = harness({ revision: 1, projects: [project("a")] });
  client.edit(projects => { projects[0].name = "unsaved"; });
  client.fail(true);
  let queued = 0;
  Object.assign(client.context, { applyPromptStudioInputValues() {}, normalizePromptStudioInputSelections: value => value,
    instrumentGenerationSnapshot: () => ({}), });
  client.context.api.queuePrompt = async () => { queued++; return { prompt_id: "prompt" }; };
  vm.runInContext(functionSource("queueSnapshot"), client.context);
  await assert.rejects(client.context.queueSnapshot(client.state.projects[0], {}, { workflow: {}, output: {} }, {}), /offline/);
  assert.equal(queued, 0);
});

for (const scenario of ["queue-fails", "release-fails", "cancelled", "dedicated"]) {
  test(`video GPU handoff: ${scenario}`, async () => {
    const client = harness({ revision: 1, projects: [project("a")] });
    const operation = { id: "gpu-test", status: "queueing" };
    const events = [];
    Object.assign(client.context, {
      applyPromptStudioInputValues() {}, normalizePromptStudioInputSelections: value => value,
      instrumentGenerationSnapshot: () => ({}),
      directorSettings: () => ({ llm_provider: "llamacpp", keep_models_loaded: scenario === "dedicated" }),
    });
    const fetch = client.context.api.fetchApi;
    client.context.api.fetchApi = async (url, options) => {
      if (!url.includes("/llm/")) return fetch(url, options);
      if (url.endsWith("/release")) {
        events.push("release");
        if (scenario === "release-fails") return response(500, {error:"unload failed"});
        if (scenario === "cancelled") operation.status = "cancelled";
        return response(200, {handoff_token:"token"});
      }
      assert.equal(JSON.parse(options.body).handoff_token, "token");
      events.push("ack");
      return response(200, {completed:true});
    };
    client.context.api.queuePrompt = async () => { events.push("queue"); throw new Error("queue failed"); };
    vm.runInContext(functionSource("queueSnapshot"), client.context);
    const run = client.context.queueSnapshot(client.state.projects[0], {}, {workflow:{},output:{}}, {}, operation);
    if (scenario === "cancelled") await run;
    else await assert.rejects(run, scenario === "release-fails" ? /unload failed/ : /queue failed/);
    assert.deepEqual(events, scenario === "dedicated" ? ["queue"] : scenario === "release-fails" ? ["release"]
      : scenario === "cancelled" ? ["release", "ack"] : ["release", "queue", "ack"]);
  });
}

test("quota failure is surfaced and does not hide mandatory remote save failure", async () => {
  const client = harness({ revision: 1, projects: [project("a")] });
  client.localStorage.setItem = () => { throw new Error("quota exceeded"); };
  client.context.videoDraftOutbox.put = async () => { throw new Error("quota exceeded"); };
  client.edit(projects => { projects[0].name = "unsaved"; });
  client.fail(true);
  await assert.rejects(client.save({ immediate: true }), /offline/);
  await client.context.videoDraftPending;
  assert.match(client.state.projectDraftError, /quota exceeded/);
});

test("accepted queue remains tracked after save failure; retry does not queue again", async () => {
  const server = { revision: 1, projects: [project("a")] };
  const client = harness(server);
  const operation = { id: "generation", status: "queueing", prompt_id: "" };
  client.edit(projects => projects[0].generations.push(operation));
  let queued = 0, polled = "";
  Object.assign(client.state, { generationProgress: new Map(), generationFailures: new Map() });
  Object.assign(client.context, { applyPromptStudioInputValues() {}, normalizePromptStudioInputSelections: value => value,
    instrumentGenerationSnapshot: () => ({}), touchGeneration() {}, pollGeneration: id => { polled = id; },
    markProjectChanged() { client.state.projectMutation++; client.context.writeProjectDraft(); },
  });
  client.context.api.queuePrompt = async () => {
    queued++;
    assert.ok(client.localStorage.getItem(client.state.projectDraftKey), "queue intent is durable before submission");
    client.fail(true);
    return { prompt_id: "accepted-prompt" };
  };
  vm.runInContext(functionSource("queueSnapshot"), client.context);
  await client.context.queueSnapshot(client.state.projects[0], {}, { workflow: {}, output: {} }, {}, operation);
  assert.equal(operation.prompt_id, "accepted-prompt");
  assert.equal(polled, "accepted-prompt");
  assert.ok(client.localStorage.getItem(client.state.projectDraftKey).includes("accepted-prompt"));
  client.fail(false);
  await client.save({ immediate: true });
  assert.equal(queued, 1);
  assert.equal(server.projects[0].generations[0].prompt_id, "accepted-prompt");
});

test("independent shots merge but divergent shot order requires explicit review", () => {
  const base = [project("a")];
  base[0].document.shots = [{ id: "one", action: "one" }, { id: "two", action: "two" }, { id: "three", action: "three" }];
  const local = copy(base), remote = copy(base);
  local[0].document.shots[0].action = "local";
  remote[0].document.shots[1].action = "remote";
  const client = harness({ revision: 1, projects: base });
  const conflicts = [];
  assert.deepEqual(plain(client.merge(base, local, remote, conflicts)[0].document.shots.map(item => item.action)), ["local", "remote", "three"]);
  assert.equal(conflicts.length, 0);
  local[0].document.shots.reverse();
  remote[0].document.shots.push(remote[0].document.shots.shift());
  client.merge(base, local, remote, conflicts);
  assert.equal(conflicts[0].path, "projects[a].document.shots");
});
