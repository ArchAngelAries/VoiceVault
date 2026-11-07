/**
 * electron/preload.cjs — Electron preload (context bridge)
 * Exposes safe Node/Electron APIs to the renderer.
 * Add APIs here as needed — never expose full Node in renderer.
 */
const { contextBridge, ipcRenderer } = require('electron')

// The installed app passes the backend address it started (normally http://127.0.0.1:7763).
function backendUrlFromArguments(argv = process.argv) {
  const prefix = '--voicevault-backend-url='
  const value = argv.find(arg => typeof arg === 'string' && arg.startsWith(prefix))?.slice(prefix.length)
  return value && /^http:\/\/127\.0\.0\.1:\d{4,5}$/.test(value) ? value : undefined
}

contextBridge.exposeInMainWorld('vault', {
  platform: process.platform,
  isElectron: true,
  backendUrl: backendUrlFromArguments(),
  retryStartup: () => ipcRenderer.invoke('retry-startup'),
  repairInstallation: () => ipcRenderer.invoke('repair-installation'),
  openLogFolder: () => ipcRenderer.invoke('open-log-folder'),
  saveRuntimeConfiguration: paths => ipcRenderer.invoke('save-runtime-configuration', paths),
  installUpdate: () => ipcRenderer.invoke('install-update'),
  onMenuAction: (cb) => ipcRenderer.on('menu-action', (_, action) => cb(action)),
})
