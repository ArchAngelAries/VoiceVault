/**
 * pages/Settings.jsx — VoiceVault Settings
 * Tabs: General | Model Sources | Media APIs | LLM Backends | TTS Engines | Hardware
 * Settings the app no longer reads stay in the saved file (never deleted) but are not shown.
 * Unsaved edits mark the Save button and the browser warns before leaving.
 */
import React, { useEffect, useState } from 'react'
import { Settings as SettingsIcon, Save, Check } from 'lucide-react'
import { useSettingsStore } from '@/stores/settingsStore'
import { useSystemStore }   from '@/stores/systemStore'
import {
  SettingsSection, SettingsField, SettingsDisclosure,
  TextInput, PasswordInput, NumberInput, SelectInput, ToggleInput, StatusDot
} from '@/components/Settings/SettingsField'
import ModelManager from '@/components/Settings/ModelManager'
import ThemePicker from '@/components/Settings/ThemePicker'
import { applyTheme, savedTheme } from '@/data/themes'
import clsx from 'clsx'

const TABS = ['General', 'Models', 'Model Sources', 'Media APIs', 'LLM Backends', 'TTS Engines', 'Hardware']

export default function Settings() {
  const { settings, loading, saving, error, load, save, update } = useSettingsStore()
  const { hardware, backendOnline } = useSystemStore()
  const [tab,   setTab]   = useState('General')
  const [saved, setSaved] = useState(false)
  // Snapshot of the settings as last loaded or saved; differs from the live settings when there are unsaved edits
  const [baseline, setBaseline] = useState(null)
  const snapshot = () => JSON.stringify(useSettingsStore.getState().settings ?? null)

  const reload = async () => { await load(); setBaseline(snapshot()) }
  useEffect(() => { reload() }, [])
  useEffect(() => { if (settings?.theme && settings.theme !== savedTheme()) applyTheme(settings.theme) }, [settings?.theme])

  const dirty = baseline !== null && !!settings && JSON.stringify(settings) !== baseline
  useEffect(() => {
    if (!dirty) return
    const warn = (event) => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  const handleSave = async () => {
    const ok = await save(settings)
    if (ok) { setBaseline(snapshot()); setSaved(true); setTimeout(() => setSaved(false), 2000) }
  }

  const set = (key) => (val) => update({ [key]: val })

  if (!loading && !settings && error) return (
    <div className="flex-1 flex flex-col items-center justify-center gap-3 p-8">
      <p role="alert" className="text-red-300 text-sm">Could not load settings: {error}</p>
      <button onClick={reload} className="vault-btn-primary">Retry</button>
    </div>
  )

  if (loading || !settings) return (
    <div className="flex-1 flex items-center justify-center">
      <span className="font-mono text-sm text-vault-muted animate-pulse">Loading settings...</span>
    </div>
  )

  return (
    <div className="flex-1 flex flex-col h-full overflow-hidden animate-fade-in">
      {settings._notice && <div role="alert" className="px-8 py-2 text-xs text-amber-300 bg-amber-950/30 border-b border-amber-900/40">{settings._notice}</div>}
      {/* Header */}
      <div className="flex items-center justify-between px-8 py-6 border-b border-vault-border shrink-0">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-vault-elevated border border-vault-border flex items-center justify-center">
            <SettingsIcon size={20} className="text-vault-accent" strokeWidth={1.8} />
          </div>
          <div>
            <h1 className="font-display font-bold text-2xl text-vault-text">Settings</h1>
            <p className="font-body text-sm text-vault-muted">Configure VoiceVault — all settings saved locally</p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          {dirty && !saving && <span role="status" className="font-body text-xs text-amber-200">You have unsaved changes</span>}
          <button onClick={handleSave} disabled={saving} title={dirty ? 'Save your changes' : 'Nothing changed since the last save'}
            className={clsx('vault-btn-primary flex items-center gap-2 text-sm',
              saved && 'bg-emerald-600 hover:bg-emerald-600')}>
            {saved ? <Check size={14} /> : <Save size={14} />}
            {saved ? 'Saved!' : saving ? 'Saving...' : dirty ? 'Save changes •' : 'Save Settings'}
          </button>
        </div>
      </div>

      {/* Tab bar */}
      <div className="flex gap-1 px-8 pt-4 border-b border-vault-border shrink-0">
        {TABS.map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={clsx(
              'px-4 py-2 font-body text-sm rounded-t-lg transition-all duration-200 border-b-2 -mb-px',
              tab === t
                ? 'text-vault-glow border-vault-accent bg-vault-elevated'
                : 'text-vault-muted border-transparent hover:text-vault-text'
            )}>
            {t}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto px-8 py-6">
        {error && <p role="alert" className="text-red-300 text-sm mb-4">Settings could not be saved: {error}</p>}

        {tab === 'Models' && <ModelManager settings={settings} set={set} />}

        {tab === 'General' && (
          <div className="max-w-2xl">
            <SettingsSection title="Appearance" description="Colour theme for the whole app. It changes instantly; press Save Settings to keep it.">
              <ThemePicker value={settings.theme} onChange={set('theme')} />
            </SettingsSection>
            <SettingsSection title="File Locations" description="Where trained models live and where generated audio is saved.">
              <SettingsField label="Models Directory" hint="Root folder for trained voice models">
                <TextInput value={settings.models_dir} onChange={set('models_dir')} placeholder="Optional trained models folder" />
              </SettingsField>
              <SettingsField label="Outputs Directory" hint="Root folder for generated audio files">
                <TextInput value={settings.outputs_dir} onChange={set('outputs_dir')} placeholder="Leave empty to use the app's data folder" />
              </SettingsField>
            </SettingsSection>

            <SettingsSection title="Experimental" description="Features that are not ready for everyone.">
              <SettingsField label="Show experimental features (Train)" hint="Training is unverified on most hardware; trained voices inherit OmniVoice's non-commercial licence.">
                <ToggleInput value={settings.experimental_features} onChange={set('experimental_features')} label="Show Train in the sidebar" />
              </SettingsField>
            </SettingsSection>
          </div>
        )}

        {tab === 'Media APIs' && (
          <div className="max-w-2xl">
            <SettingsSection title="TMDB" description="The Movie Database — movies, TV shows, cartoons. Free API key at themoviedb.org">
              <SettingsField label="API Key">
                <PasswordInput value={settings.tmdb_api_key} onChange={set('tmdb_api_key')} placeholder="Your TMDB v3 API key" />
              </SettingsField>
            </SettingsSection>
            <SettingsSection title="IGDB (Twitch)" description="Internet Game Database — video games and characters. Free via Twitch developer portal.">
              <SettingsField label="Client ID">
                <TextInput value={settings.igdb_client_id} onChange={set('igdb_client_id')} placeholder="Twitch app client ID" />
              </SettingsField>
              <SettingsField label="Client Secret">
                <PasswordInput value={settings.igdb_client_secret} onChange={set('igdb_client_secret')} />
              </SettingsField>
            </SettingsSection>
            <div className="vault-card p-4 mt-2 mb-8">
              <p className="font-mono text-xs text-vault-muted leading-relaxed">
                AniList is free with no API key required — anime browsing works out of the box.
                TMDB and IGDB need credentials.
              </p>
            </div>
          </div>
        )}

        {tab === 'LLM Backends' && (
          <div className="max-w-2xl">
            <SettingsSection title="Default Backend" description="Default service for new chats. You can change the connection in Converse or Scene Creation.">
              <SettingsField label="Default Backend">
                <SelectInput value={settings.llm_default_backend} onChange={set('llm_default_backend')}
                  options={[
                    { value: 'lmstudio',  label: 'LM Studio (local)' },
                    { value: 'ollama',    label: 'Ollama (local)' },
                    ...(settings.llm_default_backend === 'kobold' ? [{ value: 'kobold', label: 'KoboldCPP (saved alias)' }] : []),
                    { value: 'koboldcpp', label: 'KoboldCPP (local)' },
                    { value: 'openai_compatible', label: 'OpenAI-compatible server' },
                    ...(settings.llm_default_backend && !['lmstudio', 'ollama', 'kobold', 'koboldcpp', 'openai_compatible'].includes(settings.llm_default_backend)
                      ? [{ value: settings.llm_default_backend, label: `${settings.llm_default_backend} (saved legacy selection; unavailable)` }]
                      : []),
                  ]} />
              </SettingsField>
            </SettingsSection>
            {settings.llm_default_backend && !['lmstudio', 'ollama', 'kobold', 'koboldcpp', 'openai_compatible'].includes(settings.llm_default_backend) &&
              <p role="status" className="text-amber-200 text-sm mb-4">The saved default “{settings.llm_default_backend}” is not currently supported. Choose a local backend or an OpenAI-compatible server.</p>}
            <SettingsSection title="Local Backends" description="URLs for locally running LLM servers.">
              <SettingsField label="LM Studio URL" hint="Default: http://localhost:1234">
                <TextInput value={settings.lmstudio_url} onChange={set('lmstudio_url')} placeholder="http://localhost:1234" />
              </SettingsField>
              <SettingsField label="Ollama URL" hint="Default: http://localhost:11434">
                <TextInput value={settings.ollama_url} onChange={set('ollama_url')} placeholder="http://localhost:11434" />
              </SettingsField>
              <SettingsField label="KoboldCPP URL" hint="Default: http://localhost:5001">
                <TextInput value={settings.kobold_url} onChange={set('kobold_url')} placeholder="http://localhost:5001" />
              </SettingsField>
            </SettingsSection>
            <SettingsSection title="Cloud API Keys" description="Converse uses these when its own key box is left empty. OpenAI and Gemini are experimental and untested. Keys saved here stay on this PC.">
              <SettingsField label="Anthropic (Claude) API Key" hint="Used by Converse when you choose Claude API.">
                <PasswordInput value={settings.anthropic_api_key} onChange={set('anthropic_api_key')} />
              </SettingsField>
              <SettingsField label="OpenAI API Key" hint="Experimental and untested in Converse.">
                <PasswordInput value={settings.openai_api_key} onChange={set('openai_api_key')} />
              </SettingsField>
              <SettingsField label="Google Gemini API Key" hint="Experimental and untested in Converse.">
                <PasswordInput value={settings.gemini_api_key} onChange={set('gemini_api_key')} />
              </SettingsField>
            </SettingsSection>
          </div>
        )}

        {tab === 'Model Sources' && (
          <div className="max-w-2xl">
            <SettingsSection title="Model Sources" description="Use models where they are stored. Hugging Face cache settings are detected automatically. Save these settings, then use Rescan on Generate.">
              <SettingsField label="Shared Model Folder" hint="Optional existing shared library, managed by any tool or by you">
                <TextInput value={settings.shared_models_dir || ''} onChange={set('shared_models_dir')} placeholder="Optional shared models folder" />
              </SettingsField>
              {[
                ['model_search_roots', 'Additional Model Folders', 'Folders containing model files'],
                ['comfyui_dirs', 'ComfyUI Installations', 'ComfyUI folders; their models and extra model paths are read'],
                ['forge_dirs', 'Forge Installations', 'Forge folders; their models folders are read'],
              ].map(([key, label, hint]) => (
                <SettingsField key={key} label={label} hint={`${hint}. Enter one path per line.`}>
                  <textarea aria-label={label} rows={3}
                    value={Array.isArray(settings[key]) ? settings[key].join('\n') : ''}
                    onChange={e => set(key)(e.target.value.split(/\r?\n/))}
                    placeholder="One folder path per line"
                    className="w-full bg-vault-elevated border border-vault-border rounded-lg px-3 py-2 font-mono text-sm text-vault-text focus:outline-none focus:border-vault-accent" />
                </SettingsField>
              ))}
              <SettingsField label="VibeVoice Tokenizer Folder" hint="Optional override if the tokenizer cannot be found beside the model or in the cache">
                <TextInput value={settings.vibevoice_tokenizer_dir || ''} onChange={set('vibevoice_tokenizer_dir')} placeholder="Optional tokenizer folder" />
              </SettingsField>
            </SettingsSection>
            <p className="font-body text-sm text-vault-muted">Unusual installation locations can be added here. VoiceVault reads these sources without copying weights or changing the other applications.</p>
          </div>
        )}

        {tab === 'TTS Engines' && (
          <div className="max-w-2xl">
            <SettingsSection title="Fish Audio Drama 3 (local)" description="Runs Drama 3 GGUF files through the bundled audio.cpp runtime. Built with Fish Audio. Save, then Rescan on Generate.">
              <SettingsField label="audio.cpp folder" hint="Folder with audiocpp_server.exe. Leave empty to use runtime/audiocpp in the project.">
                <TextInput value={settings.audiocpp_dir || ''} onChange={set('audiocpp_dir')} placeholder="Optional" />
              </SettingsField>
              <SettingsField label="Backend" hint="Auto uses Vulkan (GPU) when the build has it, otherwise the CPU.">
                <SelectInput value={settings.drama3_backend || 'auto'} onChange={set('drama3_backend')}
                  options={[{ value: 'auto', label: 'Auto' }, { value: 'vulkan', label: 'Vulkan (GPU)' }, { value: 'cpu', label: 'CPU only (slow)' }]} />
              </SettingsField>
            </SettingsSection>
            <SettingsDisclosure title="Advanced: Drama 3 tuning" description="The defaults suit most PCs. Change these only if Drama 3 runs out of memory or feels too slow.">
              <SettingsField label="Expert layers in system RAM" hint="-1 = automatic (fits the free GPU memory when the model loads). 0 to 40 sets it by hand: more layers use less GPU memory but run slower.">
                <NumberInput value={settings.drama3_cpu_expert_layers ?? -1} onChange={set('drama3_cpu_expert_layers')} min={-1} max={40} />
              </SettingsField>
              <SettingsField label="Context length" hint="Text, reference audio and output share this budget. Larger uses more memory.">
                <NumberInput value={settings.drama3_context_length ?? 4096} onChange={set('drama3_context_length')} min={1024} max={8192} step={256} />
              </SettingsField>
              <SettingsField label="CPU threads" hint="0 uses half of the logical processors.">
                <NumberInput value={settings.drama3_threads ?? 0} onChange={set('drama3_threads')} min={0} max={64} />
              </SettingsField>
            </SettingsDisclosure>
            <SettingsSection title="Fish Audio Drama 3 (cloud)" description="Optional paid cloud service. Text and reference clips are sent to Fish Audio and billed per character.">
              <SettingsField label="Fish Audio API key">
                <PasswordInput value={settings.fish_audio_api_key || ''} onChange={set('fish_audio_api_key')} placeholder="Leave empty to keep the cloud model off" />
              </SettingsField>
            </SettingsSection>
          </div>
        )}

        {tab === 'Hardware' && (
          <div className="max-w-2xl">
            <SettingsSection title="Detected Hardware" description="Hardware detected by the VoiceVault backend. Read-only.">
              <div className="vault-card p-5 flex flex-col gap-4">
                <div className="flex items-center justify-between">
                  <span className="vault-label">Backend Status</span>
                  <StatusDot ok={backendOnline} label={backendOnline ? 'Online' : 'Offline'} />
                </div>
                {hardware && <>
                  <div className="flex items-center justify-between">
                    <span className="vault-label">Acceleration</span>
                    <span className="font-mono text-sm text-vault-glow uppercase font-semibold">{hardware.acceleration}</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="vault-label">PyTorch Version</span>
                    <span className="font-mono text-sm text-vault-text">{hardware.torch_version}</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="vault-label">Recommended Device</span>
                    <span className="font-mono text-sm text-vault-text">{hardware.recommended_device}</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="vault-label">ffmpeg Available</span>
                    <StatusDot ok={hardware.ffmpeg_available} label={hardware.ffmpeg_available ? 'Yes' : 'Not found in PATH'} />
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="vault-label">HW Video Encoder</span>
                    <span className="font-mono text-sm text-vault-text">{hardware.ffmpeg_hw_encoder}</span>
                  </div>
                  {hardware.gpus?.length > 0 && (
                    <div className="border-t border-vault-border pt-4 flex flex-col gap-3">
                      <span className="vault-label">GPUs</span>
                      {hardware.gpus.map((gpu) => (
                        <div key={gpu.index} className="vault-card-elevated p-3 flex flex-col gap-2">
                          <div className="flex items-center justify-between">
                            <span className="font-body text-sm text-vault-text font-medium">{gpu.name}</span>
                            <span className="vault-label">GPU {gpu.index}</span>
                          </div>
                          <div className="flex items-center gap-3">
                            <div className="flex-1 h-1.5 bg-vault-border rounded-full overflow-hidden">
                              <div className="h-full rounded-full bg-accent-gradient transition-all"
                                style={{ width: `${Math.round(((gpu.vram_total_mb - gpu.vram_free_mb) / gpu.vram_total_mb) * 100)}%` }} />
                            </div>
                            <span className="font-mono text-xs text-vault-muted whitespace-nowrap">
                              {Math.round(gpu.vram_total_mb - gpu.vram_free_mb)} / {Math.round(gpu.vram_total_mb)} MB
                            </span>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </>}
              </div>
            </SettingsSection>
          </div>
        )}

      </div>
    </div>
  )
}
