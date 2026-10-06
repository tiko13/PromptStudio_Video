// History contains static images. Decode visible thumbnails one at a time,
// releasing the temporary decoder after each frame and bounding the cache.
const cache = new Map();
const pending = [];
let running = false;

async function drain() {
  if (running) return;
  running = true;
  try {
    while (pending.length) {
      const {host, url} = pending.shift();
      if (!host.isConnected) continue;
      let poster = cache.get(url);
      if (poster === undefined) {
        poster = await frame(host.ownerDocument, url);
        cache.set(url, poster);
        if (cache.size > 80) cache.delete(cache.keys().next().value);
      }
      if (!host.isConnected || host.dataset.thumbnailUrl !== url || !poster) continue;
      const image = host.ownerDocument.createElement('img');
      image.src = poster;
      image.alt = '';
      host.replaceChildren(image);
    }
  } finally { running = false; }
}

function frame(doc, url) {
  return new Promise(resolve => {
    const video = doc.createElement('video');
    video.muted = true;
    video.preload = 'auto';
    let settled = false;
    const finish = poster => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      video.onloadeddata = video.onerror = null;
      video.removeAttribute('src');
      video.load();
      resolve(poster);
    };
    const timer = setTimeout(() => finish(null), 8000);
    video.onerror = () => finish(null);
    video.onloadeddata = () => {
      try {
        if (!video.videoWidth || !video.videoHeight) return finish(null);
        const canvas = doc.createElement('canvas');
        canvas.width = 192;
        canvas.height = Math.max(1, Math.round(192 * video.videoHeight / video.videoWidth));
        // Bound unusual portrait dimensions as well as ordinary landscapes.
        if (canvas.height > 192) { canvas.width = Math.max(1, Math.round(192 * video.videoWidth / video.videoHeight)); canvas.height = 192; }
        canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
        finish(canvas.toDataURL('image/jpeg', .7));
      } catch { finish(null); }
    };
    video.src = url;
  });
}

/** Returns a cleanup function for cards removed from history. */
export function observeGenerationThumbnail(host, url) {
  host.dataset.thumbnailUrl = url;
  const observer = new host.ownerDocument.defaultView.IntersectionObserver(entries => {
    if (!entries.some(entry => entry.isIntersecting)) return;
    observer.disconnect();
    pending.push({host, url});
    void drain();
  });
  observer.observe(host);
  return () => observer.disconnect();
}
