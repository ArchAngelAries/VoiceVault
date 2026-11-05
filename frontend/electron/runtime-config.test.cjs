const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const {
  readRuntimeConfiguration,
  effectiveRuntimeEnvironment,
  validateRuntimeConfiguration,
  saveRuntimeConfiguration,
  resolveDesktopUserDataDirectory,
} = require('./runtime-config.cjs')

function fixture(t) {
  const base = path.resolve(__dirname, '../../data/tmp/desktop-runtime-tests')
  fs.mkdirSync(base, { recursive: true })
  const root = fs.mkdtempSync(path.join(base, 'fixture-'))
  t.after(() => fs.rmSync(root, { recursive: true, force: true }))
  fs.mkdirSync(path.join(root, 'backend'), { recursive: true })
  fs.writeFileSync(path.join(root, 'backend', 'main.py'), '# fixture')
  const python = path.join(root, 'venv', 'Scripts', 'python.exe')
  fs.mkdirSync(path.dirname(python), { recursive: true })
  fs.writeFileSync(python, '')
  return { root, python, userData: path.join(root, 'user-data') }
}

test('reads absent configuration as empty and saves only the two path fields', t => {
  const { root, userData } = fixture(t)
  assert.deepEqual(readRuntimeConfiguration(userData), { projectRoot: '', python: '' })
  saveRuntimeConfiguration(userData, { projectRoot: root, python: '', ignored: 'secret' })
  assert.deepEqual(readRuntimeConfiguration(userData), { projectRoot: root, python: '' })
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(userData, 'desktop-runtime.json'), 'utf8')), {
    projectRoot: root,
    python: '',
  })
})

test('malformed configuration gives a readable error without exposing its contents', t => {
  const { root, userData } = fixture(t)
  fs.mkdirSync(userData)
  fs.writeFileSync(path.join(userData, 'desktop-runtime.json'), '{"token":"secret-value"')
  assert.throws(() => readRuntimeConfiguration(userData), error => {
    assert.match(error.message, /configuration is malformed/i)
    assert.doesNotMatch(error.message, /secret-value/)
    return true
  })
})

test('process environment overrides saved paths after trimming', () => {
  const saved = { projectRoot: ' C:\\saved ', python: ' C:\\saved\\python.exe ' }
  const env = { VOICEVAULT_PROJECT_ROOT: ' D:\\override ', VOICEVAULT_PYTHON: ' ' }
  const result = effectiveRuntimeEnvironment(saved, env)
  assert.equal(result.VOICEVAULT_PROJECT_ROOT, 'D:\\override')
  assert.equal(result.VOICEVAULT_PYTHON, 'C:\\saved\\python.exe')
  assert.equal(result.other, undefined)
  assert.equal(env.VOICEVAULT_PROJECT_ROOT, ' D:\\override ')
})

test('validates absolute project and optional Python paths against real files', t => {
  const { root, python } = fixture(t)
  assert.deepEqual(validateRuntimeConfiguration({ projectRoot: ` ${root} `, python: ` ${python} ` }, {}, {
    existsSync: fs.existsSync,
  }), { projectRoot: root, python })
  assert.deepEqual(validateRuntimeConfiguration({ projectRoot: root, python: '' }, {}, {
    existsSync: fs.existsSync,
  }), { projectRoot: root, python: '' })
  assert.throws(() => validateRuntimeConfiguration({ projectRoot: 'relative', python: '' }), /absolute path/)
  assert.throws(() => validateRuntimeConfiguration({ projectRoot: root, python: 'python.exe' }), /absolute path/)
  assert.throws(() => validateRuntimeConfiguration({ projectRoot: root, python: path.join(root, 'python.py') }), /python\.exe/)
  assert.throws(() => validateRuntimeConfiguration({ projectRoot: root, python: path.join(root, 'missing.exe') }), /missing executable/)
  assert.throws(() => validateRuntimeConfiguration({ projectRoot: root, python: '', extra: 'x' }), /only projectRoot and python/)
  assert.throws(() => validateRuntimeConfiguration({ projectRoot: `${root}\n`, python: '' }), /control characters/)
  assert.throws(() => validateRuntimeConfiguration({ projectRoot: 'x'.repeat(4097), python: '' }), /4096/)
  assert.throws(() => validateRuntimeConfiguration({ projectRoot: root, python: 42 }), /must be a string/)
})

test('failed atomic replacement preserves the previous configuration', t => {
  const { root, userData } = fixture(t)
  saveRuntimeConfiguration(userData, { projectRoot: root, python: '' })
  const before = fs.readFileSync(path.join(userData, 'desktop-runtime.json'), 'utf8')
  const failingFs = { ...fs, renameSync() { throw new Error('injected failure') } }
  assert.throws(() => saveRuntimeConfiguration(userData, { projectRoot: root, python: 'C:\\python.exe' }, failingFs), /Could not save/)
  assert.equal(fs.readFileSync(path.join(userData, 'desktop-runtime.json'), 'utf8'), before)
  assert.deepEqual(fs.readdirSync(userData), ['desktop-runtime.json'])
})

test('default and overridden Python must exist before a configuration can be saved', t => {
  const { root, python, userData } = fixture(t)
  saveRuntimeConfiguration(userData, { projectRoot: root, python: '' })
  const before = fs.readFileSync(path.join(userData, 'desktop-runtime.json'), 'utf8')
  const backendOnly = candidate => candidate === path.join(root, 'backend', 'main.py')
  assert.throws(() => validateRuntimeConfiguration({ projectRoot: root, python: '' }, {}, { existsSync: backendOnly }), /Python was not found/)
  assert.equal(fs.readFileSync(path.join(userData, 'desktop-runtime.json'), 'utf8'), before)
  assert.deepEqual(validateRuntimeConfiguration({ projectRoot: root, python: '' }, { VOICEVAULT_PYTHON: python }), { projectRoot: root, python: '' })
  assert.throws(() => readRuntimeConfiguration(userData, { readFileSync: () => '{"projectRoot":42,"python":""}' }), /malformed/)
})

test('explicit state directory groups desktop state while preserving defaults and individual overrides', () => {
  const root = path.resolve(__dirname, '../../data/tmp/separate-state')
  const customDesktop = path.resolve(__dirname, '../../data/tmp/separate-desktop')
  assert.equal(resolveDesktopUserDataDirectory({}), undefined)
  assert.equal(resolveDesktopUserDataDirectory({ VOICEVAULT_STATE_DIR: root }), path.join(root, 'desktop'))
  assert.equal(resolveDesktopUserDataDirectory({ VOICEVAULT_STATE_DIR: root, VOICEVAULT_USER_DATA_DIR: customDesktop }), customDesktop)
  assert.throws(() => resolveDesktopUserDataDirectory({ VOICEVAULT_STATE_DIR: 'relative', VOICEVAULT_USER_DATA_DIR: customDesktop }), /absolute path.*reopen/)
})
