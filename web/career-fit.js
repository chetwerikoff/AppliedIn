// Best-match order for Career Ops rows. The server stores the tier and the
// keyword score; this only sorts and badges them. Unclassified stays above
// "other" so a missed label is still visible.
function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function tier(job) {
  if (job.domain === 'ai') return 0;
  if (job.domain === 'it') return 1;
  if (job.domain === 'other') return 3;
  return 2;
}

export function compareMatch(a, b) {
  return tier(a) - tier(b)
    || (Number(b.fit_score) || 0) - (Number(a.fit_score) || 0)
    || (Date.parse(b.posted_at) || 0) - (Date.parse(a.posted_at) || 0);
}

export function domainClass(job) {
  return job.domain === 'other' ? ' is-other' : '';
}

export function domainBadge(job) {
  if (job.domain === 'ai') return '<span class="cb-domain cb-domain-ai">AI</span>';
  if (job.domain === 'other') {
    const reason = escapeHtml(job.domain_reason || '');
    return `<span class="cb-domain cb-domain-other" title="${reason}">Not IT</span>`;
  }
  return '';
}
