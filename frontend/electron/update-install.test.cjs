const test = require('node:test')
const assert = require('node:assert/strict')
const crypto = require('node:crypto')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { verifyInstaller } = require('./update-install.cjs')

function setup(name = 'VoiceVault-Setup-0.2.0.exe', folder = 'updates') {
  const dir = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'vv-upd-')), folder)
  fs.mkdirSync(dir)
  const file = path.join(dir, name)
  fs.writeFileSync(file, 'MZ installer')
  return { file, sha256: crypto.createHash('sha256').update('MZ installer').digest('hex') }
}

test('a matching installer in the updates folder is allowed', async () => {
  const { file, sha256 } = setup()
  assert.equal(await verifyInstaller({ path: file, sha256 }), file)
})

test('a changed file is refused', async () => {
  const { file, sha256 } = setup()
  fs.writeFileSync(file, 'MZ something else')
  await assert.rejects(verifyInstaller({ path: file, sha256 }), /changed/)
})

test('wrong names, folders and missing hashes are refused', async () => {
  const other = setup('evil.exe')
  await assert.rejects(verifyInstaller({ path: other.file, sha256: other.sha256 }), /not a VoiceVault installer/)
  const elsewhere = setup('VoiceVault-Setup-0.2.0.exe', 'Downloads')
  await assert.rejects(verifyInstaller({ path: elsewhere.file, sha256: elsewhere.sha256 }), /updates folder/)
  const good = setup()
  await assert.rejects(verifyInstaller({ path: good.file, sha256: 'abc' }), /fingerprint/)
  await assert.rejects(verifyInstaller({ path: 'VoiceVault-Setup-0.2.0.exe', sha256: good.sha256 }), /not a VoiceVault installer/)
})
