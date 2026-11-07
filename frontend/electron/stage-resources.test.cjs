const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { COPIES, isExcluded, isExcludedBackendCode, isUpstreamBinary, audit, auditPatterns } = require('./stage-resources.cjs')

test('release exclusions match the personal-data checklist', () => {
  for (const excluded of ['backend/voicevault_settings.json', 'backend/voicevault_settings.local.json', 'backend/db/voicevault.db',
    'backend/db/voicevault.db-journal', 'backend/db/x.corrupt-1', 'backend/core/__pycache__/x.pyc', 'backend/.pytest_cache/x',
    'backend/api/.env', 'backend/x.log']) assert.equal(isExcluded(excluded), true, excluded)
  for (const kept of ['backend/voicevault_settings.example.json', 'backend/main.py', 'backend/db/schema.prisma',
    'runtime/audiocpp/v0.9.3-vulkan-portable/audiocpp_server.exe']) assert.equal(isExcluded(kept), false, kept)
  assert.equal(isExcludedBackendCode('backend/tests/test_x.py'), true)
  assert.equal(isExcludedBackendCode('backend/core/test_discovery_snapshots.py'), true)
  assert.equal(isExcludedBackendCode('backend/data/x.json'), true)
  assert.equal(isExcludedBackendCode('backend/core/paths.py'), false)
})

test('the allowlist never names personal or internal files', () => {
  const sources = COPIES.map(item => item.from)
  for (const forbidden of ['data', 'Library', 'venv', 'Voices', '.claude', 'backend/voicevault_settings.json', 'docs/HANDOFF.md',
    'docs/DEVELOPMENT_STATUS.md', 'docs/REFERENCE_SOURCES.md', 'VOICEVAULT_PROJECT.md', 'runtime/espeak-ng', 'backend/tests', 'backend/data']) {
    assert.equal(sources.some(source => source === forbidden || source.startsWith(`${forbidden}/`)), false, forbidden)
  }
  assert.ok(sources.includes('frontend/build-resources/uv/uv.exe'))
  assert.ok(sources.includes('scripts/bootstrap_env.py'))
})

test('the audit finds personal strings but tolerates upstream build paths in upstream binaries', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vv-audit-'))
  try {
    fs.mkdirSync(path.join(dir, 'uv'))
    fs.mkdirSync(path.join(dir, 'backend'))
    // Fake personal strings, assembled here so this file itself contains none.
    const usersDir = ['C:', 'Users', ''].join('\\')
    const mail = ['someone', 'gmail.com'].join('@')
    fs.writeFileSync(path.join(dir, 'uv', 'uv.exe'), `${usersDir}builder\\src ${mail}`)
    fs.writeFileSync(path.join(dir, 'backend', 'a.py'), `path = "D:/MyProjects/x"  # ${usersDir}Someone\\ x`)
    fs.writeFileSync(path.join(dir, 'backend', 'b.py'), 'key = "sk-private-value-123"')
    fs.writeFileSync(path.join(dir, 'backend', 'c.py'), 'print("researchable")')
    const hits = audit(dir, auditPatterns({ USERNAME: 'arch' }, ['sk-private-value-123', 'D:/MyProjects']))
    const labels = hits.map(hit => `${hit.path}:${hit.label}`).sort()
    assert.deepEqual(labels, [
      'backend/a.py:Windows user folder', 'backend/a.py:private string #2', 'backend/b.py:private string #1',
    ])
    assert.equal(isUpstreamBinary('uv/uv.exe'), true)
    assert.equal(isUpstreamBinary('backend/a.py'), false)
    // The user name is owner-specific: it is searched in upstream binaries too.
    fs.writeFileSync(path.join(dir, 'uv', 'uv.exe'), 'C:/x/arch/y')
    assert.ok(audit(dir, auditPatterns({ USERNAME: 'arch' })).some(hit => hit.label === 'Windows user name'))
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

test('every backend code folder is packaged', () => {
  const { BACKEND_DIRS } = require('./stage-resources.cjs')
  const fs = require('node:fs')
  const path = require('node:path')
  const backend = path.join(__dirname, '..', '..', 'backend')
  const skip = new Set(['tests', 'data', '__pycache__', '.pytest_cache'])
  const codeDirs = fs.readdirSync(backend, { withFileTypes: true })
    .filter(entry => entry.isDirectory() && !skip.has(entry.name) && !entry.name.startsWith('.'))
    .filter(entry => fs.readdirSync(path.join(backend, entry.name)).some(name => name.endsWith('.py')))
    .map(entry => entry.name)
  const missing = codeDirs.filter(name => !BACKEND_DIRS.includes(name))
  assert.deepEqual(missing, [], `Add these backend folders to BACKEND_DIRS in stage-resources.cjs: ${missing.join(', ')}`)
})
