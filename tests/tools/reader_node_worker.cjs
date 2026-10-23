'use strict';

// Only the two offline DOM fixtures are supported. Each request gets a fresh VM,
// not a fresh Node process, so reader/fixture mutations never leak to the next case.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const util = require('node:util');
const readline = require('node:readline');

const FIXTURES = new Set(['closed_widget_dom.cjs', 'entry_dom_fixture.js']);
const MAX_REQUEST_BYTES = 8 * 1024 * 1024;
const MAX_REPLY_BYTES = 32 * 1024 * 1024;

function reply(message) {
  const line = JSON.stringify(message);
  if (Buffer.byteLength(line) > MAX_REPLY_BYTES)
    throw Error('Reader worker response exceeds limit');
  process.stdout.write(line + '\n');
}

function evaluate(request) {
  const {fixture, argv, timeout_ms} = request;
  if (typeof fixture !== 'string' || !FIXTURES.has(path.basename(fixture)) ||
      !Array.isArray(argv) || argv.length !==
      (path.basename(fixture) === 'closed_widget_dom.cjs' ? 3 : 2) ||
      !argv.every(s => typeof s === 'string'))
    throw Error('Unsupported offline reader fixture invocation');

  // Read both the entry script and any fixture-relative dependency on *every*
  // call. There is no Node require.cache or persistent context for test code.
  const filename = path.resolve(fixture);
  const source = fs.readFileSync(filename, 'utf8');
  let stdout = '', stderr = '';
  // Reader-visible globals belong to this VM. Host callbacks are captured in
  // bootstrap closures and removed from the global before fixture code runs.
  // In particular, URLs and CommonJS objects must not expose host prototypes.
  const argvJSON = JSON.stringify(['node', filename, ...argv]);
  const bridge = {
    argv: () => argvJSON,
    parseURL(input, base) {
      const url = new URL(input, base);
      return JSON.stringify({
        href: url.href, protocol: url.protocol, hostname: url.hostname,
        host: url.host, origin: url.origin, pathname: url.pathname,
        search: url.search, hash: url.hash, port: url.port,
        username: url.username, password: url.password,
      });
    },
    write(kind, args) {
      const line = util.format(...args) + '\n';
      if (kind === 'stderr') stderr += line;
      else stdout += line;
    },
    load(requested) {
      if (path.basename(filename) !== 'entry_dom_fixture.js' ||
          requested !== './lever_card_fixture.cjs')
        throw Error('Unsupported fixture-relative require: ' + String(requested));
      const dependency = path.join(path.dirname(filename), 'lever_card_fixture.cjs');
      const bytes = fs.readFileSync(dependency, 'utf8');
      const module = vm.runInContext('({exports:{}})', context);
      const wrapper = vm.runInContext(
        '(function(module, exports, require, __filename, __dirname) {\n' +
        bytes + '\n})', context, {filename: dependency, timeout: timeout_ms});
      const noNestedRequire = vm.runInContext(
        '() => { throw Error("Nested fixture requires are not supported"); }',
        context);
      wrapper(module, module.exports, noNestedRequire,
              dependency, path.dirname(dependency));
      return module.exports;
    },
  };
  const context = vm.createContext({__bridge: bridge});
  vm.runInContext([
    '(() => {',
    '  const {argv, parseURL, write, load} = globalThis.__bridge;',
    '  delete globalThis.__bridge;',
    '  globalThis.global = globalThis;',
    '  globalThis.process = {argv: JSON.parse(argv())};',
    '  globalThis.console = {',
    "    log: (...args) => write('stdout', args),",
    "    info: (...args) => write('stdout', args),",
    "    warn: (...args) => write('stderr', args),",
    "    error: (...args) => write('stderr', args),",
    '  };',
    '  globalThis.URL = class URL {',
    '    constructor(input, base) {',
    '      let data;',
    '      try { data = parseURL(String(input), base === undefined ? undefined : String(base)); }',
    '      catch (error) { throw new TypeError(String(error.message || error)); }',
    '      Object.assign(this, JSON.parse(data));',
    '    }',
    '    toString() { return this.href; }',
    '    toJSON() { return this.href; }',
    '  };',
    '  globalThis.require = name => {',
    '    try { return load(name); }',
    '    catch (error) { throw new Error(String(error.message || error)); }',
    '  };',
    '})()',
  ].join('\n'), context);

  try {
    vm.runInContext(source, context, {filename, timeout: timeout_ms});
    return {ok: true, stdout, stderr};
  } catch (error) {
    return {
      ok: false, returncode: 1, stdout,
      stderr: stderr + (error && error.stack ? error.stack : String(error)) + '\n',
    };
  }
}

const rl = readline.createInterface({input: process.stdin, crlfDelay: Infinity});
rl.on('line', line => {
  let id = null;
  try {
    if (Buffer.byteLength(line) > MAX_REQUEST_BYTES)
      throw Error('Reader worker request exceeds limit');
    const request = JSON.parse(line);
    id = request.id;
    if (!Number.isSafeInteger(id) || id < 1)
      throw Error('Invalid reader request id');
    const timeout = request.timeout_ms;
    if (!Number.isSafeInteger(timeout) || timeout < 1 || timeout > 60000)
      throw Error('Invalid reader request timeout');
    reply({id, ...evaluate(request)});
  } catch (error) {
    reply({id, ok: false, returncode: 1, stdout: '',
      stderr: (error && error.stack ? error.stack : String(error)) + '\n'});
  }
});
