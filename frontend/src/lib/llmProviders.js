export const DEFAULT_LLM_PROVIDER = 'lmstudio'

export const LLM_PROVIDERS = [
  { id: 'lmstudio', label: 'LM Studio', defaultEndpoint: 'http://localhost:1234' },
  { id: 'ollama', label: 'Ollama', defaultEndpoint: 'http://localhost:11434' },
  { id: 'koboldcpp', label: 'KoboldCPP', defaultEndpoint: 'http://localhost:5001' },
  { id: 'openai_compatible', label: 'Compatible API', defaultEndpoint: null },
  { id: 'anthropic', label: 'Claude API', defaultEndpoint: 'https://api.anthropic.com/v1' },
  { id: 'openai', label: 'OpenAI API (experimental, untested)', defaultEndpoint: 'https://api.openai.com/v1' },
  { id: 'gemini', label: 'Gemini API (experimental, untested)', defaultEndpoint: 'https://generativelanguage.googleapis.com/v1beta' },
]

const PROVIDER_IDS = new Set(LLM_PROVIDERS.map(provider => provider.id))
const NATIVE_CLOUD_PROVIDERS = new Set(['anthropic', 'openai', 'gemini'])

export function normalizeLlmProvider(value) {
  return PROVIDER_IDS.has(value) ? value : DEFAULT_LLM_PROVIDER
}

export function providerDefaultEndpoint(value) {
  return LLM_PROVIDERS.find(provider => provider.id === normalizeLlmProvider(value))?.defaultEndpoint ?? null
}

export function isNativeCloudProvider(provider) {
  return NATIVE_CLOUD_PROVIDERS.has(normalizeLlmProvider(provider))
}

export function providerRequiresApiKey(provider) {
  return isNativeCloudProvider(provider)
}

export function modelsForDisplay(provider, models) {
  const list = Array.isArray(models) ? models : []
  return isNativeCloudProvider(provider) ? list.slice(0, 100) : list
}

export function providerAllowsCatalogContext(provider, catalogIdentity, nameOnlyContext = false) {
  if (!isNativeCloudProvider(provider) || !catalogIdentity) return true
  if (nameOnlyContext) return true
  return /^character:wikidata:Q[1-9][0-9]*$/.test(catalogIdentity)
    && /^character:wikidata:Q[1-9][0-9]*$/.exec(catalogIdentity)?.[0] === catalogIdentity
}
