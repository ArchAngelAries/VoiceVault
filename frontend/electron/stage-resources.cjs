/**
 * Builds the clean `voicevault/` resource tree the Windows installer ships, then audits it.
 *
 * Allowlist only: backend code, the bundled audio.cpp runtime, setup scripts, public docs,
 * licences and uv. Personal state (.gitignore / docs/RELEASE_CHECKLIST.md) never matches.
 * Run: node electron/stage-resources.cjs   (from the frontend folder)
 * Extra private strings to search for (API key values, e-mail) can be listed one per line in
 * the file named by VOICEVAULT_AUDIT_STRINGS_FILE; they are never printed.
 */
const fs = require('node:fs')
const path = require('node:path')

const REPO = path.resolve(__dirname, '../..')
const STAGE = path.resolve(__dirname, '../release-staging')

const BACKEND_DIRS = ['api', 'asr', 'core', 'db', 'engines', 'gpu', 'harvest', 'llm', 'models', 'music', 'rag', 'tools', 'training', 'utils', 'voice']
const BACKEND_FILES = ['main.py', 'mcp_server.py', 'requirements.txt', 'requirements-tts.txt', 'requirements-lock.txt', 'voicevault_settings.example.json']
const COPIES = [
  ...BACKEND_DIRS.map(name => ({ from: `backend/${name}`, filter: 'backend' })),
  ...BACKEND_FILES.map(name => ({ from: `backend/${name}` })),
  { from: 'runtime/audiocpp/v0.9.3-vulkan-portable', filter: 'runtime' },
  { from: 'runtime/audiocpp/config-examples', filter: 'runtime' },
  ...['install_torch.py', 'bootstrap_env.py', 'setup_project.py', 'setup_training_runtime.py', 'training_requirements.lock']
    .map(name => ({ from: `scripts/${name}` })),
  ...['LICENSE', 'TRADEMARK.md', 'README.md', 'THIRD_PARTY_NOTICES.md', 'CREDITS.md'].map(name => ({ from: name })),
  ...['API_KEYS.md', 'MODEL_LICENSES.md', 'TERMS_OF_USE.md', 'AGENT_MANUAL.md', 'CHAT_MEMORY.md', 'assets/logo-512.png']
    .map(name => ({ from: `docs/${name}` })),
  ...['uv.exe', 'LICENSE-MIT.txt', 'LICENSE-APACHE.txt', 'uv-source.json']
    .map(name => ({ from: `frontend/build-resources/uv/${name}`, to: `uv/${name}` })),
]

/** True when a path (relative, forward slashes) must never ship. Applies to every copied file. */
function isExcluded(relative) {
  const parts = relative.split('/')
  const name = parts.at(-1)
  if (parts.some(part => part.startsWith('.') || part === '__pycache__' || part === 'node_modules' || part === 'venv')) return true
  if (/\.(pyc|pyo|db|sqlite3?|log|env|tmp)$/i.test(name) || /\.db-|\.corrupt-/i.test(name)) return true
  if (/settings.*\.json$/i.test(name) && name !== 'voicevault_settings.example.json') return true
  return false
}

function isExcludedBackendCode(relative) {
  const parts = relative.split('/')
  const name = parts.at(-1)
  if (parts.includes('tests') || parts.includes('data')) return true
  return /^test_.*\.py$|_test\.py$|^conftest\.py$/.test(name)
}

function copyTree(source, target, relativeRoot, filter, copied) {
  const stat = fs.statSync(source)
  if (stat.isDirectory()) {
    for (const entry of fs.readdirSync(source)) {
      copyTree(path.join(source, entry), path.join(target, entry), `${relativeRoot}/${entry}`, filter, copied)
    }
    return
  }
  if (isExcluded(relativeRoot)) return
  if (filter === 'backend' && isExcludedBackendCode(relativeRoot)) return
  fs.mkdirSync(path.dirname(target), { recursive: true })
  fs.copyFileSync(source, target)
  copied.push({ path: relativeRoot, bytes: stat.size })
}

