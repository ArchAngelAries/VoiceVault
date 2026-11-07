const path = require('node:path')

module.exports = {
  appId: 'app.voicevault.desktop',
  productName: 'VoiceVault',
  copyright: 'Copyright © VoiceVault contributors',
  directories: { output: 'release', buildResources: 'electron/icons' },
  files: [
    'electron/main.cjs',
    'electron/preload.cjs',
    'electron/lifecycle.cjs',
    'electron/packaging.cjs',
    'electron/runtime-config.cjs',
    'electron/bootstrap.cjs',
    'electron/update-install.cjs',
    'electron/icons/**/*',
    'dist/**/*',
    'package.json',
    '!node_modules/**',
    '!**/node_modules/**',
    '!**/venv/**',
    '!**/data/**',
    '!**/*settings*.json',
    '!**/*.db',
    '!**/*.env',
    '!**/models/**',
    '!**/weights/**',
  ],
  // The clean backend/runtime/uv tree made (and audited) by electron/stage-resources.cjs.
  extraResources: [
    { from: 'release-staging/voicevault', to: 'voicevault' },
    { from: 'electron/icons/icon.ico', to: 'icon.ico' },
  ],
  asar: true,
  npmRebuild: false,
  electronDist: path.resolve(__dirname, '../node_modules/electron/dist'),
  electronVersion: require('../node_modules/electron/package.json').version,
  // Not code-signed (no certificate configured). signAndEditExecutable stays on so the app's
  // .exe gets the VoiceVault icon and version info; electron-builder fetches winCodeSign for that.
  win: { target: [{ target: 'nsis', arch: ['x64'] }], signAndEditExecutable: true, icon: 'electron/icons/icon.ico' },
  nsis: {
    oneClick: false,
    perMachine: false,
    allowElevation: false,
    allowToChangeInstallationDirectory: true,
    installerIcon: 'electron/icons/icon.ico',
    uninstallerIcon: 'electron/icons/icon.ico',
    createDesktopShortcut: true,
    createStartMenuShortcut: true,
    shortcutName: 'VoiceVault',
    uninstallDisplayName: 'VoiceVault',
    artifactName: 'VoiceVault-Setup-${version}.${ext}',
    // User data lives in %LOCALAPPDATA%\VoiceVault; installer.nsh asks before removing it.
    deleteAppDataOnUninstall: false,
    include: 'electron/installer.nsh',
    differentialPackage: false,
  },
}
