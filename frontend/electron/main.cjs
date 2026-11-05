/** Electron owns the development servers and the single native app window. */
const { app, BrowserWindow, shell, ipcMain } = require('electron')
const { spawn } = require('node:child_process')
const fs = require('node:fs')
const path = require('node:path')
const net = require('node:net')
const {
  resolveDesktopLayout,
  defaultStateDirectory,
  bundledBackendEnvironment,
  backendPort,
} = require('./packaging.cjs')
const bootstrap = require('./bootstrap.cjs')
const {
  readRuntimeConfiguration,
  effectiveRuntimeEnvironment,
  validateRuntimeConfiguration,
  saveRuntimeConfiguration,
  resolveDesktopUserDataDirectory,
} = require('./runtime-config.cjs')
const {
  resolvePythonExecutable,
  isVoiceVaultHealth,
  electronNodeEnvironment,
  waitForReady,
  isOwnedProcessRunning,
  terminateOwnedProcessTree,
} = require('./lifecycle.cjs')

let requestedUserDataDir
let startupStateError
// The installed app keeps all writable state in %LOCALAPPDATA%\VoiceVault (or VOICEVAULT_STATE_DIR).
// VOICEVAULT_PROJECT_ROOT keeps the older "point the shell at a project folder" mode for developers.
if (app.isPackaged && !process.env.VOICEVAULT_PROJECT_ROOT?.trim() && !process.env.VOICEVAULT_STATE_DIR?.trim()) {
  try { process.env.VOICEVAULT_STATE_DIR = defaultStateDirectory(process.env) } catch (error) { startupStateError = error }
}
try { requestedUserDataDir = resolveDesktopUserDataDirectory() } catch (error) { startupStateError ||= error }
let servicePort = 7763
try { servicePort = backendPort(process.env) } catch (error) { startupStateError ||= error }
if (requestedUserDataDir) {
  const userDataDir = path.resolve(requestedUserDataDir)
  fs.mkdirSync(userDataDir, { recursive: true })
  app.setPath('userData', userDataDir)
}

const gotLock = app.requestSingleInstanceLock()
if (!gotLock) app.quit()

const isDev = !app.isPackaged
const frontendRoot = path.resolve(__dirname, '..')
let projectRoot
let backendRoot
let desktopLayout
const backendUrl = `http://127.0.0.1:${servicePort}`
const frontendUrl = 'http://127.0.0.1:5179'
let mainWindow
let booting = false
let backendProcess
let viteProcess
let ownsBackend = false
let ownsVite = false
let closing = false
let shutdownPromise
let shutdownComplete = false
let logStream
let runtimeEnv = process.env
let startupUrl
let setupChild
let repairRequested = process.argv.includes('--repair')

function log(message) {
  const line = `${new Date().toISOString()} ${message}\n`
  logStream?.write(line)
}

function openLog() {
  const dir = path.join(app.getPath('userData'), 'logs')
  fs.mkdirSync(dir, { recursive: true })
  logStream = fs.createWriteStream(path.join(dir, 'desktop.log'), { flags: 'a' })
  log(`VoiceVault starting (${isDev ? 'development' : 'packaged'})`)
}

function checkPort(port) {
  return new Promise(resolve => {
    const socket = net.connect({ host: '127.0.0.1', port })
    socket.setTimeout(500)
    socket.once('connect', () => { socket.destroy(); resolve(true) })
    socket.once('timeout', () => { socket.destroy(); resolve(false) })
    socket.once('error', () => resolve(false))
  })
}

async function isVoiceVaultBackend(timeoutMs = 45000) {
  try {
    const response = await waitForReady(`${backendUrl}/api/health/`, { timeoutMs, intervalMs: 150 })
    return isVoiceVaultHealth(await response.json())
  } catch {
    return false
  }
}

function waitForChild(processRef, label) {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error(`${label} did not start in time`)), 45000)
    processRef.once('spawn', () => { clearTimeout(timeout); resolve() })
    processRef.once('error', error => { clearTimeout(timeout); reject(new Error(`${label} failed to launch: ${error.message}`)) })
    processRef.once('exit', (code, signal) => {
      clearTimeout(timeout)
      if (!closing) log(`${label} exited (code=${code}, signal=${signal})`)
      if (!closing && !booting && mainWindow && !mainWindow.isDestroyed()) {
        showStartup(`${label} stopped unexpectedly.`, 'Retry startup to restart VoiceVault services.').catch(error => log(error.message))
      }
    })
    processRef.stdout?.on('data', chunk => log(`${label}: ${chunk.toString().trimEnd()}`))
    processRef.stderr?.on('data', chunk => log(`${label}: ${chunk.toString().trimEnd()}`))
  })
}