function stage({ repo = REPO, stageDir = STAGE } = {}) {
  const output = path.join(stageDir, 'voicevault')
  fs.rmSync(stageDir, { recursive: true, force: true })
  fs.mkdirSync(output, { recursive: true })
  const copied = []
  for (const item of COPIES) {
    const source = path.join(repo, ...item.from.split('/'))
    if (!fs.existsSync(source)) throw new Error(`Missing release input: ${item.from}${item.from.startsWith('frontend/build-resources/uv') ? ' (run scripts/fetch_uv.py)' : ''}`)
    const to = item.to || item.from
    copyTree(source, path.join(output, ...to.split('/')), to, item.filter, copied)
  }
  return { output, copied }
}

/**
 * Personal-data patterns from docs/RELEASE_CHECKLIST.md. Each is [label, RegExp, generic].
 * Generic patterns (any user folder, any e-mail) are skipped for unmodified upstream binaries, whose
 * build machines and authors legitimately appear in them; owner-specific patterns apply to every file.
 */
function isUpstreamBinary(relative) {
  return /^(uv\/uv\.exe|runtime\/audiocpp\/[^/]+\/[^/]+\.(exe|dll))$/i.test(relative)
}

function auditPatterns(env = process.env, extraStrings = []) {
  const patterns = [
    ['Windows user folder', /[A-Z]:[\\/]+Users[\\/]+(?!runner|Public|Default)[A-Za-z0-9._-]+/i, true],
    ['Stability Matrix data path', /Stability ?Matrix[\\/]+Data/i, false],
    ['e-mail address', /[A-Za-z0-9._%+-]+@(gmail|outlook|hotmail|yahoo|icloud|proton)\.[a-z]+/i, true],
  ]
  const user = env.USERNAME?.trim()
  if (user && user.length >= 3) {
    patterns.push(['Windows user name', new RegExp(`(?<![A-Za-z0-9])${user.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?![A-Za-z0-9])`, 'i'), false])
  }
  extraStrings.filter(value => value && value.length >= 6).forEach((value, index) => {
    patterns.push([`private string #${index + 1}`, new RegExp(value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')), false])
  })
  return patterns
}

/** Searches every staged file (text and binary, as latin1) and returns [{ path, label }]. */
function audit(output, patterns) {
  const hits = []
  const walk = (dir, relative) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name)
      const rel = relative ? `${relative}/${entry.name}` : entry.name
      if (entry.isDirectory()) { walk(full, rel); continue }
      if (isExcluded(rel)) hits.push({ path: rel, label: 'excluded file type' })
      const text = fs.readFileSync(full).toString('latin1')
      const upstream = isUpstreamBinary(rel)
      for (const [label, pattern, generic] of patterns) {
        if (generic && upstream) continue
        if (pattern.test(text)) hits.push({ path: rel, label })
      }
    }
  }
  walk(output, '')
  return hits
}

function readExtraStrings(env = process.env) {
  // Your own folder names and other private strings, one per line. The default file is gitignored.
  const local = path.join(__dirname, '..', '..', 'scripts', 'audit-strings.local.txt')
  const file = env.VOICEVAULT_AUDIT_STRINGS_FILE?.trim() || (fs.existsSync(local) ? local : '')
  if (!file) return []
  return fs.readFileSync(file, 'utf8').split(/\r?\n/).map(line => line.trim()).filter(Boolean)
}

function main() {
  const { output, copied } = stage()
  const hits = audit(output, auditPatterns(process.env, readExtraStrings()))
  const bytes = copied.reduce((sum, item) => sum + item.bytes, 0)
  const report = {
    created: new Date().toISOString(),
    files: copied.length,
    bytes,
    personalDataHits: hits,
  }
  fs.writeFileSync(path.join(STAGE, 'audit.json'), `${JSON.stringify(report, null, 2)}\n`)
  console.log(`Staged ${copied.length} files (${(bytes / 1048576).toFixed(1)} MiB) in ${output}`)
  if (hits.length) {
    for (const hit of hits) console.error(`AUDIT: ${hit.label} in ${hit.path}`)
    console.error('Personal-data audit failed; nothing was packaged.')
    process.exitCode = 1
  } else {
    console.log('Personal-data audit: no hits.')
  }
}

if (require.main === module) main()

module.exports = { COPIES, isExcluded, isExcludedBackendCode, isUpstreamBinary, stage, audit, auditPatterns }
