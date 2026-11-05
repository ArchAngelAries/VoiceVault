const fs = require('node:fs')
const path = require('node:path')
const { spawn } = require('node:child_process')

function resolvePythonExecutable(projectRoot, env = process.env, existsSync = fs.existsSync) {
  const override = env.VOICEVAULT_PYTHON?.trim()
  const executable = override || path.join(projectRoot, 'venv', 'Scripts', 'python.exe')
  if (!existsSync(executable)) {
    throw new Error(override
      ? `VOICEVAULT_PYTHON points to a missing executable: ${executable}`
      : `VoiceVault Python was not found at ${executable}. Run setup.bat or set VOICEVAULT_PYTHON.`)
  }
  return executable
}

function isVoiceVaultHealth(payload) {
  return payload?.status === 'ok'
    && payload?.service === 'voicevault-api'
    && payload?.version === '0.1.0'
}

function electronNodeEnvironment(env = process.env) {
  return { ...env, ELECTRON_RUN_AS_NODE: '1' }
}

async function waitForReady(url, { timeoutMs = 45000, intervalMs = 400, fetchImpl = fetch } = {}) {
  const deadline = Date.now() + timeoutMs
  let lastError
  while (Date.now() < deadline) {
    try {
      const response = await fetchImpl(url, { signal: AbortSignal.timeout(1500) })
      if (response.ok) return response
      lastError = new Error(`HTTP ${response.status}`)
    } catch (error) {
      lastError = error
    }
    await new Promise(resolve => setTimeout(resolve, intervalMs))
  }
  throw new Error(`Timed out waiting for ${url}${lastError ? ` (${lastError.message})` : ''}`)
}

function isOwnedProcessRunning(child, owned) {
  return Boolean(owned && child && Number.isInteger(child.pid) && child.pid > 0
    && child.exitCode == null && child.signalCode == null)
}

async function terminateOwnedProcessTree(child, owned, {
  platform = process.platform,
  env = process.env,
  spawnImpl = spawn,
  timeoutMs = 2500,
} = {}) {
  if (!isOwnedProcessRunning(child, owned)) return false
  const directFallback = () => {
    try { child.kill() } catch {}
  }
  if (platform !== 'win32') {
    directFallback()
    return true
  }

  const systemRoot = env.SystemRoot || env.WINDIR
  if (!systemRoot || !path.win32.isAbsolute(systemRoot)) {
    directFallback()
    return false
  }

  const taskkill = path.win32.join(systemRoot, 'System32', 'taskkill.exe')
  return new Promise(resolve => {
    let killer
    let settled = false
    const finish = result => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      resolve(result)
    }
    const fallback = () => {
      directFallback()
      finish(false)
    }
    const timer = setTimeout(() => {
      try { killer?.kill() } catch {}
      fallback()
    }, timeoutMs)
    try {
      killer = spawnImpl(taskkill, ['/PID', String(child.pid), '/T', '/F'], {
        windowsHide: true,
        stdio: 'ignore',
      })
      killer.once('error', fallback)
      killer.once('close', code => code === 0 ? finish(true) : fallback())
    } catch {
      fallback()
    }
  })
}

module.exports = {
  resolvePythonExecutable,
  isVoiceVaultHealth,
  electronNodeEnvironment,
  waitForReady,
  isOwnedProcessRunning,
  terminateOwnedProcessTree,
}