async function startBackend() {
  if (closing) return
  if (isOwnedProcessRunning(backendProcess, ownsBackend)) {
    await waitForReady(`${backendUrl}/api/health/`)
    return
  }
  const occupied = await checkPort(servicePort)
  if (closing) return
  if (occupied) {
    if (await isVoiceVaultBackend(2500)) {
      log('Using an already-running VoiceVault backend; this app will not stop it')
      return
    }
    throw new Error(`Port ${servicePort} is already in use by an unrecognized service. Close it or choose another service port before launching VoiceVault.`)
  }

  const python = resolvePythonExecutable(projectRoot, runtimeEnv)
  backendProcess = spawn(python, ['-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', String(servicePort)], {
    cwd: backendRoot,
    env: { ...runtimeEnv, MIOPEN_FIND_MODE: runtimeEnv.MIOPEN_FIND_MODE || '2' },
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  ownsBackend = true
  await waitForChild(backendProcess, 'Backend')
  log(`Owned backend PID ${backendProcess.pid}`)
  if (!await isVoiceVaultBackend(120000)) throw new Error(`The service on port ${servicePort} did not identify itself as VoiceVault.`)
}

async function startVite() {
  if (closing) return
  if (isOwnedProcessRunning(viteProcess, ownsVite)) {
    await waitForReady(frontendUrl)
    return
  }
  const occupied = await checkPort(5179)
  if (closing) return
  if (occupied) {
    throw new Error('Port 5179 is already in use. Close the other frontend server before launching the desktop app.')
  }
  const viteCli = path.join(frontendRoot, 'node_modules', 'vite', 'bin', 'vite.js')
  if (!fs.existsSync(viteCli)) throw new Error('Frontend dependencies are missing. Run npm install in the frontend folder.')
  viteProcess = spawn(process.execPath, [viteCli, '--host', '127.0.0.1', '--port', '5179', '--strictPort'], {
    cwd: frontendRoot,
    env: electronNodeEnvironment(),
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  ownsVite = true
  await waitForChild(viteProcess, 'Frontend')
  await waitForReady(frontendUrl)
}

function logoDataUrl() {
  try {
    const svg = fs.readFileSync(path.join(frontendRoot, 'dist', 'logo.svg'))
    return `data:image/svg+xml;base64,${svg.toString('base64')}`
  } catch {
    return ''
  }
}

function startupHtml() {
  const logo = logoDataUrl()
  return `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>VoiceVault</title>
  <style>*{box-sizing:border-box}body{margin:0;padding:16px;background:#0a0a0f;color:#eee;font:15px/1.5 Segoe UI,sans-serif;display:grid;place-items:center;min-height:100vh}.card{width:min(680px,100%);padding:clamp(20px,5vw,32px);border:1px solid #292833;border-radius:16px;background:#111118;box-shadow:0 24px 80px #0008}.head{display:flex;align-items:center;gap:14px}.head img{width:48px;height:48px;border-radius:12px}h1{margin:0;font-size:24px}p{color:#aaa;margin:8px 0 18px}.status{color:#bdb7ff}.error{color:#ffaaa8;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word}button{background:#9a8cff;color:#111;border:0;border-radius:8px;padding:10px 16px;font-weight:600;cursor:pointer;margin:0 8px 8px 0}button.secondary{background:#2a2836;color:#eee}button:disabled{opacity:.5;cursor:wait}button:focus-visible,input:focus-visible{outline:3px solid #eee;outline-offset:3px}small{display:block;color:#aaa;margin-top:12px}label{display:block;margin:14px 0 6px}input{width:100%;background:#09090f;color:#eee;border:1px solid #494653;border-radius:6px;padding:10px;font:inherit}input:read-only{color:#aaa}form{border-top:1px solid #292833;margin-top:20px;padding-top:6px}form button{margin-top:18px}.bar{height:8px;border-radius:4px;background:#22202c;overflow:hidden;margin:4px 0 10px}.bar>div{height:100%;width:0;background:linear-gradient(90deg,#7040e0,#d646a8);transition:width .4s}#step{color:#ddd;font-weight:600}pre{background:#09090f;border:1px solid #292833;border-radius:8px;padding:10px;height:220px;overflow:auto;font:12px/1.45 Consolas,monospace;color:#bbb;white-space:pre-wrap;word-break:break-all;margin:8px 0 14px}[hidden]{display:none!important}</style></head>
  <body><main class="card"><div class="head">${logo ? `<img src="${logo}" alt="">` : ''}<h1>VoiceVault</h1></div><p id="status" class="status" role="status" aria-live="polite">Starting your local services…</p>
  <section id="setup" hidden><p id="setupintro">VoiceVault is setting up its private Python engine. This happens once, downloads several gigabytes (the graphics-card libraries are large) and can take 10 to 40 minutes. You can keep using your PC; closing this window stops setup, and it continues next time.</p><div id="step"></div><div class="bar" role="progressbar" aria-label="Setup progress"><div id="progress"></div></div><pre id="log" aria-label="Setup details"></pre></section>
  <p id="error" class="error" role="alert" aria-live="assertive"></p><div><button id="retry" hidden>Try again</button><button id="repair" class="secondary" hidden>Repair installation</button><button id="logs" class="secondary" hidden>Open log folder</button></div>
  <form id="runtime" hidden><p>This desktop package needs your existing VoiceVault project and a compatible Python environment.</p><label for="project">VoiceVault project folder</label><input id="project" required spellcheck="false" placeholder="Full folder path containing backend/main.py"><label for="python">Python executable (optional)</label><input id="python" spellcheck="false" placeholder="Leave empty to use the project's venv"><small id="override"></small><small>Uses the selected project's existing library and settings, or your configured state overrides. Saving these paths does not move your files or install anything.</small><button id="save" type="submit">Save paths and try again</button></form>
  <small>Startup details are saved in the VoiceVault logs folder.</small></main>
  <script>
  window.formDirty=false;
  window.appendSetupLines=function(lines){const log=document.getElementById('log');const atEnd=log.scrollTop+log.clientHeight>=log.scrollHeight-24;log.textContent+=lines.join('\\n')+'\\n';if(log.textContent.length>60000)log.textContent=log.textContent.slice(-40000);if(atEnd)log.scrollTop=log.scrollHeight};
  function setBusy(value){document.querySelectorAll('button').forEach(button=>button.disabled=value)}
  async function act(action){setBusy(true);try{await action()}catch(error){document.getElementById('error').textContent=error.message}finally{setBusy(false)}}
  document.getElementById('runtime').addEventListener('input',()=>{window.formDirty=true});
  document.getElementById('retry').addEventListener('click',()=>act(()=>window.vault.retryStartup()));
  document.getElementById('repair').addEventListener('click',()=>act(()=>window.vault.repairInstallation()));
  document.getElementById('logs').addEventListener('click',()=>act(()=>window.vault.openLogFolder()));
  document.getElementById('runtime').addEventListener('submit',async(event)=>{event.preventDefault();setBusy(true);try{const result=await window.vault.saveRuntimeConfiguration({projectRoot:document.getElementById('project').value,python:document.getElementById('python').value});if(result.error)document.getElementById('error').textContent=result.error;else window.formDirty=false}catch(error){document.getElementById('error').textContent=error.message}finally{setBusy(false)}});
  </script></body></html>`
}

async function ensureStartupPage() {
  if (!mainWindow || mainWindow.isDestroyed()) return false
  if (!startupUrl || mainWindow.webContents.getURL() !== startupUrl) {
    startupUrl = `data:text/html;charset=utf-8,${encodeURIComponent(startupHtml())}`
    await mainWindow.loadURL(startupUrl)
  }
  return true
}

async function showStartup(message, error) {
  if (!await ensureStartupPage()) return
  const bundled = desktopLayout?.mode === 'bundled'
  await mainWindow.webContents.executeJavaScript(`document.getElementById('status').textContent=${JSON.stringify(message)};document.getElementById('error').textContent=${JSON.stringify(error || '')};document.getElementById('retry').hidden=${!error};document.getElementById('repair').hidden=${!(error && bundled)};document.getElementById('logs').hidden=${!(error && !isDev)}`)
  if (!isDev && !bundled) {
    let saved = {}
    try { saved = readRuntimeConfiguration(app.getPath('userData')) } catch {}
    const env = effectiveRuntimeEnvironment(saved)
    const config = { projectRoot: env.VOICEVAULT_PROJECT_ROOT || '', python: env.VOICEVAULT_PYTHON || '',
      projectLocked: Boolean(process.env.VOICEVAULT_PROJECT_ROOT?.trim()), pythonLocked: Boolean(process.env.VOICEVAULT_PYTHON?.trim()) }
    await mainWindow.webContents.executeJavaScript(`(()=>{const c=${JSON.stringify(config)};document.getElementById('runtime').hidden=${!error};if(!window.formDirty){document.getElementById('project').value=c.projectRoot;document.getElementById('python').value=c.python}document.getElementById('project').readOnly=c.projectLocked;document.getElementById('python').readOnly=c.pythonLocked;document.getElementById('override').textContent=(c.projectLocked||c.pythonLocked)?'Paths set by environment variables are read-only here. Change them in your launcher, then reopen VoiceVault.':''})()`)
  }
}

/** Shows first-run setup progress on the startup page, batching output lines. */
function setupView() {
  let pending = []
  let timer
  const run = code => {
    if (!mainWindow || mainWindow.isDestroyed() || mainWindow.webContents.getURL() !== startupUrl) return
    mainWindow.webContents.executeJavaScript(code).catch(() => {})
  }
  const flush = () => {
    timer = undefined
    if (!pending.length) return
    const lines = pending.slice(-400)
    pending = []
    run(`window.appendSetupLines(${JSON.stringify(lines)})`)
  }
  return {
    show(reason) {
      const intro = reason === 'update' ? 'VoiceVault was updated and is updating its Python engine. This is usually quick.'
        : reason === 'repair' ? 'VoiceVault is rebuilding its private Python engine. The graphics-card libraries are downloaded again, so this can take 10 to 40 minutes. Your library, settings and models are kept.'
          : null
      run(`document.getElementById('setup').hidden=false;document.getElementById('log').textContent='';${intro ? `document.getElementById('setupintro').textContent=${JSON.stringify(intro)};` : ''}`)
    },
    progress({ step, total, title }) {
      run(`document.getElementById('step').textContent=${JSON.stringify(`Step ${step} of ${total}: ${title}`)};document.getElementById('progress').style.width='${Math.round(((step - 1) / total) * 100)}%'`)
    },
    line(text) {
      pending.push(text.length > 400 ? `${text.slice(0, 400)}…` : text)
      if (!timer) timer = setTimeout(flush, 200)
    },
    done() {
      flush()
      run(`document.getElementById('progress').style.width='100%'`)
    },
  }
}

/** Runs first-run setup when needed and returns the backend environment for the installed app. */
async function prepareBundledRuntime(layout, env) {
  const environment = bootstrap.environmentLayout(layout.stateDir)
  const pythonOverride = env.VOICEVAULT_PYTHON?.trim()
  if (!pythonOverride) {
    const reason = repairRequested ? 'repair'
      : bootstrap.setupReason(environment, bootstrap.computeFingerprint(layout.projectRoot))
    if (reason) {
      log(`Python environment setup needed (${reason})`)
      await showStartup(reason === 'update' ? 'Updating VoiceVault…' : 'Setting up VoiceVault…')
      const view = setupView()
      view.show(reason)
      try {
        const marker = await bootstrap.runSetup({
          uv: layout.uv,
          layout: environment,
          resourcesRoot: layout.projectRoot,
          env,
          repair: reason === 'repair',
          onProgress: info => view.progress(info),
          onLine: text => view.line(text),
          onChild: child => { setupChild = child },
          isCancelled: () => closing,
        })
        repairRequested = false
        log(`Python environment ready: torch ${marker.torch} (${marker.accelerator})`)
      } finally {
        view.done()
      }
      if (closing) return env
      await showStartup('Starting your local services…')
    }
  }
  return bundledBackendEnvironment(env, { stateDir: layout.stateDir, python: pythonOverride || environment.python })
}

function logFolder() {
  return desktopLayout?.mode === 'bundled'
    ? bootstrap.environmentLayout(desktopLayout.stateDir).logDir
    : path.join(app.getPath('userData'), 'logs')
}

async function boot() {
  if (booting || closing) return
  booting = true
  try {
    await showStartup('Starting your local services…')
    if (closing) return
    if (startupStateError) throw startupStateError
    runtimeEnv = isDev ? process.env : effectiveRuntimeEnvironment(readRuntimeConfiguration(app.getPath('userData')))
    desktopLayout = resolveDesktopLayout({ isPackaged: app.isPackaged, appDir: __dirname, resourcesPath: process.resourcesPath, env: runtimeEnv })
    projectRoot = desktopLayout.projectRoot
    backendRoot = path.join(projectRoot, 'backend')
    if (desktopLayout.mode === 'bundled') runtimeEnv = await prepareBundledRuntime(desktopLayout, runtimeEnv)
    if (closing) return
    await startBackend()
    if (closing) return
    if (isDev) await startVite()
    if (closing) return
    if (!mainWindow || mainWindow.isDestroyed()) return
    if (isDev) await mainWindow.loadURL(frontendUrl)
    else await mainWindow.loadFile(path.join(frontendRoot, 'dist', 'index.html'))
    log('Application window is ready')
  } catch (error) {
    log(error.stack || error.message)
    if (!closing) {
      const where = desktopLayout?.mode === 'bundled' ? bootstrap.environmentLayout(desktopLayout.stateDir).logDir : path.join(app.getPath('userData'), 'logs', 'desktop.log')
      await showStartup('VoiceVault could not start.', `${error.message}\n\nLog: ${where}`)
    }
  } finally {
    booting = false
  }
}

async function stopOwnedProcesses() {
  closing = true
  await Promise.all([
    terminateOwnedProcessTree(viteProcess, ownsVite),
    terminateOwnedProcessTree(backendProcess, ownsBackend),
    terminateOwnedProcessTree(setupChild, Boolean(setupChild)),
  ])
  log('VoiceVault closed')
  logStream?.end()
}

async function createWindow() {
  mainWindow = new BrowserWindow({
    show: false,
    width: 1400,
    height: 900,
    minWidth: 900,
    minHeight: 600,
    backgroundColor: '#0a0a0f',
    title: 'VoiceVault',
    icon: path.join(__dirname, 'icons', process.platform === 'win32' ? 'icon.ico' : 'icon.png'),
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.cjs'),
      // The installed renderer learns the backend address from here (VOICEVAULT_BACKEND_PORT).
      additionalArguments: isDev ? [] : [`--voicevault-backend-url=${backendUrl}`],
    },
  })
  mainWindow.setMenuBarVisibility(false)
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/i.test(url)) shell.openExternal(url)
    return { action: 'deny' }
  })
  startupUrl = `data:text/html;charset=utf-8,${encodeURIComponent(startupHtml())}`
  await mainWindow.loadURL(startupUrl)
  mainWindow.show()
  mainWindow.focus()
  mainWindow.on('closed', () => { mainWindow = null })
}

