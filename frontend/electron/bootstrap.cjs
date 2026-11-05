/**
 * First-run setup of the installed app's private Python environment.
 *
 * The installer ships uv.exe (Astral, MIT OR Apache-2.0) but no Python or Python packages.
 * On first launch, or when the bundled requirements change, Electron runs:
 *   uv python install 3.12  ->  uv venv  ->  scripts/bootstrap_env.py (torch, packages, Prisma, check)
 * Everything lives in <state>/python; nothing is written to PATH, the registry or uv's global folders.
 */
const fs = require('node:fs')
const path = require('node:path')
const crypto = require('node:crypto')
const { spawn } = require('node:child_process')

// Bump to force every installed copy to re-run setup (for example after changing the plan below).
const BOOTSTRAP_VERSION = 1
const PYTHON_VERSION = '3.12'
const FINGERPRINT_FILES = [
  'backend/requirements.txt',
  'backend/requirements-tts.txt',
  'backend/requirements-lock.txt',
  'scripts/bootstrap_env.py',
  'scripts/install_torch.py',
  'scripts/setup_project.py',
]

function environmentLayout(stateDir) {
  const root = path.join(stateDir, 'python')
  const venv = path.join(root, 'venv')
  return {
    root,
    venv,
    python: path.join(venv, 'Scripts', 'python.exe'),
    interpreters: path.join(root, 'interpreters'),
    cache: path.join(root, 'uv-cache'),
    marker: path.join(root, 'voicevault-env.json'),
    logDir: path.join(stateDir, 'logs'),
    setupLog: path.join(stateDir, 'logs', 'setup.log'),
  }
}

function computeFingerprint(resourcesRoot, readFileSync = fs.readFileSync) {
  const hash = crypto.createHash('sha256')
  hash.update(`voicevault-bootstrap:${BOOTSTRAP_VERSION}:python${PYTHON_VERSION}\n`)
  for (const relative of FINGERPRINT_FILES) {
    hash.update(`${relative}\n`)
    hash.update(readFileSync(path.join(resourcesRoot, ...relative.split('/'))))
  }
  return hash.digest('hex')
}

function readMarker(markerPath, readFileSync = fs.readFileSync) {
  try {
    const parsed = JSON.parse(readFileSync(markerPath, 'utf8'))
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null
  } catch {
    return null
  }
}

/** Why setup must run, or null when the environment is ready. */
function setupReason(layout, fingerprint, { existsSync = fs.existsSync, readFileSync = fs.readFileSync } = {}) {
  if (!existsSync(layout.python)) return 'first-run'
  const marker = readMarker(layout.marker, readFileSync)
  if (!marker) return 'incomplete'
  if (marker.fingerprint !== fingerprint) return 'update'
  return null
}

/** Environment for uv and the setup script: everything uv owns stays inside <state>/python. */
function setupEnvironment(layout, uv, env = process.env) {
  const result = { ...env }
  for (const key of Object.keys(result)) {
    // A developer's own uv or Python settings must not redirect the app's environment.
    if (/^(UV_|PIP_|PYTHON|VIRTUAL_ENV$|CONDA_)/i.test(key)) delete result[key]
  }
  return withPathPrefix({
    ...result,
    UV_PYTHON_INSTALL_DIR: layout.interpreters,
    UV_CACHE_DIR: layout.cache,
    UV_NO_CONFIG: '1',
    UV_PYTHON_PREFERENCE: 'only-managed',
    UV_NO_PROGRESS: '1',
    UV_COMPILE_BYTECODE: '1',
    UV_HTTP_TIMEOUT: '300',
    VIRTUAL_ENV: layout.venv,
    VOICEVAULT_UV: uv,
    PYTHONUNBUFFERED: '1',
    PYTHONIOENCODING: 'utf-8',
    PYTHONNOUSERSITE: '1',
    // Prisma's generator writes the schema with the locale encoding; the schema is UTF-8.
    PYTHONUTF8: '1',
  }, path.join(layout.venv, 'Scripts'))
}

/** Prepends a folder to PATH, keeping one PATH entry whatever its case (Windows uses "Path"). */
function withPathPrefix(env, folder) {
  const result = { ...env }
  const key = Object.keys(result).find(name => name.toUpperCase() === 'PATH')
  const current = key ? result[key] : ''
  if (key) delete result[key]
  result.PATH = [folder, current].filter(Boolean).join(path.delimiter)
  return result
}

