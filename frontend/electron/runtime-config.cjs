const fs = require('node:fs')
const path = require('node:path')
const { randomUUID } = require('node:crypto')
const { resolveProjectRoot } = require('./packaging.cjs')
const { resolvePythonExecutable } = require('./lifecycle.cjs')

const CONFIG_FILE = 'desktop-runtime.json'
const MAX_PATH_LENGTH = 4096

function resolveDesktopUserDataDirectory(env = process.env) {
  const stateDir = env.VOICEVAULT_STATE_DIR?.trim()
  if (stateDir && !path.isAbsolute(stateDir)) {
    throw new Error('VOICEVAULT_STATE_DIR must be an absolute path. Change your launcher and reopen VoiceVault; existing files have not been moved.')
  }
  const override = env.VOICEVAULT_USER_DATA_DIR?.trim()
  return override ? path.resolve(override) : stateDir ? path.join(path.resolve(stateDir), 'desktop') : undefined
}

function readRuntimeConfiguration(userDataDir, fsImpl = fs) {
  const filePath = path.join(userDataDir, CONFIG_FILE)
  let contents
  try {
    contents = fsImpl.readFileSync(filePath, 'utf8')
  } catch (error) {
    if (error?.code === 'ENOENT') return { projectRoot: '', python: '' }
    throw new Error('Could not read the VoiceVault desktop runtime configuration.')
  }

  try {
    const parsed = JSON.parse(contents)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error()
    for (const key of ['projectRoot', 'python']) {
      if (typeof parsed[key] !== 'string' || parsed[key].length > MAX_PATH_LENGTH
        || /[\u0000-\u001f\u007f]/.test(parsed[key])) throw new Error()
    }
    return {
      projectRoot: typeof parsed.projectRoot === 'string' ? parsed.projectRoot : '',
      python: typeof parsed.python === 'string' ? parsed.python : '',
    }
  } catch {
    throw new Error('The VoiceVault desktop runtime configuration is malformed. Enter and save your project and Python paths below.')
  }
}

function effectiveRuntimeEnvironment(saved, env = process.env) {
  const effective = { ...env }
  const projectRoot = env.VOICEVAULT_PROJECT_ROOT?.trim() || saved?.projectRoot?.trim() || ''
  const python = env.VOICEVAULT_PYTHON?.trim() || saved?.python?.trim() || ''
  effective.VOICEVAULT_PROJECT_ROOT = projectRoot
  effective.VOICEVAULT_PYTHON = python
  return effective
}

function validateRuntimeConfiguration(input, env = process.env, { existsSync = fs.existsSync } = {}) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) {
    throw new Error('Runtime configuration must be an object with projectRoot and python paths.')
  }
  const keys = Object.keys(input)
  if (keys.some(key => key !== 'projectRoot' && key !== 'python')) {
    throw new Error('Runtime configuration may contain only projectRoot and python paths.')
  }
  for (const key of ['projectRoot', 'python']) {
    if (typeof input[key] !== 'string') throw new Error(`${key} must be a string.`)
    if (input[key].length > MAX_PATH_LENGTH) throw new Error(`${key} must be 4096 characters or fewer.`)
    if (/[\u0000-\u001f\u007f]/.test(input[key])) throw new Error(`${key} cannot contain control characters.`)
  }

  const result = { projectRoot: input.projectRoot.trim(), python: input.python.trim() }
  if (!result.projectRoot || !path.win32.isAbsolute(result.projectRoot)) {
    throw new Error('Project folder must be an absolute path containing backend/main.py.')
  }
  const effective = effectiveRuntimeEnvironment(result, env)
  const projectRoot = resolveProjectRoot({
    isPackaged: true,
    appDir: '',
    env: effective,
    existsSync,
  })
  if (result.python && !path.win32.isAbsolute(result.python)) {
    throw new Error('Python must be an absolute path to python.exe.')
  }
  if (result.python && path.win32.extname(result.python).toLowerCase() !== '.exe') {
    throw new Error('Python must be an absolute path to python.exe.')
  }
  resolvePythonExecutable(projectRoot, effective, existsSync)
  return result
}

function saveRuntimeConfiguration(userDataDir, config, fsImpl = fs) {
  const normalized = { projectRoot: config.projectRoot, python: config.python }
  const directory = path.resolve(userDataDir)
  const target = path.join(directory, CONFIG_FILE)
  const temporary = path.join(directory, `.${CONFIG_FILE}.${randomUUID()}.tmp`)
  fsImpl.mkdirSync(directory, { recursive: true })
  try {
    fsImpl.writeFileSync(temporary, `${JSON.stringify(normalized, null, 2)}\n`, { encoding: 'utf8', flag: 'wx' })
    fsImpl.renameSync(temporary, target)
  } catch (error) {
    try { fsImpl.unlinkSync(temporary) } catch {}
    throw new Error('Could not save the VoiceVault desktop runtime configuration.')
  }
  return normalized
}

module.exports = {
  readRuntimeConfiguration,
  effectiveRuntimeEnvironment,
  validateRuntimeConfiguration,
  saveRuntimeConfiguration,
  resolveDesktopUserDataDirectory,
}
