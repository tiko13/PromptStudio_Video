/** Video editing uses integral frames; persisted documents retain their seconds contract. */
export const FPS = 24;
export const SAMPLE_RATE = 48000;
export const MAX_FRAMES = 3592; // largest 17k+5 count below the backend's 3600 limit
export const frame = seconds => Math.round(Number(seconds) * FPS);
export const seconds = frames => frames / FPS;
export const clone = value => structuredClone(value);

// Python round(), used by the existing execution contract. Do not change saved renders.
export function roundEven(value) {
  const floor = Math.floor(value), fraction = value - floor;
  return fraction === .5 ? floor + (floor % 2) : Math.round(value);
}
export function generatedFrames(duration) {
  if (!Number.isFinite(Number(duration)) || Number(duration) <= 0) throw new Error('Duration must be positive.');
  const count = Math.max(5, roundEven(Number(duration) * FPS));
  const aligned = count + ((5 - count % 17 + 17) % 17);
  if (!Number.isFinite(aligned) || aligned > MAX_FRAMES) throw new Error('Duration exceeds the supported generation length.');
  return aligned;
}
const isExtension = project => Boolean(project.extension_source?.parent_project_id && project.extension_source?.parent_generation_id);
export function timingPlan(project) {
  const requested = Number(project.document.duration_seconds ?? 5);
  if (!isExtension(project)) {
    const sampled = generatedFrames(requested);
    return { requested, sampled, delivered: sampled, context: 0, duration: seconds(sampled) };
  }
  if (requested < 5 || requested > 15) throw new Error('Extension requests must stay between 5 and 15 seconds.');
  const context = 39;
  const desired = roundEven(requested * FPS) + context;
  const index = Math.max(0, roundEven((desired - 5) / 17));
  const choices = [Math.max(0, index - 1), index, index + 1].map(k => 17 * k + 5);
  const sampled = choices.sort((a, b) => Math.abs(a - desired) - Math.abs(b - desired) || a - b)[0];
  return { requested, sampled, delivered: sampled - context, context, duration: seconds(sampled - context) };
}
export function formatTime(value, unit = 'timecode') {
  const n = Math.max(0, frame(value));
  if (unit === 'frames') return String(n);
  if (unit === 'seconds') return Number(value).toFixed(6).replace(/\.?0+$/, '') || '0';
  const parts = [Math.floor(n / 86400), Math.floor(n / 1440) % 60, Math.floor(n / 24) % 60, n % 24];
  return parts.map(v => String(v).padStart(2, '0')).join(':');
}
export function parseTime(value, unit = 'timecode') {
  const text = String(value).trim();
  if (!text) throw new Error('Enter a time.');
  if (unit === 'timecode') {
    if (!/^\d+:\d{2}:\d{2}:\d{2}$/.test(text)) throw new Error('Use HH:MM:SS:FF.');
    const [h, m, s, f] = text.split(':').map(Number);
    if (m >= 60 || s >= 60 || f >= FPS) throw new Error('Timecode seconds/minutes must be below 60 and frames below 24.');
    return ((h * 60 + m) * 60 + s) + f / FPS;
  }
  if (!/^\d+(?:\.\d+)?$/.test(text)) throw new Error('Enter a nonnegative number.');
  const number = Number(text);
  if (!Number.isFinite(number) || (unit === 'frames' && !Number.isInteger(number))) throw new Error('Enter a whole frame number.');
  return unit === 'frames' ? seconds(number) : number;
}
export function shotDuration(project, index) {
  return Number(project.document.shots[index + 1]?.start ?? timingPlan(project).duration) - Number(project.document.shots[index].start);
}
function resizeEvents(shot, oldDuration, newDuration, policy) {
  if (Math.abs(oldDuration - newDuration) < 1e-9) return;
  for (const field of ['steps', 'sound_cues', 'audio_clips']) {
    for (const item of shot[field] || []) {
      if (item.start == null || item.end == null) continue;
      if (policy === 'scale' && field !== 'audio_clips') {
        item.start = Math.min(newDuration - 1 / FPS, seconds(frame(item.start / oldDuration * newDuration)));
        item.end = Math.min(newDuration, Math.max(item.start + 1 / FPS, seconds(frame(item.end / oldDuration * newDuration))));
      } else if (Number(item.end) > newDuration + 1e-9) {
        if (policy !== 'trim') throw new Error('This edit would shorten an event or audio clip. Choose Trim overflow or adjust its timing first.');
        const minimum = field === 'audio_clips' ? 1 / SAMPLE_RATE : 1 / FPS;
        item.start = Math.min(Number(item.start), newDuration - minimum);
        item.end = newDuration;
        if (field === 'audio_clips') {
          item.fade_in = Math.min(item.fade_in || 0, item.end - item.start);
          item.fade_out = Math.min(item.fade_out || 0, item.end - item.start);
        }
      }
    }
  }
}
export function guideRange(reference, totalFrames) {
  const start = Number(reference.guide_frame ?? 24);
  if (reference.kind === 'image') return { start, end: start + 1, frames: 1, cropped: false, unknown: false, overflow: start >= totalFrames };
  const sourceEnd = reference.trim_end ?? (Number(reference.duration_seconds) || null);
  if (sourceEnd == null) return { start, end: start + 1, unknown: true, cropped: false, overflow: start >= totalFrames };
  const sourceSeconds = Math.max(0, sourceEnd - Number(reference.trim_start || 0));
  const raw = Math.floor(sourceSeconds * FPS + 1e-9);
  const length = reference.kind === 'video' ? (raw < 5 ? 1 : raw - ((raw - 5) % 17)) : Math.ceil(sourceSeconds * FPS);
  return { start, end: start + length, frames: length, unknown: false,
    cropped: reference.kind === 'video' && length !== raw, overflow: start + length > totalFrames };
}
export function validateTimeline(project) {
  const plan = timingPlan(project);
  let previous = -1;
  project.document.shots.forEach((shot, index) => {
    const start = Number(shot.start);
    if (!Number.isFinite(start) || start <= previous || start >= plan.duration || (!index && start !== 0)) throw new Error('Shot boundaries must increase inside the delivered timeline.');
    previous = start;
    const duration = shotDuration(project, index);
    for (const field of ['steps', 'sound_cues', 'audio_clips']) for (const item of shot[field] || []) {
      if (item.start == null && item.end == null) continue;
      if (!Number.isFinite(Number(item.start)) || !Number.isFinite(Number(item.end)) || item.start < 0 || item.end <= item.start || item.end > duration + 1e-9) throw new Error(`An event in Shot ${index + 1} falls outside the shot.`);
    }
  });
  for (const ref of project.document.references || []) if (ref.roles?.includes('timeline_guide')) {
    const range = guideRange(ref, plan.delivered);
    if (range.start < 0 || range.overflow) throw new Error('A guide extends beyond the delivered timeline. Trim it or move it earlier.');
  }
  return plan;
}
/** Atomic edit: errors leave the caller untouched. Rolls affect adjacent shots; ripple shifts later shots. */
export function editBoundary(project, index, time, { mode = 'roll', events = 'preserve' } = {}) {
  const next = { ...project, document: clone(project.document) }, shots = next.document.shots;
  if (index < 1 || index > shots.length) throw new Error('The first shot starts at zero.');
  const before = timingPlan(project), old = index === shots.length ? before.duration : shots[index].start;
  const target = seconds(frame(time)), delta = target - old;
  if (target < Number(shots[index - 1].start) + 1 / FPS) throw new Error('A shot needs at least one frame.');
  if (mode === 'ripple' || index === shots.length) {
    const requested = before.duration + delta;
    if (isExtension(next) && (requested < 5 || requested > 15)) throw new Error('Extension requests must stay between 5 and 15 seconds.');
    next.document.duration_seconds = requested;
    if (mode === 'ripple') for (let i = index; i < shots.length; i++) shots[i].start = seconds(frame(shots[i].start) + frame(delta));
  } else {
    if (target >= Number(shots[index + 1]?.start ?? before.duration)) throw new Error('A shot needs at least one frame.');
    shots[index].start = target;
  }
  shots.forEach((shot, i) => resizeEvents(shot, shotDuration(project, i), shotDuration(next, i), events));
  validateTimeline(next);
  return next.document;
}
export function changeDuration(project, requested, events = 'preserve') {
  const next = { ...project, document: clone(project.document) };
  if (!Number.isFinite(requested) || requested <= 0) throw new Error('Duration must be positive.');
  if (isExtension(next) && (requested < 5 || requested > 15)) throw new Error('Extension requests must stay between 5 and 15 seconds.');
  next.document.duration_seconds = requested;
  const i = next.document.shots.length - 1;
  resizeEvents(next.document.shots[i], shotDuration(project, i), shotDuration(next, i), events);
  validateTimeline(next);
  return next.document;
}
/** Local, bounded history. External changes invalidate history rather than overwriting newer edits. */
export function createHistory(initial, limit = 60) {
  let present = JSON.stringify(initial), past = [], future = [];
  return {
    record(value) { const next = JSON.stringify(value); if (next === present) return; past.push(present); if (past.length > limit) past.shift(); present = next; future = []; },
    sync(value) { if (JSON.stringify(value) !== present) { present = JSON.stringify(value); past = []; future = []; } },
    undo() { if (!past.length) return null; future.push(present); present = past.pop(); return JSON.parse(present); },
    redo() { if (!future.length) return null; past.push(present); present = future.pop(); return JSON.parse(present); },
    get canUndo() { return !!past.length; }, get canRedo() { return !!future.length; },
  };
}
