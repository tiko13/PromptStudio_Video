const CHANNEL_NAME = "promptstudio.video.standalone.v1";
const requestId = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
const status = document.querySelector("#promptstudio-video-standalone-status");
const embeddedHost = document.querySelector("#promptstudio-video-host");
const windowName = window.name || `promptstudio-video-${requestId}`;
window.name = windowName;

let connected = false;
let connecting = false;
let channel = null;
let hostPoll = null;
let fallbackTimer = null;
let failureTimer = null;

function setStatus(message) {
  if (status) status.textContent = message;
}

function finishConnection() {
  connected = true;
  channel?.close();
  channel = null;
  if (hostPoll) window.clearInterval(hostPoll);
  if (fallbackTimer) window.clearTimeout(fallbackTimer);
  if (failureTimer) window.clearTimeout(failureTimer);
  hostPoll = fallbackTimer = failureTimer = null;
}

async function connectToHost(host) {
  if (connected || connecting) return connected;
  connecting = true;
  try {
    if (!host || host.closed || host.location.origin !== window.location.origin) return false;
    const attached = await host.__promptstudioVideoStudioHost?.attach?.(window);
    if (!attached) return false;
    finishConnection();
    return true;
  } catch (_) {
    return false;
  } finally {
    connecting = false;
  }
}

async function connectEmbeddedHost() {
  if (!connected) await connectToHost(embeddedHost?.contentWindow);
}

function startEmbeddedHost() {
  if (connected || !embeddedHost || embeddedHost.hasAttribute("src")) return;
  setStatus("Starting a private ComfyUI workflow host…");
  embeddedHost.src = "/";
  hostPoll = window.setInterval(connectEmbeddedHost, 500);
}

if (!await connectToHost(window.opener)) {
  if (typeof BroadcastChannel === "function") {
    channel = new BroadcastChannel(CHANNEL_NAME);
    channel.addEventListener("message", event => {
      if (event.data?.requestId !== requestId) return;
      if (event.data.type === "connected") finishConnection();
      else if (event.data.type === "failed") startEmbeddedHost();
    });
    channel.postMessage({ type: "connect", requestId, windowName });
  }
  fallbackTimer = window.setTimeout(startEmbeddedHost, 1500);
}

embeddedHost?.addEventListener("load", connectEmbeddedHost);
failureTimer = window.setTimeout(() => {
  if (connected) return;
  if (hostPoll) window.clearInterval(hostPoll);
  hostPoll = null;
  setStatus("Video Studio could not start its ComfyUI workflow host. Refresh after ComfyUI has finished loading.");
}, 15000);

window.addEventListener("beforeunload", () => {
  channel?.close();
  if (hostPoll) window.clearInterval(hostPoll);
  if (fallbackTimer) window.clearTimeout(fallbackTimer);
  if (failureTimer) window.clearTimeout(failureTimer);
}, { once: true });
