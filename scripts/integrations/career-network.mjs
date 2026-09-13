// AppliedIn's streaming adapter for Career Ops' reverse-ATS discovery modules.
// The upstream providers and filters own reading; AppliedIn owns persistence.
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { createHash } from 'node:crypto';

export async function scanNetwork(input, deps) {
  const { sources, loadList, makeContext, matches, emit, stopped = () => false } = deps;
  const cutoff = Date.parse(input.cutoff);
  for (const name of input.ats) {
    if (stopped()) break;
    const source = sources[name];
    const { list, status } = await loadList(name, source.dataset);
    const entries = list.map(source.toEntry).filter(Boolean);
    const hash = createHash('sha256').update(JSON.stringify(entries)).digest('hex');
    const previous = input.cursor?.[name];
    let next = previous?.hash === hash ? previous.next : 0;
    // A changed directory must never reuse an offset from a different ordering.
    if (previous && previous.hash !== hash) emit({kind:'notice', message:`${name}: directory changed; rechecking from the beginning.`});
    const end = input.limit ? Math.min(entries.length, next + input.limit) : entries.length;
    let claim = next;
    const finished = new Set();
    emit({kind:'source', source:name, total:entries.length, next, hash, status});
    const ctx = {...makeContext(), sinceMs:cutoff, includeUndated:input.include_undated, syntheticEntries:true};
    await Promise.all(Array.from({length:Math.min(source.concurrency || 6, end - next)}, async () => {
      while (claim < end && !stopped()) {
        const index = claim++, entry = entries[index];
        let jobs = [], error = '', read = 0, undated = 0, old = 0, filtered = 0, capped = false;
        try {
          const postings = await source.provider.fetch(entry, ctx);
          read = postings.length;
          capped = !!(postings.workdayTruncated || postings.icimsTruncated);
          jobs = postings.filter(job => {
            if (!matches(job)) { filtered++; return false; }
            const posted = typeof job.postedAt === 'number' ? job.postedAt : Date.parse(job.postedAt);
            if (!Number.isFinite(posted) || !posted) {
              if (!input.include_undated) { undated++; return false; }
            } else if (posted < cutoff) { old++; return false; }
            return true;
          });
        } catch (err) { error = String(err.message).slice(0, 250); }
        finished.add(index);
        while (finished.delete(next)) next++;
        // Each company is durable before the next batch. On interruption a small
        // overlap may be re-read; idempotent URL imports make that safe.
        emit({kind:'company', source:name, company:entry.name, provider:name,
          jobs, error, read, undated, old, filtered, capped, cursor:{hash, next}});
      }
    }));
  }
  emit({kind:'done', stopped:stopped()});
}

export async function cachedList(name, url, directory, fetchJson) {
  fs.mkdirSync(directory, {recursive:true});
  const file = path.join(directory, `${name}.json`);
  let cached;
  try { cached = JSON.parse(fs.readFileSync(file, 'utf8')); } catch {}
  if (Array.isArray(cached) && Date.now() - fs.statSync(file).mtimeMs < 86400000) return {list:cached, status:'ok'};
  try {
    const list = await fetchJson(url, {timeoutMs:30000});
    if (!Array.isArray(list) || !list.length) throw Error('Empty company directory');
    fs.writeFileSync(file + '.tmp', JSON.stringify(list)); fs.renameSync(file + '.tmp', file);
    return {list, status:'ok'};
  } catch {
    return {list:Array.isArray(cached) ? cached : [], status:cached?.length ? 'stale' : 'unavailable'};
  }
}

async function main() {
  const root = process.argv[2];
  const upstream = file => import(pathToFileURL(path.join(root, file)));
  const { SOURCES, passesFilters } = await upstream('scan-ats-full.mjs');
  const { buildTitleFilter, buildLocationFilter } = await upstream('scan.mjs');
  const { makeHttpCtx, fetchJson } = await upstream('providers/_http.mjs');
  if (process.argv.includes('--check')) { console.log('ready'); return; }
  let text = ''; for await (const chunk of process.stdin) text += chunk;
  const input = JSON.parse(text);
  let stopping = false;
  process.on('SIGTERM', () => { stopping = true; });
  const titleFilter = buildTitleFilter({positive:input.positive, negative:input.negative});
  const locationFilter = buildLocationFilter({allow:input.locations});
  await scanNetwork(input, {
    sources:SOURCES, makeContext:makeHttpCtx, stopped:() => stopping,
    matches:job => passesFilters(job, {titleFilter, locationFilter}),
    loadList:(name, url) => cachedList(name, url, input.cache_dir, fetchJson),
    emit:event => process.stdout.write(JSON.stringify(event) + '\n'),
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch(error => { console.error(error.message); process.exitCode = 1; });
}
