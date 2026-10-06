// Presentation only: the backend preview remains authoritative for patch/replacement semantics.
import { timingPlan } from './timeline-model.js';

const shotFields = ['composition', 'subjects', 'environment', 'lighting', 'transition', 'steps', 'camera', 'sounds', 'visible_text', 'notes', 'sound_cues', 'audio_clips'];
const projectFields = ['main_description', 'style', 'overall_soundscape', 'non_diegetic_music', 'complete_silence', 'task_types', 'subject_definitions', 'summary', 'retention_analysis', 'prompt_override'];
const labels = {
  main_description: 'Main description', overall_soundscape: 'Overall soundscape', non_diegetic_music: 'Background music',
  complete_silence: 'Complete silence', task_types: 'Reference tasks', subject_definitions: 'Subject definitions',
  retention_analysis: 'Reference continuity', prompt_override: 'Manual prompt override', steps: 'Action and dialogue sequence',
  visible_text: 'Visible text', sound_cues: 'Sound cues', audio_clips: 'Exact audio', speaker_id: 'Speaker ID',
  utterance_id: 'Dialogue link', trim_start: 'Source start', trim_end: 'Source end', timing_explicit: 'Explicit timing',
};
const label = key => labels[key] || key.replaceAll('_', ' ').replace(/^./, c => c.toUpperCase());
const empty = value => value == null || value === '' || (Array.isArray(value) && !value.length)
  || (typeof value === 'object' && !Array.isArray(value) && Object.values(value).every(empty));
const stable = value => JSON.stringify(value, (key, item) => {
  // Generated cue/step IDs are bookkeeping, not authored changes.
  if (key === 'id') return undefined;
  if (item && !Array.isArray(item) && typeof item === 'object') return Object.fromEntries(Object.entries(item).sort(([a], [b]) => a.localeCompare(b)));
  return item;
});
const same = (a, b) => (empty(a) && empty(b)) || stable(a) === stable(b);
export const reviewTime = value => `${Number(Number(value).toFixed(3))} s`;

export function formatReviewValue(value, field = '') {
  if (empty(value)) return 'Not specified';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (typeof value !== 'object') return String(value);
  if (field === 'steps' && Array.isArray(value)) return value.map((step, index) => {
    const timing = step.start != null && step.end != null ? ` · ${reviewTime(step.start)}–${reviewTime(step.end)} within shot` : '';
    const title = step.type === 'dialogue' ? (step.performance === 'singing' ? 'Singing' : 'Dialogue') : 'Action';
    const details = Object.entries(step).filter(([key, item]) => !['id', 'type', 'text', 'start', 'end', 'timing_explicit'].includes(key) && !empty(item) && item !== false)
      .map(([key, item]) => `${label(key)}: ${formatReviewValue(item)}`).join(' · ');
    return `${index + 1}. ${title}${timing}${details ? `\n${details}` : ''}\n${step.text || 'Not specified'}`;
  }).join('\n\n');
  if (field === 'camera') {
    return [value.type || 'Movement not specified',
      value.amplitude && `Amplitude: ${value.amplitude}`, value.speed && `Speed: ${value.speed}`,
      value.target && `Target: ${value.target}`].filter(Boolean).join('\n');
  }
  if (Array.isArray(value)) return value.map(item => `• ${formatReviewValue(item)}`).join('\n');
  return Object.entries(value).filter(([key]) => key !== 'id').map(([key, item]) => `${label(key)}: ${formatReviewValue(item, key)}`).join('\n');
}

function fieldsFor(before, after, fields, added = false, removed = false) {
  return fields.map(key => {
    const oldValue = before?.[key], value = after?.[key];
    const changed = !same(oldValue, value);
    const status = removed ? 'Removed' : added ? (empty(value) ? 'Not specified' : 'Added')
      : !changed ? 'Unchanged' : empty(value) ? 'Cleared' : empty(oldValue) ? 'Added' : 'Changed';
    return { key, name: label(key), status, changed: added || removed || changed,
      value: formatReviewValue(removed ? oldValue : value, key),
      before: changed && !added && !removed && !empty(oldValue) ? formatReviewValue(oldValue, key) : '' };
  });
}

function range(document, index, extensionSource) {
  const shots = document.shots || [];
  const start = Number(shots[index].start || 0);
  const end = Number(shots[index + 1]?.start ?? timingPlan({document, extension_source: extensionSource}).duration);
  return `${reviewTime(start)}–${reviewTime(end)} · ${reviewTime(end - start)} duration`;
}

