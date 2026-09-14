import { auth } from './auth.js';

const dialog = document.querySelector('#steering-panel');
dialog.innerHTML = `<header><div><h2 id="steering-title">Agent instructions</h2><p>One steering file for search, scoring, tailoring, review and applications.</p></div></header>
  <div class="steering-body"><p id="steering-help">Describe how you want agents to work. Saved changes reach new model requests and new browser sessions; sessions already running keep their current instructions. Job preferences and application approval still apply.</p>
    <div class="steering-import"><label class="btn">Import .md or .txt<input type="file" accept=".md,.txt,text/plain,text/markdown" class="steering-file"></label><span>Import fills the editor. Save to activate.</span></div>
    <label for="steering-content">Your instructions</label><textarea id="steering-content" maxlength="32000" spellcheck="false" aria-describedby="steering-help" placeholder="For example: Keep application answers concise. Emphasize relevant infrastructure work already in my résumé."></textarea>
    <div class="steering-meta"><span class="steering-count"></span><span class="steering-instance"></span></div>
  </div><footer><span class="steering-status" role="status" aria-live="polite"></span><button type="button" class="btn steering-reload">Reload saved</button><button type="button" class="btn steering-save">Save instructions</button></footer>`;
const $ = selector => dialog.querySelector(selector);
$('.steering-instance').textContent = `Private to ${new URL(window.APPLIEDIN_CONFIG?.apiUrl || location.origin, location.href).host} · steering.md`;
const editor = $('#steering-content'), status = $('.steering-status'), save = $('.steering-save');
let saved = '', revision, busy = false, loaded = false;
const dirty = () => loaded && editor.value !== saved;
function paint() {
  save.disabled = busy || !dirty();
  editor.disabled = busy || !loaded;
  $('.steering-reload').disabled = busy;
  $('.steering-file').disabled = busy || !loaded;
  $('.steering-count').textContent = `${editor.value.length.toLocaleString()} / 32,000 characters`;
}
async function request(body) {
  const cfg = window.APPLIEDIN_CONFIG || {};
  if (cfg.demo || new URLSearchParams(location.search).has('demo')) throw new Error('Connect to AppliedIn to save instructions.');
  const response = await fetch((cfg.apiUrl || '').replace(/\/$/, '') + '/steering', {
    method: body === undefined ? 'GET' : 'POST', headers: {'Content-Type':'application/json', ...auth.header()},
    ...(body === undefined ? {} : {body: JSON.stringify(body)})
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not load or save instructions. Try again.');
  return data;
}
async function reload() {
  if (busy || (dirty() && !confirm('Replace your unsaved edits with the saved instructions?'))) return;
  busy = true; status.textContent = 'Loading…'; paint();
  try {
    const data = await request();
    saved = data.content; revision = data.revision; editor.value = saved; loaded = true;
    status.textContent = saved.trim() ? 'Saved instructions loaded.' : 'No shared instructions yet.';
  } catch (error) { status.textContent = error.message; }
  finally { busy = false; paint(); }
}
export function showSteering(visible) {
  dialog.hidden = !visible;
  if (visible && !loaded && !busy) reload();
}
editor.oninput = () => { status.textContent = dirty() ? 'Unsaved changes' : 'No changes'; paint(); };
$('.steering-reload').onclick = reload;
$('.steering-file').onchange = async event => {
  const file = event.target.files[0];
  if (!file || busy) return;
  busy = true; paint();
  try {
    if (!/\.(md|txt)$/i.test(file.name) || file.size > 128000) throw new Error('Choose a .md or .txt file with up to 32,000 characters.');
    const text = await file.text();
    if (text.length > 32000 || text.includes('\0')) throw new Error('Use plain text with up to 32,000 characters.');
    if (dirty() && !confirm('Replace your unsaved edits with this file?')) return;
    editor.value = text; status.textContent = 'Imported — save to activate.';
  } catch (error) { status.textContent = error.message; }
  finally { busy = false; event.target.value = ''; paint(); }
};
save.onclick = async () => {
  if (busy || !dirty()) return;
  busy = true; paint(); status.textContent = 'Saving…';
  try {
    const data = await request({content:editor.value, revision});
    saved = data.content; revision = data.revision;
    status.textContent = saved.trim() ? 'Saved — active for new agent requests.' : 'Saved — shared instructions cleared.';
  } catch (error) { status.textContent = error.message; }
  finally { busy = false; paint(); }
};
window.addEventListener('beforeunload', event => { if (dirty()) { event.preventDefault(); event.returnValue = ''; } });
