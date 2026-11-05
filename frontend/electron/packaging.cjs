const fs = require('node:fs')
const path = require('node:path')

function resolveProjectRoot({ isPackaged, appDir, env = process.env, existsSync = fs.existsSync }) {
  const configuredRoot = env.VOICEVAULT_PROJECT_ROOT?.trim()
  let projectRoot
  if (isPackaged) {
    if (!configuredRoot || !path.isAbsolute(configuredRoot)) {
      throw new Error('Enter the absolute path of your VoiceVault project folder below. It must contain backend/main.py. This package also needs your existing compatible Python environment. VOICEVAULT_PROJECT_ROOT is available as a launcher override.')
    }
    projectRoot = path.resolve(configuredRoot)
  } else {
    // appDir is the directory containing electron/main.cjs.
    projectRoot = path.resolve(appDir, '../..')
  }

  if (!existsSync(path.join(projectRoot, 'backend', 'main.py'))) {
    throw new Error(`VoiceVault backend code was not found under ${projectRoot}. Choose a full project folder containing backend/main.py below, or change VOICEVAULT_PROJECT_ROOT in your launcher. Choose your Python executable below if the project has no compatible venv.`)
  }
  return projectRoot
}

/** The installed app's writable folder: VOICEVAULT_STATE_DIR, else %LOCALAPPDATA%\VoiceVault. */
function defaultStateDirectory(env = process.env) {
  const explicit = env.VOICEVAULT_STATE_DIR?.trim()
  if (explicit) {
    if (!path.isAbsolute(explicit)) throw new Error('VOICEVAULT_STATE_DIR must be an absolute path.')
    return path.resolve(explicit)
  }
  const local = env.LOCALAPPDATA?.trim()
  if (!local || !path.isAbsolute(local)) {
    throw new Error('Windows did not report a local application data folder (LOCALAPPDATA). Set VOICEVAULT_STATE_DIR to a full folder path and reopen VoiceVault.')
  }
  return path.join(path.resolve(local), 'VoiceVault')
}

/**
 * Where the backend code comes from:
 *  - development: this repository;
 *  - external: a packaged shell pointed at a project folder (VOICEVAULT_PROJECT_ROOT or saved paths);
 *  - bundled: the installer's resources/voicevault folder with the first-run Python environment.
 */
function resolveDesktopLayout({ isPackaged, appDir, resourcesPath, env = process.env, existsSync = fs.existsSync }) {
  if (!isPackaged) return { mode: 'development', projectRoot: resolveProjectRoot({ isPackaged, appDir, env, existsSync }) }
  if (env.VOICEVAULT_PROJECT_ROOT?.trim()) {
    return { mode: 'external', projectRoot: resolveProjectRoot({ isPackaged, appDir, env, existsSync }) }
  }
  if (!resourcesPath || !path.isAbsolute(resourcesPath)) throw new Error('The installed VoiceVault resources folder was not found. Reinstall VoiceVault.')
  const projectRoot = path.join(resourcesPath, 'voicevault')
  if (!existsSync(path.join(projectRoot, 'backend', 'main.py'))) {
    throw new Error('The installed VoiceVault files are incomplete (backend/main.py is missing). Reinstall VoiceVault; your library and settings are kept.')
  }
  return {
    mode: 'bundled',
    projectRoot,
    stateDir: defaultStateDirectory(env),
    uv: path.join(projectRoot, 'uv', 'uv.exe'),
  }
}

/** Backend process environment for the installed app. Explicit variables always win. */
function bundledBackendEnvironment(env, { stateDir, python }) {
  const result = { ...env }
  const setDefault = (key, value) => { if (!result[key]?.trim()) result[key] = value }
  setDefault('VOICEVAULT_STATE_DIR', stateDir)
  setDefault('VOICEVAULT_PYTHON', python)
  setDefault('VOICEVAULT_MODELS_DIR', path.join(stateDir, 'Models'))
  setDefault('VOICEVAULT_RUNTIME_DIR', path.join(stateDir, 'runtime'))
  // Hugging Face downloads from Settings -> Models land in the state folder too, unless the user
  // already chose a cache location.
  if (!result.HF_HOME?.trim() && !result.HF_HUB_CACHE?.trim()) result.HF_HOME = path.join(stateDir, 'huggingface')
  result.PYTHONNOUSERSITE = '1'
  result.PYTHONUTF8 = '1'
  result.PYTHONIOENCODING = 'utf-8'
  const pathKey = Object.keys(result).find(name => name.toUpperCase() === 'PATH')
  const current = pathKey ? result[pathKey] : ''
  if (pathKey) delete result[pathKey]
  result.PATH = [path.dirname(result.VOICEVAULT_PYTHON), current].filter(Boolean).join(path.delimiter)
  return result
}

/** Backend port: 7763, or VOICEVAULT_BACKEND_PORT for isolated checks beside another instance. */
function backendPort(env = process.env) {
  const raw = env.VOICEVAULT_BACKEND_PORT?.trim()
  if (!raw) return 7763
  const port = Number(raw)
  if (!/^\d+$/.test(raw) || port < 1024 || port > 65535) {
    throw new Error('VOICEVAULT_BACKEND_PORT must be a whole number from 1024 to 65535.')
  }
  return port
}

module.exports = { resolveProjectRoot, defaultStateDirectory, resolveDesktopLayout, bundledBackendEnvironment, backendPort }
