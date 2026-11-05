const test = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const { resolveProjectRoot } = require('./packaging.cjs')

test('development startup resolves the repository root from the Electron source folder', () => {
  const appDir = path.resolve(__dirname)
  const expected = path.resolve(appDir, '../..')
  assert.equal(resolveProjectRoot({
    isPackaged: false,
    appDir,
    env: { VOICEVAULT_PROJECT_ROOT: 'ignored-in-development' },
    existsSync: candidate => candidate === path.join(expected, 'backend', 'main.py'),
  }), expected)
})

test('packaged startup accepts an explicit absolute repository root with backend code', () => {
  const expected = path.resolve(__dirname, '../../external-project')
  assert.equal(resolveProjectRoot({
    isPackaged: true,
    appDir: __dirname,
    env: { VOICEVAULT_PROJECT_ROOT: expected },
    existsSync: candidate => candidate === path.join(expected, 'backend', 'main.py'),
  }), expected)
})

test('packaged startup rejects missing or relative project-root configuration', () => {
  let checked = false
  const existsSync = () => { checked = true; return true }
  assert.throws(() => resolveProjectRoot({ isPackaged: true, appDir: __dirname, env: {}, existsSync }),
    /absolute path.*compatible Python environment.*VOICEVAULT_PROJECT_ROOT/)
  assert.throws(() => resolveProjectRoot({ isPackaged: true, appDir: __dirname,
    env: { VOICEVAULT_PROJECT_ROOT: 'relative/project' }, existsSync }), /absolute path/)
  assert.equal(checked, false)
})

test('packaged startup rejects an absolute path that is not a full VoiceVault project', () => {
  const candidateRoot = path.resolve(__dirname, '../../incomplete-project')
  assert.throws(() => resolveProjectRoot({
    isPackaged: true,
    appDir: __dirname,
    env: { VOICEVAULT_PROJECT_ROOT: candidateRoot },
    existsSync: () => false,
  }), /containing backend\/main\.py.*VOICEVAULT_PROJECT_ROOT.*Python/)
})

const { defaultStateDirectory, resolveDesktopLayout, bundledBackendEnvironment, backendPort } = require('./packaging.cjs')

test('installed app keeps its state in LOCALAPPDATA unless VOICEVAULT_STATE_DIR is set', () => {
  const local = path.resolve(__dirname, '../../fixture-local')
  assert.equal(defaultStateDirectory({ LOCALAPPDATA: local }), path.join(local, 'VoiceVault'))
  const custom = path.resolve(__dirname, '../../fixture-state')
  assert.equal(defaultStateDirectory({ LOCALAPPDATA: local, VOICEVAULT_STATE_DIR: custom }), custom)
  assert.throws(() => defaultStateDirectory({ VOICEVAULT_STATE_DIR: 'relative' }), /absolute/)
  assert.throws(() => defaultStateDirectory({}), /LOCALAPPDATA.*VOICEVAULT_STATE_DIR/)
})

test('installed app runs the bundled backend; VOICEVAULT_PROJECT_ROOT still selects an external project', () => {
  const resources = path.resolve(__dirname, '../../fixture-install/resources')
  const local = path.resolve(__dirname, '../../fixture-local')
  const bundledMain = path.join(resources, 'voicevault', 'backend', 'main.py')
  const layout = resolveDesktopLayout({ isPackaged: true, appDir: __dirname, resourcesPath: resources,
    env: { LOCALAPPDATA: local }, existsSync: candidate => candidate === bundledMain })
  assert.deepEqual(layout, { mode: 'bundled', projectRoot: path.join(resources, 'voicevault'),
    stateDir: path.join(local, 'VoiceVault'), uv: path.join(resources, 'voicevault', 'uv', 'uv.exe') })
  const project = path.resolve(__dirname, '../../external-project')
  assert.equal(resolveDesktopLayout({ isPackaged: true, appDir: __dirname, resourcesPath: resources,
    env: { VOICEVAULT_PROJECT_ROOT: project }, existsSync: candidate => candidate === path.join(project, 'backend', 'main.py') }).mode, 'external')
  assert.throws(() => resolveDesktopLayout({ isPackaged: true, appDir: __dirname, resourcesPath: resources,
    env: { LOCALAPPDATA: local }, existsSync: () => false }), /incomplete.*Reinstall/)
  assert.equal(resolveDesktopLayout({ isPackaged: false, appDir: __dirname, env: {}, existsSync: () => true }).mode, 'development')
})

test('bundled backend environment points models, runtimes and caches at the state folder', () => {
  const state = path.resolve(__dirname, '../../fixture-state')
  const python = path.join(state, 'python', 'venv', 'Scripts', 'python.exe')
  const env = bundledBackendEnvironment({ Path: 'C:\\Windows' }, { stateDir: state, python })
  assert.equal(env.VOICEVAULT_STATE_DIR, state)
  assert.equal(env.VOICEVAULT_PYTHON, python)
  assert.equal(env.VOICEVAULT_MODELS_DIR, path.join(state, 'Models'))
  assert.equal(env.VOICEVAULT_RUNTIME_DIR, path.join(state, 'runtime'))
  assert.equal(env.HF_HOME, path.join(state, 'huggingface'))
  assert.equal(env.Path, undefined)
  assert.equal(env.PATH.split(path.delimiter)[0], path.dirname(python))
  const custom = bundledBackendEnvironment({ VOICEVAULT_MODELS_DIR: 'D:\\Models', HF_HUB_CACHE: 'D:\\hf' }, { stateDir: state, python })
  assert.equal(custom.VOICEVAULT_MODELS_DIR, 'D:\\Models')
  assert.equal(custom.HF_HOME, undefined)
})

test('backend port defaults to 7763 and accepts only a valid override', () => {
  assert.equal(backendPort({}), 7763)
  assert.equal(backendPort({ VOICEVAULT_BACKEND_PORT: '7790' }), 7790)
  for (const bad of ['80', '70000', 'abc', '7790x']) assert.throws(() => backendPort({ VOICEVAULT_BACKEND_PORT: bad }), /1024 to 65535/)
})
