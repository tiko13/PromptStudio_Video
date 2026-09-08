import { createFeatureController } from "/extensions/ComfyUI_PromptStudio/js/prompt-studio/ui/feature-controller.js";

/** Active editor-document input ownership, independent of project operations. */
export function createVideoDocumentInteractionController({ state, isFileDrag, clearMediaDrag,
  addMediaFiles, clipboardImageFiles, closeSystemStatus, closeVideoDrawer }) {
  function mountMedia(doc, scope) {
  const activeHere = () => state.panel?.ownerDocument === doc && !state.panel.hidden;
  const targetsDirector = event => event.composedPath().some(target =>
    target?.classList?.contains("psvstudio-director-dialog") && target.open
  );
  scope.listen(doc, "dragenter", event => {
    if (!activeHere() || !isFileDrag(event) || targetsDirector(event)) return;
    event.preventDefault();
    state.mediaDropDepth.set(doc, (state.mediaDropDepth.get(doc) || 0) + 1);
    doc.body?.classList.add("psvstudio-media-drag-active");
  }, true);
  scope.listen(doc, "dragover", event => {
    if (!activeHere() || !isFileDrag(event) || targetsDirector(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  }, true);
  scope.listen(doc, "dragleave", event => {
    if (!activeHere() || !isFileDrag(event) || targetsDirector(event)) return;
    const depth = Math.max(0, (state.mediaDropDepth.get(doc) || 0) - 1);
    state.mediaDropDepth.set(doc, depth);
    if (!depth || !event.relatedTarget) clearMediaDrag(doc);
  }, true);
  scope.listen(doc, "drop", event => {
    if (!activeHere() || !isFileDrag(event) || targetsDirector(event)) return;
    event.preventDefault();
    event.stopPropagation();
    clearMediaDrag(doc);
    addMediaFiles(event.dataTransfer.files);
  }, true);
  scope.listen(doc, "paste", event => {
    if (!activeHere() || targetsDirector(event)) return;
    const files = clipboardImageFiles(event);
    if (!files.length) return;
    event.preventDefault();
    event.stopPropagation();
    addMediaFiles(files);
  }, true);
  }
  function mountTransient(ownerDocument, scope) {
  scope.listen(ownerDocument, "pointerdown", event => {
    if (!state.panel || state.panel.hidden || state.panel.ownerDocument !== ownerDocument) return;
    const control = state.panel.querySelector("#psvstudio-kobold-control");
    if (control?.open && !control.contains(event.target)) closeSystemStatus();
  }, { capture: true });
  scope.listen(ownerDocument, "keydown", event => {
    if (
      event.defaultPrevented
      || event.key !== "Escape"
      || !state.panel
      || state.panel.hidden
      || state.panel.ownerDocument !== ownerDocument
      || state.panel.querySelector("dialog[open]")
    ) return;
    if (closeSystemStatus({ restoreFocus: true }) || closeVideoDrawer({ restoreFocus: true })) {
      event.preventDefault();
      event.stopPropagation();
    }
  }, { capture: true });
  }
  return createFeatureController({
    mount(ownerDocument, scope) {
      mountMedia(ownerDocument, scope);
      mountTransient(ownerDocument, scope);
    },
    dispose(ownerDocument) { clearMediaDrag(ownerDocument); },
  });
}
