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
  const output = {
    log: (...args) => { stdout += util.format(...args) + '\n'; },
    info: (...args) => { stdout += util.format(...args) + '\n'; },
    warn: (...args) => { stderr += util.format(...args) + '\n'; },
    error: (...args) => { stderr += util.format(...args) + '\n'; },
  };
  class RequestURL extends URL {}
  const sandbox = {
    console: output,
    process: {argv: ['node', filename, ...argv]},
    URL: RequestURL,
  };
  const context = vm.createContext(sandbox);
  // In Node's normal CommonJS context global is an alias of globalThis.
  vm.runInContext('globalThis.global = globalThis', context);
  sandbox.require = requested => {
    if (path.basename(filename) !== 'entry_dom_fixture.js' ||
        requested !== './lever_card_fixture.cjs')
      throw Error('Unsupported fixture-relative require: ' + String(requested));
    const dependency = path.join(path.dirname(filename), 'lever_card_fixture.cjs');
    const bytes = fs.readFileSync(dependency, 'utf8');
    const module = {exports: {}};
    const wrapper = vm.runInContext(
      '(function(module, exports, require, __filename, __dirname) {\n' +
      bytes + '\n})', context, {filename: dependency, timeout: timeout_ms});
    wrapper(module, module.exports, () => {
      throw Error('Nested fixture requires are not supported');
    }, dependency, path.dirname(dependency));
    return module.exports;
  };

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
