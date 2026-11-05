import test from 'node:test'
import assert from 'node:assert/strict'
import { spokenText } from './spokenText.js'

test('spoken text drops standalone stage directions and bracketed instructions', () => {
  assert.equal(spokenText('Hello there.\n*waves*\n[The room goes quiet.]\nHow are you?'), 'Hello there. How are you?')
})

test('spoken text keeps dialogue after a leading action and preserves ordinary prose', () => {
  assert.equal(spokenText('*smiles* I missed you.\nThis (perhaps) is ordinary dialogue.'), 'I missed you. This (perhaps) is ordinary dialogue.')
})

test('spoken text removes inline actions around Unicode dialogue and keeps bold words', () => {
  assert.equal(spokenText('Hello! *smiles* こんにちは。 [whispers] **Really?**'), 'Hello! こんにちは。 Really?')
})

test('manual spoken text shares control XML and multiline JSON suppression', () => {
  assert.equal(
    spokenText('Hello there.\n<delivery>sadly</delivery>\n{\n  "tool": "secret",\n  "args": {"x": 1}\n}\nHow are you?'),
    'Hello there. How are you?',
  )
})

test('manual spoken text suppresses fenced JSON and preserves ordinary inline parentheses', () => {
  assert.equal(
    spokenText('This (perhaps) is spoken.\n```json\n{"command":"secret"}\n```\nContinue here.'),
    'This (perhaps) is spoken. Continue here.',
  )
})
