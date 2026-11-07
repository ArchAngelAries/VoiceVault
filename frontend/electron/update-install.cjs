/**
 * electron/update-install.cjs — run a downloaded VoiceVault update, after checking it again here.
 *
 * The backend downloads the installer and checks it against GitHub's SHA-256. The desktop app
 * re-checks the name and the hash itself before running it, so a file swapped in afterwards is
 * never started. It only runs when the user clicks "Install now".
 */
const crypto = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')

const INSTALLER_NAME = /^VoiceVault-Setup-\d+\.\d+\.\d+\.exe$/

function sha256File(file, fsImpl = fs) {
  return new Promise((resolve, reject) => {
    const hash = crypto.createHash('sha256')
    fsImpl.createReadStream(file).on('data', chunk => hash.update(chunk)).on('error', reject)
      .on('end', () => resolve(hash.digest('hex')))
  })
}

/** Resolves to the installer path when it is safe to run; throws a plain message otherwise. */
async function verifyInstaller(info, fsImpl = fs) {
  const file = typeof info?.path === 'string' ? info.path : ''
  const expected = typeof info?.sha256 === 'string' ? info.sha256.toLowerCase() : ''
  if (!file || !path.isAbsolute(file) || !INSTALLER_NAME.test(path.basename(file))) throw new Error('This is not a VoiceVault installer.')
  if (!/^[0-9a-f]{64}$/.test(expected)) throw new Error('The installer has no fingerprint to check.')
  if (path.basename(path.dirname(file)) !== 'updates') throw new Error('The installer is not in VoiceVault’s updates folder.')
  if (!fsImpl.existsSync(file)) throw new Error('The downloaded installer is missing. Download it again.')
  if (await sha256File(file, fsImpl) !== expected) throw new Error('The installer changed after it was checked, so it was not started. Download it again.')
  return file
}

module.exports = { INSTALLER_NAME, sha256File, verifyInstaller }
