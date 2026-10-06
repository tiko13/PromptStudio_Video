import { app } from "/scripts/app.js";
import { api } from "/scripts/api.js";

app.registerExtension({
  name: "PromptStudio.Video.ReferenceAdapterType",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== "PSV_MiniMaxH3ReferenceAdapters") return;
    const created = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function (...args) {
      const result = created?.apply(this, args);
      const type = this.widgets?.find(widget => widget.name === "adapter_type");
      const adapter = this.widgets?.find(widget => widget.name === "adapter");
      if (!type || !adapter) return result;
      type.label = "Type";
      let request = 0;
      const refresh = async () => {
        const version = ++request;
        try {
          const response = await api.fetchApi(`/promptstudio-video/adapters?type=${encodeURIComponent(type.value || "")}`);
          if (!response.ok) return;
          const data = await response.json();
          if (version !== request) return;
          adapter.options.values = ["None", ...data.adapters.filter(item => item.kind !== "lora").map(item => `${item.category}:${item.name}`)];
          // Preserve an old selection so changing Type cannot silently drop an adapter.
          this.setDirtyCanvas?.(true, true);
        } catch { /* Execution validates Type if the catalog is unavailable. */ }
      };
      const callback = type.callback;
      type.callback = function (...values) { callback?.apply(this, values); void refresh(); };
      const configure = this.onConfigure;
      this.onConfigure = function (...values) { const result = configure?.apply(this, values); void refresh(); return result; };
      void refresh();
      return result;
    };
  },
});
