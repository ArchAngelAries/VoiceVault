const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { EventEmitter } = require('node:events')
const {
  FINGERPRINT_FILES,
  environmentLayout,
  computeFingerprint,
  setupReason,
  setupEnvironment,
  setupPlan,
  lineSplitter,
  runSetup,
} = require('./bootstrap.cjs')

function fakeResources() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'vv-bootstrap-'))
  for (const relative of FINGERPRINT_FILES) {
    const file = path.join(root, ...relative.split('/'))
    fs.mkdirSync(path.dirname(file), { recursive: true })
    fs.writeFileSync(file, `content of ${relative}\n`)
  }
  return root
}

test('environment layout keeps Python, uv cache and logs inside the state folder', () => {
  const state = path.resolve('/state')
  const layout = environmentLayout(state)
  for (const value of Object.values(layout)) assert.ok(value.startsWith(state), value)
  assert.equal(layout.python, path.join(state, 'python', 'venv', 'Scripts', 'python.exe'))
  assert.equal(layout.setupLog, path.join(state, 'logs', 'setup.log'))
})

test('fingerprint changes when a requirement file changes', () => {
  const root = fakeResources()
  try {
    const before = computeFingerprint(root)
    assert.equal(computeFingerprint(root), before)
    fs.appendFileSync(path.join(root, 'backend', 'requirements.txt'), 'newpackage\n')
    assert.notEqual(computeFingerprint(root), before)
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

test('setup runs on first run, after an interrupted setup and after an update only', () => {
  const layout = environmentLayout(path.resolve('/state'))
  const files = {}
  const deps = { existsSync: file => file in files, readFileSync: file => { if (!(file in files)) throw new Error('ENOENT'); return files[file] } }
  assert.equal(setupReason(layout, 'abc', deps), 'first-run')
  files[layout.python] = ''
  assert.equal(setupReason(layout, 'abc', deps), 'incomplete')
  files[layout.marker] = '{not json'
  assert.equal(setupReason(layout, 'abc', deps), 'incomplete')
  files[layout.marker] = JSON.stringify({ fingerprint: 'old' })
  assert.equal(setupReason(layout, 'abc', deps), 'update')
  files[layout.marker] = JSON.stringify({ fingerprint: 'abc' })
  assert.equal(setupReason(layout, 'abc', deps), null)
})

test('setup environment isolates uv and Python from the user profile and developer settings', () => {
  const layout = environmentLayout(path.resolve('/state'))
  const env = setupEnvironment(layout, 'C:\\app\\uv.exe', {
    Path: 'C:\\Windows', UV_INDEX_URL: 'https://example.invalid', PIP_INDEX_URL: 'x', PYTHONPATH: 'x', VIRTUAL_ENV: 'other', KEEP: '1',
  })
  assert.equal(env.UV_INDEX_URL, undefined)
  assert.equal(env.PIP_INDEX_URL, undefined)
  assert.equal(env.PYTHONPATH, undefined)
  assert.equal(env.KEEP, '1')
  assert.equal(env.UV_PYTHON_INSTALL_DIR, layout.interpreters)
  assert.equal(env.UV_CACHE_DIR, layout.cache)
  assert.equal(env.UV_NO_CONFIG, '1')
  assert.equal(env.PYTHONUTF8, '1')
  assert.equal(env.UV_PYTHON_PREFERENCE, 'only-managed')
  assert.equal(env.VIRTUAL_ENV, layout.venv)
  assert.equal(env.VOICEVAULT_UV, 'C:\\app\\uv.exe')
  assert.equal(env.Path, undefined)
  assert.equal(env.PATH.split(path.delimiter)[0], path.join(layout.venv, 'Scripts'))
  assert.ok(env.PATH.endsWith('C:\\Windows'))
})

test('setup plan never registers Python or adds it to PATH', () => {
  const layout = environmentLayout(path.resolve('/state'))
  const plan = setupPlan({ uv: 'uv.exe', layout, resourcesRoot: path.resolve('/res'), fingerprint: 'f' })
  assert.deepEqual(plan[0].args, ['python', 'install', '3.12', '--no-bin', '--no-registry'])
  assert.deepEqual(plan[1].args.slice(0, 2), ['venv', layout.venv])
  assert.ok(plan[1].args.includes('--allow-existing'))
  assert.equal(plan[2].command, layout.python)
  assert.deepEqual(plan[2].args.slice(-6), ['--resources', path.resolve('/res'), '--marker', layout.marker, '--fingerprint', 'f'])
  assert.equal(plan.at(-1).optional, true)
})

test('line splitter handles split chunks and carriage-return progress', () => {
  const lines = []
  const splitter = lineSplitter(line => lines.push(line))
  splitter.push('one\r\ntw')
  splitter.push('o\rthree\n\n')
  splitter.push('tail')
  splitter.flush()
  assert.deepEqual(lines, ['one', 'two', 'three', 'tail'])
})

function fakeSpawn(behaviour) {
  const calls = []
  const spawnImpl = (command, args, options) => {
    calls.push({ command, args, options })
    const child = new EventEmitter()
    child.stdout = new EventEmitter()
    child.stderr = new EventEmitter()
    setImmediate(() => {
      const code = behaviour({ command, args, child })
      child.emit('close', code)
    })
    return child
  }
  return { calls, spawnImpl }
}

test('runSetup runs every step, logs output and returns the written marker', async () => {
  const resources = fakeResources()
  const state = fs.mkdtempSync(path.join(os.tmpdir(), 'vv-state-'))
  const uv = path.join(resources, 'uv.exe')
  fs.writeFileSync(uv, '')
  const layout = environmentLayout(state)
  try {
    const { calls, spawnImpl } = fakeSpawn(({ args, child }) => {
      child.stdout.emit('data', `ran ${args[0]}\n`)
      const marker = args.indexOf('--marker')
      if (marker >= 0) {
        fs.writeFileSync(args[marker + 1], JSON.stringify({ fingerprint: args[args.indexOf('--fingerprint') + 1], torch: '2.9.1+rocm7.2.1', accelerator: 'rocm' }))
      }
      return args[0] === 'cache' ? 1 : 0 // the optional cache clean may fail without failing setup
    })
    const progress = []
    const lines = []
    const marker = await runSetup({ uv, layout, resourcesRoot: resources, env: { PATH: 'x' }, spawnImpl,
      onProgress: info => progress.push(info.step), onLine: line => lines.push(line) })
    assert.equal(marker.accelerator, 'rocm')
    assert.equal(calls.length, 4)
    assert.deepEqual(progress, [1, 2, 3, 4])
    assert.ok(lines.includes('ran python'))
    const log = fs.readFileSync(layout.setupLog, 'utf8')
    assert.match(log, /Setup complete: torch 2\.9\.1\+rocm7\.2\.1/)
    assert.equal(setupReason(layout, computeFingerprint(resources), { existsSync: () => true }), null)
  } finally {
    fs.rmSync(resources, { recursive: true, force: true })
    fs.rmSync(state, { recursive: true, force: true })
  }
})

test('runSetup stops at a failed step, keeps no marker and repair removes the old environment', async () => {
  const resources = fakeResources()
  const state = fs.mkdtempSync(path.join(os.tmpdir(), 'vv-state-'))
  const uv = path.join(resources, 'uv.exe')
  fs.writeFileSync(uv, '')
  const layout = environmentLayout(state)
  fs.mkdirSync(layout.venv, { recursive: true })
  fs.writeFileSync(path.join(layout.venv, 'old.txt'), 'x')
  fs.writeFileSync(layout.marker, JSON.stringify({ fingerprint: 'old' }))
  try {
    const { calls, spawnImpl } = fakeSpawn(({ args }) => args[0] === 'venv' ? 3 : 0)
    await assert.rejects(runSetup({ uv, layout, resourcesRoot: resources, env: {}, spawnImpl, repair: true }),
      /Create the private Python environment failed \(exit code 3\)/)
    assert.equal(calls.length, 2)
    assert.equal(fs.existsSync(path.join(layout.venv, 'old.txt')), false)
    assert.equal(fs.existsSync(layout.marker), false)
    assert.match(fs.readFileSync(layout.setupLog, 'utf8'), /Setup failed/)
  } finally {
    fs.rmSync(resources, { recursive: true, force: true })
    fs.rmSync(state, { recursive: true, force: true })
  }
})

test('runSetup refuses to start without the bundled uv', async () => {
  const state = fs.mkdtempSync(path.join(os.tmpdir(), 'vv-state-'))
  try {
    await assert.rejects(runSetup({ uv: path.join(state, 'missing-uv.exe'), layout: environmentLayout(state), resourcesRoot: state }),
      /uv\.exe is missing.*Reinstall/)
  } finally {
    fs.rmSync(state, { recursive: true, force: true })
  }
})
