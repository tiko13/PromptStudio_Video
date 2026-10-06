import { FPS, frame, formatTime, parseTime } from './timeline-model.js';

/** A transport owns only its listeners/animation, never project content or source media. */
export function mountTransport(host, { duration, position, onPosition, media = null, offset = 0, unit = 'timecode', label = '', range = null, onRange = () => {} }) {
  const doc = host.ownerDocument, win = doc.defaultView;
  const bar = doc.createElement('div'); bar.className = 'psvstudio-transport';
  let playing = false, disposed = false, raf = 0, last = 0, time = Math.max(0, Math.min(duration, position || 0));
  let markIn = Math.min(Math.max(0, range?.in || 0), duration - 1 / FPS);
  let markOut = Math.max(markIn + 1 / FPS, Math.min(duration, range?.out ?? duration));
  let loop = Boolean(range?.loop);
  const button = (name, action) => { const b = doc.createElement('button'); b.type = 'button'; b.className = 'psvstudio-button'; b.textContent = name; b.addEventListener('click', action); bar.append(b); return b; };
  const seek = value => {
    time = Math.max(0, Math.min(duration, frame(value) / FPS));
    if (media && Number.isFinite(media.duration)) media.currentTime = Math.min(media.duration, offset + time);
    paint();
  };
  button('−1 frame', () => { pause(); seek(time - 1 / FPS); });
  const play = button('Play', () => playing ? pause() : start());
  button('+1 frame', () => { pause(); seek(time + 1 / FPS); });
  const input = doc.createElement('input'); input.type = 'text'; input.ariaLabel = `${label} playhead`.trim(); input.className = 'psvstudio-time-entry';
  input.addEventListener('input', () => input.setCustomValidity(''));
  input.addEventListener('change', () => { try { seek(parseTime(input.value, unit)); input.value = formatTime(time, unit); input.setCustomValidity(''); } catch (error) { input.setCustomValidity(error.message); input.reportValidity(); } });
  bar.append(input);
  const rangeText = doc.createElement('small');
  const rangeChanged = () => {
    rangeText.textContent = `In ${formatTime(markIn, unit)} · Out ${formatTime(markOut, unit)}`;
    onRange({ in: markIn, out: markOut, loop });
  };
  button('Set In', () => { markIn = Math.min(frame(time) / FPS, markOut - 1 / FPS); rangeChanged(); });
  button('Set Out', () => { markOut = Math.max(markIn + 1 / FPS, frame(time) / FPS); rangeChanged(); });
  const loopButton = button('Loop range', () => { loop = !loop; loopButton.setAttribute('aria-pressed', String(loop)); rangeChanged(); });
  loopButton.setAttribute('aria-pressed', String(loop));
  button('Clear range', () => { markIn = 0; markOut = duration; rangeChanged(); });
  bar.append(rangeText); rangeChanged();
  const note = doc.createElement('small'); note.textContent = media ? 'Saved take preview · edits require a new render' : 'Timing rehearsal · no rendered media'; bar.append(note);
  const paint = () => { if (doc.activeElement !== input) input.value = formatTime(time, unit); onPosition(time); };
  function pause() { playing = false; play.textContent = 'Play'; media?.pause(); win.cancelAnimationFrame(raf); raf = 0; }
  function tick(now) {
    if (disposed || !host.isConnected) { pause(); return; }
    if (!playing) return;
    time = media ? Math.max(0, media.currentTime - offset) : time + (last ? (now - last) / 1000 : 0); last = now;
    if (time >= markOut || media?.ended) {
      if (loop) { seek(markIn); last = now; if (media?.paused) media.play().catch(pause); }
      else { time = markOut; paint(); pause(); return; }
    }
    paint(); raf = win.requestAnimationFrame(tick);
  }
  async function start() {
    if (time < markIn || time >= markOut) seek(markIn);
    playing = true; last = 0; play.textContent = 'Pause';
    if (media) { seek(time); try { await media.play(); } catch { pause(); note.textContent = 'Preview is not playable. Frame positioning remains available.'; return; } }
    if (playing && !disposed) raf = win.requestAnimationFrame(tick);
  }
  const mediaSeek = () => {
    if (!media) return;
    time = Math.max(0, Math.min(duration, media.currentTime - offset));
    const target = Math.min(media.duration, offset + time);
    if (Number.isFinite(target) && Math.abs(media.currentTime - target) > .001) media.currentTime = target;
    paint();
  };
  const mediaPlay = () => {
    if (!playing) {
      if (time < markIn || time >= markOut) seek(markIn);
      playing = true; last = 0; play.textContent = 'Pause'; raf = win.requestAnimationFrame(tick);
    }
  };
  const mediaPause = () => { if (playing) { if (media?.ended && loop) { seek(markIn); media.play().catch(pause); } else pause(); } };
  const mediaLoaded = () => { if (!disposed) seek(time); };
  media?.addEventListener('seeked', mediaSeek); media?.addEventListener('play', mediaPlay); media?.addEventListener('pause', mediaPause);
  media?.addEventListener('loadedmetadata', mediaLoaded);
  host.append(bar); paint();
  if (media && Number.isFinite(media.duration)) seek(time);
  return { seek, pause, dispose({ pauseMedia = true } = {}) { disposed = true; playing = false; win.cancelAnimationFrame(raf); if (pauseMedia) media?.pause(); media?.removeEventListener('seeked', mediaSeek); media?.removeEventListener('play', mediaPlay); media?.removeEventListener('pause', mediaPause); media?.removeEventListener('loadedmetadata', mediaLoaded); } };
}

