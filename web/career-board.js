// This element stays mounted outside #pane so routine pipeline polling cannot
// close source settings, lose selections or interrupt a button press.
import { auth } from './auth.js';
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const date = value => {
  const d = new Date(/^\d{4}-\d{2}-\d{2}$/.test(value || '') ? value + 'T12:00:00' : value);
  return value && Number.isFinite(+d) ? d.toLocaleDateString(undefined, {month:'short', day:'numeric'}) : 'Date unavailable';
};
const plainWhy = value => String(value || '').replace(/\s*\(\[[^\]]+\]\(https?:\/\/[^)]+\)\)/g, '').replace(/\[([^\]]+)\]\(https?:\/\/[^)]+\)/g, '$1').trim();
const labels = {new:'New', pipeline:'In pipeline', handled:'Already handled', dismissed:'Dismissed'};
class CareerBoard extends HTMLElement {
  static observedAttributes = ['company', 'query', 'hidden'];
  connectedCallback() {
    this.selected = new Set(); this.data = null; this.busy = false; this.page = 1;
    this.pendingApplications = new Map();
    this.innerHTML = `<section class="cb-panel cb-page"><header class="cb-heading"><div><h2>Discover jobs</h2><p>Search your preferred job boards, then review the roles that fit.</p></div><button type="button" class="btn cb-pipeline">View pipeline →</button></header>
      <div class="cb-body">
        <aside class="cb-search-column" aria-label="Search preferences"><div class="cb-network"><h3 class="cb-search-title">Your search</h3>
          <div class="cb-search-brief"><label for="cb-search-query">Interests <small>Optional</small></label><textarea id="cb-search-query" rows="2" maxlength="600" placeholder="AI infrastructure, developer tools, fintech…"></textarea></div>
          <div class="cb-search-options"><label>Search with <select class="cb-client"><option value="claude">Claude</option><option value="codex">Codex</option></select></label><fieldset class="cb-board-picker"><legend>Job boards</legend><div class="cb-ats">${['greenhouse','lever','ashby','workday','icims'].map(name => `<label><input type="checkbox" value="${name}" ${name !== 'icims' ? 'checked' : ''}>${({greenhouse:'Greenhouse',lever:'Lever',ashby:'Ashby',workday:'Workday',icims:'iCIMS'})[name]}</label>`).join('')}</div></fieldset></div>
          <div class="cb-search-fields"><label>Roles to find<textarea class="cb-roles" rows="3" placeholder="Software Engineer, Platform Engineer" aria-describedby="cb-filter-help"></textarea></label><label>Locations<textarea class="cb-locations" rows="3" placeholder="Seattle, California, Remote (US)"></textarea></label></div>

          <div class="cb-toolbar"><label>Posted within <select class="cb-days"><option value="1">24 hours</option><option value="3">3 days</option><option value="7">7 days</option><option value="30" selected>30 days</option><option value="90">90 days</option><option value="365">1 year</option></select></label><span class="cb-search-hint">Uses your selected client and preferences</span><button type="button" class="btn cb-network-start" disabled>Search jobs</button><button type="button" class="btn cb-network-continue" hidden>Continue scan</button><button type="button" class="btn cb-network-stop" hidden>Stop scan</button></div>
          <details class="cb-refine"><summary>More preferences</summary><p id="cb-filter-help" class="cb-note">Separate roles and locations with commas. Leave a field blank to include all.</p><label class="cb-check"><input type="checkbox" class="cb-undated" checked> Include jobs without a posting date</label><label>Exclude titles containing<input class="cb-exclude" placeholder="Intern, Sales Engineer"></label><div class="cb-toolbar"><label>Companies per source <select class="cb-depth"><option value="150">150 per batch</option><option value="500">500 per batch</option><option value="1000">1,000 per batch</option><option value="0">Entire directory</option></select></label><button type="button" class="btn cb-reset-filters">Use saved preferences</button></div><p class="cb-note">Continue scans the next batch. Entire-directory scans can take hours; completed results are saved and can be continued.</p></details>

        </div>
        <details class="cb-settings"><summary>Tracked companies & automation</summary>
        <div class="cb-toolbar"><p class="cb-context">Search company feeds, then prepare roles for your approval queue.</p><button type="button" class="btn cb-scan">Search now</button></div>

          <form><p>Search by interests to discover employers, or choose company feeds to monitor.</p><label><input name="scheduled_interests" type="checkbox"> Run an additional web search every 6 hours with my selected client</label>
            <label><input name="scheduled" type="checkbox"> Search every 6 hours while AppliedIn is running and unpaused</label>
            <label><input name="auto_prepare" type="checkbox"> Automatically prepare matches using my job preferences</label>
            <p class="cb-note">Preparation uses your AI model and per-company limit, up to 50 roles per scan. Every new application waits for your approval.</p>
            <input type="search" class="cb-source-search" placeholder="Find a company source…" aria-label="Find a company source">
            <div class="cb-sources"></div><button type="submit" class="btn">Save sources & automation</button><span class="cb-saved" role="status"></span>
          </form>
        </details>
        </aside><section class="cb-results" aria-label="Discovered jobs"><section class="cb-applications" hidden aria-label="Selected applications"><header><div><h3>Your applications</h3><p class="cb-application-summary" role="status"></p></div><span class="cb-application-refresh">Updates every 6 seconds</span></header><div class="cb-application-list"></div></section><div class="cb-results-heading"><h3>Matching jobs</h3><span>Choose roles to score, tailor, and review.</span></div>
        <span class="cb-count" hidden></span><p class="cb-message" role="status" aria-live="polite"></p>
        <div class="cb-coverage" hidden aria-live="polite"></div>
        <details class="cb-progress" hidden><summary>Search activity</summary><ol aria-label="Search progress"></ol></details>
        <div class="cb-toolbar cb-filters"><label>Show <select class="cb-state"><option value="network">Latest search</option><option value="interest">Earlier interest searches</option><option value="new">All new jobs</option><option value="pipeline">In pipeline</option><option value="dismissed">Dismissed</option><option value="all">All results</option></select></label>
          <label>Sort <select class="cb-sort"><option value="found">Recently found</option><option value="posted">Recently posted</option><option value="company">Company</option></select></label>
          <span class="cb-result-count"></span>
        </div>
        <div class="cb-toolbar cb-actions"><label><input class="cb-all" type="checkbox"> Select visible</label><span class="cb-selection">0 selected</span><button type="button" class="btn cb-apply" disabled hidden title="Score, tailor, and submit only the selected roles">Apply selected</button><button type="button" class="btn cb-prepare" disabled>Score & tailor selected</button><button type="button" class="btn cb-dismiss" disabled>Dismiss</button></div>
        <p class="cb-apply-help" hidden>Apply selected scores, tailors, and submits. Score & tailor stops for review.</p><div class="cb-list"></div><div class="cb-pagination"><button type="button" class="btn cb-prev">Previous</button><span></span><button type="button" class="btn cb-next">Next</button></div>

        </section>
      </div></section>`;
    this.mode = 'network';
    this.$('.cb-client').onchange = async () => {
      try { await this.request('/provider', {provider:this.$('.cb-client').value}); }
      catch (error) { this.message(error.message, true); }
    };
    this.$('.cb-network-start').onclick = () => this.network(false);
    this.$('.cb-network-continue').onclick = () => this.network(true);
    this.$('.cb-network-stop').onclick = async () => {
      try { await this.request('/network/stop', {}); this.message('Stopping scan; completed matches are saved.'); }
      catch (error) { this.message(error.message, true); }
    };
    this.$('.cb-reset-filters').onclick = () => { this.seedNetwork(null); this.paintStatus(); };
    this.$('.cb-network').oninput = () => this.paintStatus();
    this.$('.cb-scan').onclick = () => this.scan();
    this.$('#cb-search-query').onkeydown = event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); this.network(false); } };
    this.$('.cb-pipeline').onclick = () => this.dispatchEvent(new CustomEvent('career-pipeline', {bubbles:true, detail:{company:this.preparedCompany || this.getAttribute('company') || ''}}));
    this.$('form').onsubmit = event => { event.preventDefault(); this.save(); };
    this.$('.cb-source-search').oninput = event => {
      const q = event.target.value.toLowerCase();
      this.querySelectorAll('.cb-source').forEach(el => el.hidden = !el.textContent.toLowerCase().includes(q));
    };
    for (const name of ['.cb-state', '.cb-sort']) this.$(name).onchange = () => { this.page = 1; this.selected.clear(); this.renderRows(); this.paintStatus(); };
    this.$('.cb-all').onchange = event => {
      for (const row of this.visibleRows || []) if (row.state === 'new') {
        event.target.checked ? this.selected.add(row.id) : this.selected.delete(row.id);
      }
      this.renderRows();
    };
    this.$('.cb-list').onchange = event => {
      const id = event.target.dataset.pick;
      if (id) { event.target.checked ? this.selected.add(id) : this.selected.delete(id); this.paintSelection(); }
    };
    this.$('.cb-apply').onclick = () => this.act('apply');
    this.$('.cb-application-list').onpointerdown = () => { this.applicationPointerUntil = Date.now() + 500; };
    this.$('.cb-application-list').onclick = event => {
      const button = event.target.closest('[data-application-company]');
      if (button) this.dispatchEvent(new CustomEvent('career-pipeline', {bubbles:true, detail:{company:button.dataset.applicationCompany}}));
    };
    this.$('.cb-prepare').onclick = () => this.act('prepare');
    this.$('.cb-dismiss').onclick = () => this.act('dismiss');
    this.$('.cb-prev').onclick = () => { this.page--; this.renderRows(); };
    this.$('.cb-next').onclick = () => { this.page++; this.renderRows(); };
    this.refresh();
    this.timer = setInterval(() => { if (!this.hidden) this.refresh(); }, 6000);
  }
  disconnectedCallback() { clearInterval(this.timer); this.progressController?.abort(); }
  attributeChangedCallback(name, oldValue, newValue) {
    if (oldValue === newValue || !this.data) return;
    if (name === 'hidden') {
      if (this.hidden) this.progressController?.abort(); else this.refresh();
      return;
    }
    this.selected.clear(); this.page = 1; this.renderRows(); this.paintStatus();
  }
  $(selector) { return this.querySelector(selector); }
  async request(path = '', body) {
    const cfg = window.APPLIEDIN_CONFIG || {};
    if (cfg.demo === true || new URLSearchParams(location.search).has('demo')) throw new Error('Career Ops is available when connected to your local AppliedIn server.');
    const response = await fetch((cfg.apiUrl || '').replace(/\/$/, '') + '/career-ops' + path, {
      method: body === undefined ? 'GET' : 'POST', headers: {'Content-Type':'application/json', ...auth.header()},
      ...(body === undefined ? {} : {body: JSON.stringify(body)})
    });
    const result = await response.json();
    if (!response.ok) throw new Error(response.status === 404 ? 'The search service is updating. Refresh this page and try again.' : typeof result.detail === 'string' ? result.detail : 'Could not complete this action. Try again.');
    return result;
  }
  async refresh() {
    if (this.loading || this.busy) return;
    this.loading = true;
    try {
      const data = await this.request();
      if (!this.data) {
        this.seedNetwork(data.network_filters, data.preferences);
        if (!data.last_network && data.jobs.length) this.$('.cb-state').value = 'new';
        this.$('[name=scheduled]').checked = data.settings.scheduled;
        this.$('[name=scheduled_interests]').checked = !!data.settings.scheduled_interests;
        this.$('.cb-client').value = data.network_filters?.provider || data.settings.search_provider || 'claude';
        this.$('[name=auto_prepare]').checked = data.settings.auto_prepare;
        this.$('.cb-sources').innerHTML = data.sources.map(c => `<label class="cb-source"><input type="checkbox" name="company" value="${esc(c.name)}" ${data.settings.companies.includes(c.name) ? 'checked' : ''}>${esc(c.name)}</label>`).join('');
      }
      const changed = JSON.stringify(this.data?.jobs) !== JSON.stringify(data.jobs);
      const searchChanged = this.data?.last_search?.started_at !== data.last_search?.started_at || this.data?.last_network?.started_at !== data.last_network?.started_at;
      this.data = data;
      this.applicationRefreshFailed = false;
      for (const row of data.jobs) if (row.application) this.pendingApplications.delete(row.id);
      this.paintApplications();
      this.dispatchEvent(new CustomEvent('career-sources', {bubbles:true, detail:{companies: [...new Set([...data.settings.companies, ...data.jobs.map(j => j.company)])]}}));
      this.paintStatus();
      this.streamProgress();
      // Updating only when rows change keeps native dropdowns and selection stable.
      if (changed || searchChanged) this.renderRows();
    } catch (error) { this.applicationRefreshFailed = true; this.paintApplications(); this.message(error.message, true); }
    finally { this.loading = false; }
  }
  paintApplications() {
    const jobs = new Map((this.data?.jobs || []).filter(r => r.application).map(r => [r.id, r]));
    for (const [id, row] of this.pendingApplications) if (!jobs.has(id)) jobs.set(id, row);
    const active = p => ['starting', 'queued', 'score', 'tailor', 'waiting', 'apply'].includes(p);
    const rows = [...jobs.values()].sort((a,b) => Number(active(b.application.phase)) - Number(active(a.application.phase)) || (b.application.requested_at || '').localeCompare(a.application.requested_at || ''));
    const panel = this.$('.cb-applications'); panel.hidden = !rows.length;
    if (!rows.length) return;
    const counts = rows.reduce((n,r) => { const p = r.application.phase; n[active(p) ? 'active' : p === 'applied' ? 'applied' : 'attention']++; return n; }, {active:0, applied:0, attention:0});
    this.$('.cb-application-summary').textContent = [counts.active && `${counts.active} in progress`, counts.applied && `${counts.applied} applied`, counts.attention && `${counts.attention} need attention`].filter(Boolean).join(' · ');
    this.$('.cb-application-refresh').textContent = this.applicationRefreshFailed ? 'Connection interrupted · retrying…' : 'Updates every 6 seconds';
    // A poll must not replace a button between pointerdown and click, or steal
    // keyboard focus while the user is opening a role's pipeline.
    if (Date.now() < (this.applicationPointerUntil || 0) || this.$('.cb-application-list').contains(document.activeElement)) return;
    const steps = ['Queued', 'Score', 'Tailor', 'Apply'];
    this.$('.cb-application-list').innerHTML = rows.slice(0, 50).map(r => {
      const a = r.application, index = {starting:0, queued:0, score:1, tailor:2, waiting:3, apply:3, applied:4}[a.phase];
      const requested = Date.parse(a.requested_at), elapsed = Number.isFinite(requested) ? Math.max(0, Math.floor((Date.now() - requested) / 1000)) : null;
      const duration = elapsed === null ? '' : elapsed < 60 ? `${elapsed}s` : `${Math.floor(elapsed / 60)}m ${elapsed % 60}s`;
      const stamp = a.applied_at ? new Date(a.applied_at).toLocaleString(undefined, {month:'short', day:'numeric', hour:'numeric', minute:'2-digit'}) : '';
      return `<article class="cb-application ${active(a.phase) ? 'is-active' : a.phase === 'applied' ? 'is-applied' : 'needs-attention'}"><div class="cb-application-top"><div><strong>${esc(r.title)}</strong><p>${esc(r.company)}</p></div><span class="cb-application-state">${active(a.phase) ? '<i class="cb-live-dot" aria-hidden="true"></i>' : ''}${esc(a.label)}</span></div>
        ${index !== undefined ? `<ol class="cb-application-steps" aria-label="Application stages">${steps.map((s,i) => `<li class="${i < index ? 'done' : i === index ? 'current' : ''}" ${i === index ? 'aria-current="step"' : ''}>${i < index ? '✓ ' : ''}${s}</li>`).join('')}</ol>` : ''}
        ${a.detail ? `<p class="cb-application-detail">${esc(a.detail)}</p>` : ''}<footer><span>${a.phase === 'applied' ? (stamp ? `Applied ${esc(stamp)}` : 'Application recorded') : active(a.phase) ? `${duration ? `Requested ${duration} ago · ` : ''}Not yet confirmed submitted` : 'Open the pipeline to review the outcome'}${a.match_score != null ? ` · Match ${esc(a.match_score)}/10` : ''}</span><button type="button" class="btn" data-application-company="${esc(r.company)}">${active(a.phase) || a.phase === 'applied' ? 'View in pipeline' : 'Review in pipeline'} →</button></footer></article>`;
    }).join('') + (rows.length > 50 ? '<p class="cb-note">Showing the 50 most recent applications, with active roles first. View pipeline for all roles.</p>' : '');
  }
  message(text, error = false) {
    this.$('.cb-message').hidden = !text; this.$('.cb-message').textContent = text; this.$('.cb-message').classList.toggle('cb-error', error);
  }
  paintStatus() {
    if (!this.data) return;
    const company = this.getAttribute('company') || '';
    const supported = !company || this.data.sources.some(s => s.name === company);
    const scan = this.$('.cb-scan'); scan.disabled = this.busy || this.data.running || (!company && !!this.data.error);
    scan.textContent = this.data.running ? 'Searching…' : company ? `Search ${company}` : 'Search selected sources';
    this.$('.cb-context').textContent = company ? `More jobs at ${company}. Prepared roles join this company’s application queue.` : 'Search company feeds, then prepare roles for your approval queue.';
    const elapsed = this.data.active_search?.started_at ? Math.max(0, Math.floor((Date.now() - Date.parse(this.data.active_search.started_at)) / 1000)) : 0;
    this.$('.cb-list').setAttribute('aria-busy', String(!!this.data.running));
    const latest = [this.data.last_scan, this.data.last_search, this.data.last_network].filter(Boolean).sort((a,b) => (b.started_at || '').localeCompare(a.started_at || ''))[0];
    const receipt = this.$('.cb-state').value === 'network' ? this.data.last_network : this.$('.cb-state').value === 'interest' ? this.data.last_search : latest;
    this.paintNetwork();
    let status = this.data.running ? (this.data.active_search?.kind === 'feeds' ? 'Searching public feeds…' : `Searching for matching roles and companies${elapsed ? ` · ${elapsed}s` : ''}. This can take 1–2 minutes…`) : receipt ? `${receipt.found} postings read · ${receipt.added} added to board · ${receipt.companies} source${receipt.companies === 1 ? '' : 's'} · ${date(receipt.finished_at)}` : 'Choose Search jobs to scan public company boards.';
    if (this.data.running && this.data.progress?.events?.length) status = `${this.data.progress.events.at(-1).message} · ${elapsed}s`;
    this.paintProgress();
    if (!this.data.running && !receipt) status = '';
    if (!supported && !this.data.running) status += ` ${company} uses web search because no direct feed is connected.`;
    if (receipt?.kind === 'web' && !this.data.running) status = `${receipt.found} match${receipt.found === 1 ? '' : 'es'} · ${receipt.companies} compan${receipt.companies === 1 ? 'y' : 'ies'}. ${receipt.summary || ''}`;
    const errors = this.data.running ? [] : receipt?.errors || [];
    if (!this.data.running && receipt?.kind !== 'web' && receipt?.kind !== 'network' && receipt?.sources?.length) status += ' ' + receipt.sources.slice(0, 4).map(s => `${s.company}: ${s.found} postings`).join(' · ') + '.';
    if (!this.data.running && receipt?.kind === 'network') status = `${receipt.found} matches saved this batch · ${receipt.read} postings read · ${receipt.unreachable} boards unreachable. ${receipt.complete ? 'Reached the end of the selected directories.' : 'Partial scan — continue to check more companies.'}`;
    if (errors.length && receipt?.kind !== 'network') status += ' ' + errors.slice(0, 3).map(e => `${e.company}: ${e.error}`).join(' ');
    this.message(status, !!errors.length && (receipt?.kind !== 'network' || !receipt.found));
    this.$('.cb-count').textContent = `${this.rows().filter(r => r.state === 'new').length} new${company ? ` · ${company}` : ''}`;
  }
  paintProgress() {
    const latest = [this.data.last_search, this.data.last_scan, this.data.last_network].filter(Boolean).sort((a,b) => (b.started_at || '').localeCompare(a.started_at || ''))[0];
    const shown = this.$('.cb-state').value === 'network' ? this.data.last_network : this.$('.cb-state').value === 'interest' ? this.data.last_search : latest;
    const progress = this.data.running ? this.data.progress : shown?.progress;
    const box = this.$('.cb-progress'), events = progress?.events || [];
    box.hidden = !events.length;
    if (!events.length) return;
    if (this.progressRun !== progress.run_id) {
      this.progressRun = progress.run_id; box.open = false;
    }
    const list = box.querySelector('ol');
    const signature = progress.run_id + ':' + events.at(-1).seq;
    if (signature === this.progressSignature) return;
    this.progressSignature = signature;
    const follow = list.scrollHeight - list.scrollTop - list.clientHeight < 35;
    list.innerHTML = events.map(event => {
      const seconds = Math.max(0, Math.floor((Date.parse(event.at) - Date.parse(progress.run_id)) / 1000)) || 0;
      return `<li><time>${seconds}s</time><span>${esc(event.message)}</span></li>`;
    }).join('');
    if (follow) list.scrollTop = list.scrollHeight;
  }
  async streamProgress() {
    if (!this.data?.running || this.hidden || this.progressController) return;
    const controller = new AbortController(); this.progressController = controller;
    let reader;
    try {
      const cfg = window.APPLIEDIN_CONFIG || {};
      const response = await fetch((cfg.apiUrl || '').replace(/\/$/, '') + '/career-ops/progress', {
        headers: auth.header(), signal: controller.signal
      });
      if (!response.ok || !response.headers.get('content-type')?.includes('text/event-stream')) return;
      reader = response.body.getReader();
      const decoder = new TextDecoder(); let buffer = '';
      while (true) {
        const {value, done} = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, {stream:true});
        let boundary;
        while ((boundary = buffer.indexOf('\n\n')) >= 0) {
          const packet = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2);
          if (!packet.startsWith('data: ')) continue;
          const progress = JSON.parse(packet.slice(6));
          this.data.progress = progress; this.data.active_search = progress.active_search;
          if (!progress.running) { await this.refresh(); return; }
          this.paintStatus();
        }
      }
    } catch {
      // Regular polling recovers lost streams without replacing the user's form.
    } finally {
      if (reader) await reader.cancel().catch(() => {});
      this.progressController = null;
    }
  }
  rows() {
    const company = this.getAttribute('company') || '', query = (this.getAttribute('query') || '').toLowerCase().trim();
    return (this.data?.jobs || []).filter(r => (!company || r.company === company) && (!query || `${r.title} ${r.company} ${r.location}`.toLowerCase().includes(query)));
  }
  renderRows() {
    if (!this.data) return;
    const kind = this.$('.cb-state').value, sort = this.$('.cb-sort').value;
    const rows = this.rows().filter(r => kind === 'all' || (kind === 'network' ? !!this.data.last_network && r.search_id === this.data.last_network.search_id : kind === 'interest' ? !!this.data.last_search && r.search_id === this.data.last_search.started_at : r.state === kind)).sort((a,b) => sort === 'company' ? a.company.localeCompare(b.company) || a.title.localeCompare(b.title) : (Date.parse(b[sort === 'posted' ? 'posted_at' : 'first_seen']) || 0) - (Date.parse(a[sort === 'posted' ? 'posted_at' : 'first_seen']) || 0));
    const eligible = new Set(rows.filter(r => r.state === 'new').map(r => r.id));
    this.selected = new Set([...this.selected].filter(id => eligible.has(id)));
    const pages = Math.max(1, Math.ceil(rows.length / 25)); this.page = Math.min(Math.max(this.page, 1), pages);
    this.visibleRows = rows.slice((this.page-1)*25, this.page*25);
    this.$('.cb-result-count').textContent = `${rows.length} role${rows.length === 1 ? '' : 's'}`;
    this.$('.cb-list').innerHTML = this.visibleRows.map(r => `<article class="cb-job"><input type="checkbox" data-pick="${esc(r.id)}" aria-label="Select ${esc(r.title)} at ${esc(r.company)}" ${r.state !== 'new' ? 'disabled' : ''} ${this.selected.has(r.id) ? 'checked' : ''}>
      <div><a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">${esc(r.title)}</a><p>${esc(r.company)} · ${esc(r.location || 'Location not listed')}</p><small>${r.posted_at ? `Posted ${esc(date(r.posted_at))}` : 'Posting date unavailable'} · Found ${esc(date(r.first_seen))}${r.last_seen ? ` · Last seen ${esc(date(r.last_seen))}` : ''}</small>${r.why ? `<p class="cb-why">${esc(plainWhy(r.why))}</p>` : ""}${r.verification === "needs_posting_read" ? `<small>Posting will be checked before tailoring</small>` : ""}</div><span class="cb-state-label">${esc(r.application?.label || (r.state === "new" && r.verification === "verified" ? "Posting checked" : labels[r.state] || r.state))}</span></article>`).join('') || '<div class="cb-empty"><span class="cb-empty-icon" aria-hidden="true">⌕</span><strong>Your next role starts here</strong><p>Search jobs above, or choose All new jobs to browse earlier discoveries.</p></div>';
    this.$('.cb-actions').hidden = !rows.length;
    this.$('.cb-pagination').hidden = pages <= 1;
    this.$('.cb-pagination span').textContent = `${this.page} / ${pages}`;
    this.$('.cb-prev').disabled = this.page <= 1; this.$('.cb-next').disabled = this.page >= pages;
    this.paintSelection();
  }
  paintSelection() {
    const size = this.selected.size;
    this.$('.cb-selection').textContent = `${size} selected`;
    this.$('.cb-apply').hidden = !this.data?.capabilities?.apply_selected;
    this.$('.cb-apply').disabled = this.busy || !size || size > 50;
    this.$('.cb-apply-help').hidden = !size || !this.data?.capabilities?.apply_selected;
    this.$('.cb-prepare').disabled = this.busy || !size || size > 50;
    this.$('.cb-dismiss').disabled = this.busy || !size || size > 50;
    const eligible = (this.visibleRows || []).filter(r => r.state === 'new');
    const checked = eligible.filter(r => this.selected.has(r.id)).length;
    this.$('.cb-all').checked = !!eligible.length && checked === eligible.length;
    this.$('.cb-all').indeterminate = checked > 0 && checked < eligible.length;
    this.$('.cb-all').disabled = !eligible.length || this.busy;
  }
  seedNetwork(filters, prefs = this.data?.preferences || {}) {
    filters ||= {positive:prefs.titles || [], negative:prefs.exclude_keywords || [], locations:prefs.locations || [], ats:['greenhouse','lever','ashby','workday'], days:30, include_undated:true, limit:150};
    this.$('.cb-client').value = filters.provider || this.data?.settings.search_provider || 'claude';
    this.$('#cb-search-query').value = filters.interests || '';
    this.$('.cb-roles').value = filters.positive.join(', ');
    this.$('.cb-exclude').value = filters.negative.join(', ');
    this.$('.cb-locations').value = filters.locations.join(', ');
    this.$('.cb-days').value = String(filters.days);
    this.$('.cb-depth').value = String(filters.limit);
    this.$('.cb-undated').checked = filters.include_undated;
    this.querySelectorAll('.cb-ats input').forEach(el => el.checked = filters.ats.includes(el.value));
  }
  networkFilters() {
    const list = selector => this.$(selector).value.split(/[,;\n]+/).map(s => s.trim()).filter(Boolean);
    return {provider:this.$('.cb-client').value, interests:this.$('#cb-search-query').value.trim(), positive:list('.cb-roles'), negative:list('.cb-exclude'), locations:list('.cb-locations'),
      ats:[...this.querySelectorAll('.cb-ats input:checked')].map(el => el.value), days:Number(this.$('.cb-days').value),
      include_undated:this.$('.cb-undated').checked, limit:Number(this.$('.cb-depth').value)};
  }
  paintNetwork() {
    const running = this.data.running && this.data.active_search?.kind === 'network';
    const last = this.data.last_network, filters = this.networkFilters();
    this.$('.cb-network-start').disabled = this.busy || this.data.running || !filters.ats.length;
    this.$('.cb-network-start').textContent = running ? 'Scanning job boards…' : 'Search jobs';
    const more = this.$('.cb-network-continue');
    more.hidden = !last || last.complete || this.data.running;
    more.disabled = this.busy || !this.data.network_filters || Object.keys(filters).some(k => JSON.stringify(filters[k]) !== JSON.stringify(this.data.network_filters[k]));
    more.title = more.disabled ? 'Restore the previous filters, or start a new scan with these filters.' : 'Scan the next companies using the same filters';
    this.$('.cb-network-stop').hidden = !running;
    const coverage = running ? this.data.active_search.coverage : last?.coverage;
    const box = this.$('.cb-coverage');
    box.hidden = this.mode !== 'network' || !coverage || !Object.keys(coverage).length;
    if (box.hidden) return;
    const items = Object.entries(coverage), checked = items.reduce((n,[,c]) => n + c.next, 0), total = items.reduce((n,[,c]) => n + c.total, 0);
    box.innerHTML = `<div><strong>${checked.toLocaleString()} / ${total.toLocaleString()} company boards checked</strong><span>${running ? `${this.data.active_search.found || 0} matches this batch` : `${last?.undated || 0} matches excluded for missing dates`}</span></div><progress max="${total || 1}" value="${checked}" aria-label="Company directory coverage"></progress><p>${items.map(([name,c]) => `${esc(name)} ${c.next.toLocaleString()}/${c.total.toLocaleString()}${c.status !== 'ok' ? ` (${esc(c.status)})` : ''}`).join(' · ')}</p><details class="cb-coverage-details"><summary>About coverage</summary><p class="cb-note">Counts include unreachable boards. Provider page limits can also make results partial${last?.capped ? ` · ${last.capped} boards reported a page limit` : ''}.</p></details>`;
  }
  async network(resume) {
    if (this.busy || !this.data || this.data.running) return;
    this.busy = true; this.paintStatus(); this.message('Starting network scan…');
    try {
      const filters = this.networkFilters();
      const result = await this.request('/network', {...filters, resume});
      this.data.running = true; this.data.active_search = {kind:result.kind || 'network'};
      this.data.progress = null; this.$('.cb-state').value = 'network';
      this.page = 1; this.selected.clear();
      this.dispatchEvent(new CustomEvent('career-show-all', {bubbles:true}));
    } catch (error) { this.message(error.message, true); }
    finally { this.busy = false; if (this.data.running) await this.refresh(); else this.paintNetwork(); }
  }
  async save() {
    if (this.busy) return;
    this.busy = true; const button = this.$('form button'); button.disabled = true;
    try {
      const settings = await this.request('/settings', {companies: [...this.querySelectorAll('[name=company]:checked')].map(c => c.value), scheduled:this.$('[name=scheduled]').checked, auto_prepare:this.$('[name=auto_prepare]').checked, interests:this.$('#cb-search-query').value.trim(), scheduled_interests:this.$('[name=scheduled_interests]').checked, search_provider:this.$('.cb-client').value});
      this.data.settings = settings; this.$('.cb-saved').textContent = 'Saved';
    } catch (error) { this.$('.cb-saved').textContent = error.message; }
    finally { this.busy = false; button.disabled = false; }
  }
  async scan() {
    if (this.busy || !this.data || this.data.running) return;
    this.busy = true; this.paintStatus(); this.$('.cb-scan').textContent = 'Starting…';
    try {
      const result = await this.request('/scan', {company:this.getAttribute('company') || ''});
      this.data.running = true; this.data.active_search = {kind:result.kind === 'web' ? 'company_web' : 'feeds'}; this.data.progress = null;
      this.$('.cb-state').value = result.kind === 'web' ? 'interest' : 'new';
    } catch (error) { this.message(error.message, true); }
    finally { this.busy = false; if (this.data?.running) { this.paintStatus(); this.refresh(); } else { this.$('.cb-scan').disabled = false; this.$('.cb-scan').textContent = this.getAttribute('company') ? `Search ${this.getAttribute('company')}` : 'Search selected sources'; } }
  }
  async act(action) {
    if (this.busy || !this.selected.size || this.selected.size > 50) return;
    this.busy = true; this.paintSelection();
    const ids = [...this.selected];
    if (action === 'apply') {
      const requested_at = new Date().toISOString();
      for (const r of this.data.jobs.filter(r => this.selected.has(r.id))) this.pendingApplications.set(r.id, {...r, application:{phase:'starting', label:'Sending request…', requested_at}});
      this.paintApplications();
      this.$('.cb-apply').textContent = 'Starting…';
      this.$('.cb-applications').scrollIntoView({block:'nearest', behavior:'smooth'});
    }
    try {
      const companies = [...new Set(this.data.jobs.filter(r => this.selected.has(r.id)).map(r => r.company))];
      this.preparedCompany = companies.length === 1 ? companies[0] : '';
      const result = await this.request('/' + action, {ids:[...this.selected]});
      if (action === 'apply') for (const id of ids) {
        const row = this.pendingApplications.get(id);
        if (row) row.application = {...row.application, phase:'queued', label:'Request accepted', detail:'Waiting for the pipeline to report progress.'};
      }
      this.selected.clear(); this.busy = false; await this.refresh();
      if (action === 'apply' && !this.applicationRefreshFailed) for (const id of ids) {
        const pending = this.pendingApplications.get(id), tracked = this.data.jobs.find(r => r.id === id);
        if (pending && tracked?.pk && result.pks && !result.pks.includes(tracked.pk)) pending.application = {...pending.application, phase:'unknown', label:'Already in pipeline', detail:'This role was already handled. Open its pipeline to see the current status.'};
      }
      this.message(action === 'apply' ? `${result.started} selected roles started: scoring → tailoring → application. Follow them in View pipeline. Low scores or missing answers will stop for attention.` : action === 'prepare' ? `${result.prepared} sent for preparation${result.duplicates ? ` · ${result.duplicates} already handled` : ''}. Use View pipeline to follow preparation and review the tailored résumés.${this.data.paused ? ' Unpause the pipeline to start preparation.' : ''}` : 'Selected jobs dismissed.');
    } catch (error) {
      if (action === 'apply') for (const id of ids) {
        const row = this.pendingApplications.get(id);
        if (row) row.application = {...row.application, phase:'unknown', label:'Request not confirmed', detail:'Check the pipeline before retrying. ' + error.message};
      }
      this.message(error.message, true);
    }
    finally { this.busy = false; this.$('.cb-apply').textContent = 'Apply selected'; this.paintApplications(); this.paintSelection(); }
  }
}
customElements.define('career-board', CareerBoard);
