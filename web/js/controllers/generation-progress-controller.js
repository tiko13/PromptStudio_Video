import { createFeatureController } from "/extensions/ComfyUI_PromptStudio/js/prompt-studio/ui/feature-controller.js";

/** Application-owned progress projection; all project jobs retain their IDs. */
export function createVideoGenerationProgressController({ state, markGenerationExecuting,
  renderGenerations, failGeneration, executionFailureMessage, setApiConnected,
  renderSystemStatusSummary, freezeDisconnectedControls, isVideoStudioControl,
  isDisconnectedAllowedControl, MutationObserver = globalThis.MutationObserver }) {
  return createFeatureController({
    mount(api, scope) {
      const promptId = event => String(
        event?.detail?.prompt_id || event?.detail?.promptId || state.activeGenerationPromptId || "",
      );
      const updateProgress = (id, changes) => {
        const current = state.generationProgress.get(id) || {};
        state.generationProgress.set(id, { ...current, ...changes });
      };
      const savedProgress = id => state.generationProgress.get(id) || state.pendingGenerationProgress?.get(id) || {};
      // ComfyUI may publish execution events as soon as queuePrompt resolves.
      // Keep a small, bounded handoff buffer for the gap before the caller has
      // recorded that returned prompt ID against its project.
      const rememberEarlyProgress = (id, changes) => {
        if (!id) return;
        const pending = state.pendingGenerationProgress ||= new Map();
        while (pending.size >= 64) pending.delete(pending.keys().next().value);
        pending.set(id, { ...(pending.get(id) || {}), ...changes });
      };
      const runningProgress = event => {
        const nodes = event?.detail?.nodes;
        if (!nodes || typeof nodes !== "object") return null;
        const running = Object.values(nodes).find(node => node?.state === "running");
        if (!running) return null;
        const value = Number(running.value);
        const max = Number(running.max);
        return Number.isFinite(value) && Number.isFinite(max) && max > 0 ? { value, max } : null;
      };
      scope.listen(api, "execution_start", event => {
        const id = promptId(event);
        if (!markGenerationExecuting(id)) {
          rememberEarlyProgress(id, { phase: "generating" });
          return;
        }
        updateProgress(id, { phase: "generating" });
      });
      scope.listen(api, "progress", event => {
        const id = promptId(event);
        const changes = {
          phase: "generating",
          value: Number(event.detail?.value),
          max: Number(event.detail?.max),
        };
        if (!markGenerationExecuting(id)) {
          rememberEarlyProgress(id, changes);
          return;
        }
        if (state.generationProgress.get(id)?.phase === "finalizing") {
          renderGenerations();
          return;
        }
        updateProgress(id, changes);
        renderGenerations();
      });
      for (const eventName of ["execution_error", "execution_interrupted"]) {
        scope.listen(api, eventName, event => {
          const id = promptId(event);
          failGeneration(id, executionFailureMessage(eventName, event?.detail));
        });
      }
      scope.listen(api, "executing", event => {
        const id = promptId(event);
        const tracked = markGenerationExecuting(id);
        const current = savedProgress(id);
        const samplerComplete = Number.isFinite(current.value) && current.max > 0 && current.value >= current.max;
        const node = event?.detail?.node ?? event?.detail;
        const changes = { phase: node == null || samplerComplete ? "finalizing" : "generating" };
        if (!tracked) {
          rememberEarlyProgress(id, changes);
          return;
        }
        updateProgress(id, changes);
        renderGenerations();
      });
      scope.listen(api, "progress_state", event => {
        const id = promptId(event);
        const progress = runningProgress(event);
        const current = savedProgress(id);
        const changes = current.phase === "finalizing"
          ? {}
          : { phase: progress ? "generating" : current.phase || "generating", ...(progress || {}) };
        if (!markGenerationExecuting(id)) return rememberEarlyProgress(id, changes);
        if (current.phase !== "finalizing") updateProgress(id, changes);
        renderGenerations();
      });
      scope.listen(api, "execution_success", event => {
        const id = promptId(event);
        if (!markGenerationExecuting(id)) return;
        updateProgress(id, { phase: "finalizing" });
        renderGenerations();
      });
      scope.listen(api, "reconnecting", () => setApiConnected(false));
      scope.listen(api, "reconnected", () => setApiConnected(true));
      scope.listen(api, "status", event => {
        const queueRemaining = Number(event.detail?.exec_info?.queue_remaining);
        if (Number.isFinite(queueRemaining)) state.comfyQueueRemaining = Math.max(0, queueRemaining);
        setApiConnected(event.detail !== null);
        renderSystemStatusSummary();
      });

      state.disconnectedControlObserver?.disconnect();
      const observer = new MutationObserver(mutations => {
        if (state.apiConnected) return;
        for (const mutation of mutations) {
          if (mutation.type === "childList") {
            mutation.addedNodes.forEach(node => {
              if (node.nodeType === Node.ELEMENT_NODE) freezeDisconnectedControls(node);
            });
          } else if (
            mutation.type === "attributes"
            && isVideoStudioControl(mutation.target)
            && !mutation.target.disabled
            && !isDisconnectedAllowedControl(mutation.target)
          ) {
            state.disconnectedControls.set(mutation.target, false);
            mutation.target.disabled = true;
          }
        }
      });
      state.disconnectedControlObserver = observer;
      scope.own(() => {
        observer.disconnect();
        if (state.disconnectedControlObserver === observer) state.disconnectedControlObserver = null;
      });
      observer.observe(state.panel, {
        attributes: true,
        attributeFilter: ["disabled"],
        childList: true,
        subtree: true,
      });
      setApiConnected(api.socket ? api.socket.readyState === WebSocket.OPEN : true);
    },
  });
}