export function buildProposalReview(before, after, proposal, extensionSource = null) {
  const oldShots = before.shots || [], shots = after.shots || [];
  const notices = [];
  const oldCuts = oldShots.slice(1).map(shot => Number(shot.start));
  const newCuts = shots.slice(1).map(shot => Number(shot.start));
  for (const cut of newCuts.filter(time => !oldCuts.includes(time))) notices.push(`Cut added at ${reviewTime(cut)}.`);
  for (const cut of oldCuts.filter(time => !newCuts.includes(time))) notices.push(`Cut removed at ${reviewTime(cut)}.`);
  if (oldShots.length !== shots.length) notices.unshift(`Shot count: ${oldShots.length} → ${shots.length}.`);
  const project = fieldsFor(before, after, projectFields).filter(field => field.changed);
  const cards = shots.map((shot, index) => {
    const oldIndex = oldShots.findIndex(item => item.id === shot.id);
    const original = oldShots[oldIndex];
    const replacement = proposal.operations?.some(op => op.op === 'update_shot' && op.shot_id === shot.id && op.replace);
    const fields = fieldsFor(original, shot, shotFields, !original);
    const timing = range(after, index, extensionSource);
    const oldTiming = original ? range(before, oldIndex, extensionSource) : '';
    const moved = oldIndex >= 0 && oldIndex !== index;
    const status = !original ? 'New' : replacement ? 'Replaced' : fields.some(field => field.changed) || oldTiming !== timing || moved ? 'Updated' : 'Unchanged';
    return { title: moved ? `Shot ${oldIndex + 1} → Shot ${index + 1}` : `Shot ${index + 1}`, status,
      timing, oldTiming: oldTiming !== timing ? oldTiming : '',
      transition: index === 0 ? 'Opening shot · no cut' : `Cut at ${reviewTime(shot.start)}`,
      fields: fields.filter(field => !['sound_cues', 'audio_clips'].includes(field.key) || field.changed || !empty(shot[field.key])) };
  });
  oldShots.forEach((shot, index) => {
    if (shots.some(item => item.id === shot.id)) return;
    cards.push({title: `Shot ${index + 1}`, status: 'Removed', timing: range(before, index, extensionSource),
      oldTiming: '', transition: 'Removed from the original timeline', fields: fieldsFor(shot, null, shotFields, false, true).filter(field => field.value !== 'Not specified')});
  });
  const protectedChanges = (proposal.protected_content_changes || []).flatMap(change => ['removed', 'added'].flatMap(kind =>
    (change[kind] || []).map(value => ({name: `${label(change.kind)} · ${label(kind)}`, value: formatReviewValue(value)}))));
  return {version: 1, notices, project, cards, protectedChanges};
}

export function renderProposalReview(review, owner) {
  const node = (tag, className, text) => {
    const item = owner.createElement(tag); item.className = className;
    if (text != null) item.textContent = text;
    return item;
  };
  const root = node('div', 'psvstudio-proposal-review');
  const details = (title, content) => {
    const element = node('details', 'psvstudio-review-details');
    element.append(node('summary', '', title), content); return element;
  };
  const fieldNode = field => {
    const item = node('div', `psvstudio-review-field${field.changed ? ' is-changed' : ''}`);
    const heading = node('div', 'psvstudio-review-field-heading');
    heading.append(node('strong', '', field.name), node('span', 'psvstudio-review-badge', field.status));
    item.append(heading, node('p', '', field.status === 'Cleared' ? 'Cleared — no value remains.' : field.value));
    if (field.before) item.append(details(`Previous ${field.name.toLowerCase()}`, node('p', '', field.before)));
    return item;
  };
  root.append(node('p', 'psvstudio-help', 'Proposed result at review time. Applying checks that the document has not changed.'));
  if (review.notices.length) {
    const summary = node('div', 'psvstudio-review-structure');
    summary.append(node('strong', '', 'Timeline changes'));
    for (const notice of review.notices) summary.append(node('p', '', notice));
    root.append(summary);
  }
  if (review.project.length) {
    const section = node('section', 'psvstudio-review-shot');
    section.append(node('h3', '', 'Project'));
    for (const field of review.project) section.append(fieldNode(field));
    root.append(section);
  }
  for (const card of review.cards) {
    const section = node('section', `psvstudio-review-shot${card.status === 'Removed' ? ' is-removed' : ''}`);
    const head = node('div', 'psvstudio-review-shot-heading');
    head.append(node('h3', '', card.title), node('span', 'psvstudio-review-badge', card.status));
    section.append(head, node('p', 'psvstudio-review-timing', card.timing), node('p', 'psvstudio-help', card.transition));
    if (card.oldTiming) section.append(node('p', 'psvstudio-help', `Previously: ${card.oldTiming}`));
    for (const field of card.fields.filter(item => item.changed)) section.append(fieldNode(field));
    const unchanged = card.fields.filter(item => !item.changed);
    if (unchanged.length) {
      const content = node('div', '');
      for (const field of unchanged) content.append(fieldNode(field));
      section.append(details(`Unchanged details (${unchanged.length})`, content));
    }
    root.append(section);
  }
  if (review.protectedChanges.length) {
    const content = node('section', 'psvstudio-review-shot');
    content.append(node('h3', '', 'Protected content changes'));
    for (const field of review.protectedChanges) content.append(node('strong', '', field.name), node('p', '', field.value));
    root.append(content);
  }
  return root;
}
