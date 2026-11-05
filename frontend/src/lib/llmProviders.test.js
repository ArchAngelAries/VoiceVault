import test from 'node:test'
import assert from 'node:assert/strict'
import {
  DEFAULT_LLM_PROVIDER, LLM_PROVIDERS, normalizeLlmProvider, providerDefaultEndpoint,
  isNativeCloudProvider, modelsForDisplay, providerAllowsCatalogContext, providerRequiresApiKey,
} from './llmProviders.js'

test('local chat services have the expected novice defaults', () => {
  assert.deepEqual(LLM_PROVIDERS.slice(0, 3).map(({ id, defaultEndpoint }) => [id, defaultEndpoint]), [
    ['lmstudio', 'http://localhost:1234'],
    ['ollama', 'http://localhost:11434'],
    ['koboldcpp', 'http://localhost:5001'],
  ])
})

test('compatible API preserves the current editable endpoint', () => {
  assert.equal(providerDefaultEndpoint('openai_compatible'), null)
  assert.equal(normalizeLlmProvider('openai_compatible'), 'openai_compatible')
})

test('unknown server provider falls back to LM Studio', () => {
  assert.equal(normalizeLlmProvider('unknown'), DEFAULT_LLM_PROVIDER)
})

test('native cloud providers use their exact destinations while keeping local defaults unchanged', () => {
  assert.deepEqual(LLM_PROVIDERS.slice(0, 3).map(({ id }) => id), ['lmstudio', 'ollama', 'koboldcpp'])
  for (const [provider, endpoint] of [
    ['anthropic', 'https://api.anthropic.com/v1'],
    ['openai', 'https://api.openai.com/v1'],
    ['gemini', 'https://generativelanguage.googleapis.com/v1beta'],
  ]) {
    assert.equal(normalizeLlmProvider(provider), provider)
    assert.equal(providerDefaultEndpoint(provider), endpoint)
    assert.equal(isNativeCloudProvider(provider), true)
    assert.equal(providerRequiresApiKey(provider), true)
  }
  assert.equal(isNativeCloudProvider('openai_compatible'), false)
  assert.equal(providerRequiresApiKey('lmstudio'), false)
})

test('native cloud providers allow custom and Wikidata context, or any identity with name-only enabled', () => {
  for (const provider of ['anthropic', 'openai', 'gemini']) {
    assert.equal(providerAllowsCatalogContext(provider, null), true)
    assert.equal(providerAllowsCatalogContext(provider, 'character:wikidata:Q123'), true)
    for (const identity of ['character:anilist:4', 'character:tmdb:5', 'character:igdb:6', 'character:wikidata:Q0', 'character:wikidata:Q01', 'character:wikidata:Q9\n']) {
      assert.equal(providerAllowsCatalogContext(provider, identity), false, `${provider} ${identity}`)
      assert.equal(providerAllowsCatalogContext(provider, identity, true), true, `${provider} ${identity}`)
    }
  }
  assert.equal(providerAllowsCatalogContext('lmstudio', 'character:anilist:4'), true)
})

test('native cloud model display is limited to the first 100 entries', () => {
  const models = Array.from({ length: 105 }, (_, index) => ({ id: `model-${index}` }))
  assert.equal(modelsForDisplay('anthropic', models).length, 100)
  assert.equal(modelsForDisplay('openai', models)[99].id, 'model-99')
  assert.equal(modelsForDisplay('gemini', models).length, 100)
  assert.equal(modelsForDisplay('lmstudio', models).length, 105)
  assert.deepEqual(modelsForDisplay('openai', null), [])
})