const peaksCache = new Map();
/** Bounded, lazy waveform preview. It does not upload audio or alter the mix. */
export async function drawWaveform(canvas, url, start = 0, end = null, sourceDuration = 0) {
  if (!url) return;
  if (!(sourceDuration > 0 && sourceDuration <= 300)) { canvas.title = 'Waveform preview requires a source no longer than five minutes with known duration.'; return; }
  const win = canvas.ownerDocument.defaultView;
  try {
    let request = peaksCache.get(url);
    if (!request) {
      request = (async () => {
        const response = await fetch(url);
        if (!response.ok || Number(response.headers.get('content-length')) > 24 * 1024 * 1024) throw new Error('Waveform source unavailable or too large');
        const reader = response.body.getReader(), chunks = []; let size = 0;
        try { while (true) { const {value, done} = await reader.read(); if (done) break; size += value.length; if (size > 24 * 1024 * 1024) throw new Error('Waveform preview limit'); chunks.push(value); } }
        finally { await reader.cancel(); }
        const data = new Uint8Array(size); let cursor = 0; for (const part of chunks) { data.set(part, cursor); cursor += part.length; }
        const AudioContext = win.AudioContext || win.webkitAudioContext;
        const context = new AudioContext();
        try {
          const buffer = await context.decodeAudioData(data.buffer);
          const channel = buffer.getChannelData(0), count = 2048, peaks = new Float32Array(count);
          for (let i = 0; i < count; i++) for (let j = Math.floor(i * channel.length / count); j < Math.floor((i + 1) * channel.length / count); j++) peaks[i] = Math.max(peaks[i], Math.abs(channel[j]));
          return { peaks, duration: buffer.duration };
        } finally { await context.close(); }
      })();
      peaksCache.set(url, request); if (peaksCache.size > 16) peaksCache.delete(peaksCache.keys().next().value);
    }
    const result = await request;
    if (!canvas.isConnected) return;
    canvas.width = Math.max(32, Math.min(2048, canvas.clientWidth * 2)); canvas.height = 48;
    const ctx = canvas.getContext('2d'); ctx.strokeStyle = '#8de1c3'; ctx.beginPath();
    for (let x = 0; x < canvas.width; x++) {
      const time = start + x / canvas.width * ((end ?? result.duration) - start);
      const value = time < result.duration ? result.peaks[Math.min(2047, Math.floor(time / result.duration * 2048))] : 0;
      ctx.moveTo(x, 24 - value * 22); ctx.lineTo(x, 24 + value * 22);
    }
    ctx.stroke(); canvas.title = 'Source audio waveform';
  } catch { canvas.title = 'Waveform unavailable; audio placement is unchanged.'; }
}
