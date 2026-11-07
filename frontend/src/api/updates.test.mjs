import test from 'node:test'
import assert from 'node:assert/strict'

test('release notes become plain text', async () => {
  // updates.js imports the HTTP client, which needs Vite; test the pure helper from its source.
  const source = (await import('node:fs')).readFileSync(new URL('./updates.js', import.meta.url), 'utf8')
  const body = source.slice(source.indexOf('export function plainNotes'), source.indexOf('/** "1.2 MB"'))
  const plainNotes = new Function(`${body.replace('export ', '')}; return plainNotes`)()
  const md = '## VoiceVault 0.1.1\n### What\'s fixed\n- **Faster chat.** See [CHANGELOG.md](https://x/y)\n- `Get-FileHash` *works*'
  assert.equal(plainNotes(md), "VoiceVault 0.1.1\nWhat's fixed\n• Faster chat. See CHANGELOG.md\n• Get-FileHash works")
})