function isStartupSender(event) {
  return Boolean(mainWindow && !mainWindow.isDestroyed() && startupUrl
    && event.sender === mainWindow.webContents
    && event.senderFrame === mainWindow.webContents.mainFrame
    && mainWindow.webContents.getURL() === startupUrl)
}

async function configureRuntime(event, input) {
  if (!isStartupSender(event) || isDev) return { error: 'Runtime paths can only be changed from packaged startup recovery.' }
  if (booting || closing) return { error: 'Wait for startup to finish before changing paths.' }
  if (isOwnedProcessRunning(backendProcess, ownsBackend)) return { error: 'A backend is still running. Close and reopen VoiceVault before changing runtime paths.' }
  try {
    const config = validateRuntimeConfiguration(input)
    saveRuntimeConfiguration(app.getPath('userData'), config)
    await boot()
    return { saved: true }
  } catch (error) {
    return { error: error.message }
  }
}

async function repairInstallation(event) {
  if (!isStartupSender(event) || desktopLayout?.mode !== 'bundled') return { error: 'Repair is only available in the installed app.' }
  if (booting || closing) return { error: 'Wait for startup to finish before repairing.' }
  if (isOwnedProcessRunning(backendProcess, ownsBackend)) return { error: 'VoiceVault is running. Close and reopen it, then choose Repair installation.' }
  repairRequested = true
  await boot()
  return { started: true }
}

if (gotLock) {
  ipcMain.handle('retry-startup', event => isStartupSender(event) ? boot() : undefined)
  ipcMain.handle('repair-installation', repairInstallation)
  ipcMain.handle('open-log-folder', async event => {
    if (!isStartupSender(event)) return
    const folder = logFolder()
    fs.mkdirSync(folder, { recursive: true })
    await shell.openPath(folder)
  })
  ipcMain.handle('save-runtime-configuration', configureRuntime)
  app.on('second-instance', () => {
    if (!mainWindow) createWindow().then(boot).catch(error => log(error.message))
    else {
      if (mainWindow.isMinimized()) mainWindow.restore()
      mainWindow.show()
      mainWindow.focus()
    }
  })
  app.whenReady().then(async () => {
    openLog()
    await createWindow()
    boot()
  })
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow().then(boot).catch(error => log(error.message))
    }
  })
  app.on('before-quit', event => {
    if (shutdownComplete) return
    event.preventDefault()
    if (shutdownPromise) return
    shutdownPromise = stopOwnedProcesses().finally(() => {
      shutdownComplete = true
      app.quit()
    })
  })
  app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit() })
}