function setupPlan({ uv, layout, resourcesRoot, fingerprint }) {
  return [
    { title: 'Download Python 3.12', command: uv,
      args: ['python', 'install', PYTHON_VERSION, '--no-bin', '--no-registry'] },
    { title: 'Create the private Python environment', command: uv,
      args: ['venv', layout.venv, '--python', PYTHON_VERSION, '--allow-existing', '--no-project'] },
    { title: 'Install the voice engine packages', command: layout.python,
      args: [path.join(resourcesRoot, 'scripts', 'bootstrap_env.py'), '--resources', resourcesRoot,
        '--marker', layout.marker, '--fingerprint', fingerprint] },
    { title: 'Free temporary download space', command: uv, args: ['cache', 'clean'], optional: true },
  ]
}

/** Splits streamed output into lines; carriage-return progress lines count as lines too. */
function lineSplitter(onLine) {
  let pending = ''
  return {
    push(chunk) {
      pending += chunk
      const parts = pending.split(/\r\n|\n|\r/)
      pending = parts.pop()
      for (const part of parts) if (part.trim()) onLine(part)
    },
    flush() {
      if (pending.trim()) onLine(pending)
      pending = ''
    },
  }
}

function runStep(step, { env, onLine, spawnImpl = spawn, onChild }) {
  return new Promise((resolve, reject) => {
    let child
    try {
      child = spawnImpl(step.command, step.args, { env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] })
    } catch (error) {
      reject(error)
      return
    }
    onChild?.(child)
    const out = lineSplitter(onLine)
    const err = lineSplitter(onLine)
    child.stdout?.setEncoding?.('utf8')
    child.stderr?.setEncoding?.('utf8')
    child.stdout?.on('data', chunk => out.push(String(chunk)))
    child.stderr?.on('data', chunk => err.push(String(chunk)))
    child.once('error', error => reject(new Error(`${step.title} could not start: ${error.message}`)))
    child.once('close', code => {
      out.flush()
      err.flush()
      onChild?.(null)
      if (code === 0) resolve()
      else reject(new Error(`${step.title} failed (exit code ${code}).`))
    })
  })
}

/**
 * Runs the plan. onProgress({ step, total, title }) for each step; onLine(text) for output.
 * Output is also appended to the setup log. `repair` removes the old environment first.
 */
async function runSetup({ uv, layout, resourcesRoot, env = process.env, repair = false,
  onProgress = () => {}, onLine = () => {}, spawnImpl = spawn, fsImpl = fs, onChild, isCancelled = () => false }) {
  fsImpl.mkdirSync(layout.logDir, { recursive: true })
  fsImpl.mkdirSync(layout.root, { recursive: true })
  const log = fsImpl.createWriteStream(layout.setupLog, { flags: 'a' })
  const write = text => log.write(`${new Date().toISOString()} ${text}\n`)
  const emit = text => { write(text); onLine(text) }
  try {
    if (!fsImpl.existsSync(uv)) throw new Error(`The bundled uv.exe is missing (${uv}). Reinstall VoiceVault.`)
    const fingerprint = computeFingerprint(resourcesRoot, fsImpl.readFileSync)
    write(`Setup started (${repair ? 'repair' : 'install/update'}), fingerprint ${fingerprint}`)
    if (repair) {
      emit('Removing the old Python environment…')
      fsImpl.rmSync(layout.venv, { recursive: true, force: true })
    }
    fsImpl.rmSync(layout.marker, { force: true })
    const childEnv = setupEnvironment(layout, uv, env)
    const plan = setupPlan({ uv, layout, resourcesRoot, fingerprint })
    for (const [index, step] of plan.entries()) {
      if (isCancelled()) throw new Error('Setup was cancelled.')
      onProgress({ step: index + 1, total: plan.length, title: step.title })
      emit(`== ${step.title}`)
      try {
        await runStep(step, { env: childEnv, onLine: emit, spawnImpl, onChild })
      } catch (error) {
        if (!step.optional) throw error
        emit(`(skipped: ${error.message})`)
      }
    }
    const marker = readMarker(layout.marker, fsImpl.readFileSync)
    if (!marker || marker.fingerprint !== fingerprint) throw new Error('Setup finished without recording a working environment.')
    write(`Setup complete: torch ${marker.torch} (${marker.accelerator})`)
    return marker
  } catch (error) {
    write(`Setup failed: ${error.message}`)
    throw error
  } finally {
    await new Promise(resolve => log.end(resolve))
  }
}

module.exports = {
  BOOTSTRAP_VERSION,
  FINGERPRINT_FILES,
  environmentLayout,
  computeFingerprint,
  readMarker,
  setupReason,
  setupEnvironment,
  withPathPrefix,
  setupPlan,
  lineSplitter,
  runStep,
  runSetup,
}
