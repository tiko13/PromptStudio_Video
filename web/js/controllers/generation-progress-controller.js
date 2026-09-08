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
        if (!markGenerationExecuting(id)) return;
        updateProgress(id, { phase: "generating" });
      });
      scope.listen(api, "progress", event => {
        const id = promptId(event);
        if (!markGenerationExecuting(id)) return;
        if (state.generationProgress.get(id)?.phase === "finalizing") {
          renderGenerations();
          return;
        }
        updateProgress(id, {
          phase: "generating",
          value: Number(event.detail?.value),
          max: Number(event.detail?.max),
        });
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
        if (!markGenerationExecuting(id)) return;
        const current = state.generationProgress.get(id) || {};
        const samplerComplete = Number.isFinite(current.value) && Number.isFinite(current.max)
          && current.max > 0 && current.value >= current.max;
        const node = event?.detail && typeof event.detail === "object" ? event.detail.node : event?.detail;
        updateProgress(id, { phase: node == null || samplerComplete ? "finalizing" : "generating" });
        renderGenerations();
      });
      scope.listen(api, "progress_state", event => {
        const id = promptId(event);
        if (!markGenerationExecuting(id)) return;
        const progress = runningProgress(event);
        const current = state.generationProgress.get(id) || {};
        if (current.phase !== "finalizing") {
          updateProgress(id, { phase: progress ? "generating" : current.phase || "generating", ...(progress || {}) });
        }
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
