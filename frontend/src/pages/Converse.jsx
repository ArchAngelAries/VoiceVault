import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { AlertCircle, Bot, Brain, Check, Loader2, MessageCircle, Pencil, Plus, RefreshCw, Save, Search, Send, Square, Trash2, Volume2, VolumeX, X } from 'lucide-react'
import { audioUrl, getEngines, getModels, getReferences, synthesize, warmModel } from '@/api/tts'
import { createSession, deleteSession, getChatConnection, getEarlierMessages, getSession, linkMessageAudio, listChatModels, listSessions, searchSessions, streamMessage, streamScene, updateSession } from '@/api/converse'
import ReplayButton from '@/components/converse/ReplayButton'
import MemoryPanel from '@/components/converse/MemoryPanel'
import SceneControls from '@/components/converse/SceneControls'
import { normalizeScene } from '@/components/converse/sceneSettings'
import ChatCharacterPicker from '@/components/converse/ChatCharacterPicker'
import CharacterAvatar from '@/components/converse/CharacterAvatar'
import CharacterPortraitEditor from '@/components/converse/CharacterPortraitEditor'
import { castPayload, createCastMember, getCastMember, listCast, listPersonas, personaSnapshot, updateCastMember } from '@/api/cast'
import ProfileSuggestions from '@/components/converse/ProfileSuggestions.jsx'
import { resolveSuggestionVoice } from '@/components/converse/profileSuggestions.js'
import { spokenText } from '@/components/converse/spokenText'
import ChatAudioInput from '@/components/converse/ChatAudioInput'
import { applyTranscriptToDraft } from '@/components/converse/audioInput'
import { streamTts } from '@/api/streamedTts'
import { BACKEND_URL } from '@/api/client'
import { StreamedSpeechPlayer } from '@/lib/streamedSpeech'
import DeliveryControls from '@/components/tts/DeliveryControls'
import SavedVoicePresets from '@/components/tts/SavedVoicePresets'
import { DEFAULT_GENERATION, presetVoiceSettings } from '@/lib/savedVoicePreset'
import { createSpeechQueue } from '@/components/converse/speechQueue'
import { PACE_GAP_SECONDS, speechSnapshot } from '@/components/converse/speechRouting'
import { DEFAULT_LLM_PROVIDER, LLM_PROVIDERS, isNativeCloudProvider, modelsForDisplay, normalizeLlmProvider, providerDefaultEndpoint, providerRequiresApiKey } from '@/lib/llmProviders'
import {
  buildRosterPayload, characterForMessage, EMPTY_CHARACTER_PROFILE, MAX_CHAT_CHARACTERS,
  messageSpeakerLabel, newCharacterId, normalizeCharacters, removeSecondaryCharacter,
  updateCharacterProfile,
} from '@/components/converse/characterRoster'

const DEFAULT_ENDPOINT = 'http://localhost:1234'
const EMPTY_PROFILE = { ...EMPTY_CHARACTER_PROFILE, image_url: '' }
const ERROR_TEXT = (error) => error?.response?.data?.detail || error?.message || 'Something went wrong.'
const USABLE_TTS = new Set(['integrated', 'tested'])

export default function Converse({ sceneCreation = false }) {
  const location = useLocation()
  const [sessions, setSessions] = useState([])
  const [memoryOpen, setMemoryOpen] = useState(false)
  const [autoConnect, setAutoConnect] = useState(false)
  const navigate = useNavigate()
  const [loadingEarlier, setLoadingEarlier] = useState(false)
  const [memoryInfo, setMemoryInfo] = useState(null)
  const [chatQuery, setChatQuery] = useState('')
  const [chatHits, setChatHits] = useState(null)        // search results, or null when not searching
  const [renaming, setRenaming] = useState(null)        // { id, text }
  const [confirmDelete, setConfirmDelete] = useState(null)
  const [contextTokens, setContextTokens] = useState(() => {
    try { return Number(localStorage.getItem('vv.chatContextTokens')) || 8192 } catch { return 8192 }
  })
  const [librarySessions, setLibrarySessions] = useState([])
  const [session, setSession] = useState(null)
  const roleplay = !sceneCreation && !!session?.roleplay
  const [profile, setProfile] = useState(EMPTY_PROFILE)
  const [characters, setCharacters] = useState([{ id: 'primary', profile: EMPTY_PROFILE }])
  const [activeSpeakerId, setActiveSpeakerId] = useState('primary')
  const [profileDirty, setProfileDirty] = useState(false)
  const [scene, setScene] = useState({ context: '', participant_ids: [], rounds: 1 })
  const [sceneProgress, setSceneProgress] = useState(null)
  const [title, setTitle] = useState('')
  const [draft, setDraft] = useState('')
  const [provider, setProvider] = useState(DEFAULT_LLM_PROVIDER)
  const [nameOnlyCloudContext, setNameOnlyCloudContext] = useState(false)
  const [endpoint, setEndpoint] = useState('http://localhost:1234')
  const [apiKey, setApiKey] = useState('')
  const [models, setModels] = useState([])
  const [model, setModel] = useState('')
  const [connected, setConnected] = useState(false)
  const [ttsEngines, setTtsEngines] = useState([])
  const [ttsModels, setTtsModels] = useState([])
  const [references, setReferences] = useState([])
  const [ttsEngineId, setTtsEngineId] = useState('')
  const [ttsModelId, setTtsModelId] = useState('')
  const [referenceId, setReferenceId] = useState('')
  const [speechMode, setSpeechMode] = useState('description')
  const [hasSavedSpeechSettings, setHasSavedSpeechSettings] = useState(false)
  const [messages, setMessages] = useState([])
  const [loading, setLoading] = useState(true)
  const [connecting, setConnecting] = useState(false)
  const [sending, setSending] = useState(false)
  const [saving, setSaving] = useState(false)
  const [creating, setCreating] = useState(false)
  const [speaking, setSpeaking] = useState(false)
  const [capturing, setCapturing] = useState(false)
  const [audioPlaying, setAudioPlaying] = useState(false)
  const [previewPlaying, setPreviewPlaying] = useState(false)
  const [portraitUploading, setPortraitUploading] = useState(false)
  const [audioResult, setAudioResult] = useState(null)
  const [delivery, setDelivery] = useState({ emotion: 'neutral', style: 'natural', expression: null })
  const [autoSpeak, setAutoSpeak] = useState(!sceneCreation)
  const [proposeDelivery, setProposeDelivery] = useState(false)
  const [speechSegments, setSpeechSegments] = useState([])
  const [speechTiming, setSpeechTiming] = useState(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [personas, setPersonas] = useState([])
  const [defaultPersonaId, setDefaultPersonaId] = useState(null)
  const [castMembers, setCastMembers] = useState([])
  const [castBusy, setCastBusy] = useState(false)
  const [personasLoaded, setPersonasLoaded] = useState(false)
  const castParamHandledRef = useRef(false)
  const controllerRef = useRef(null)
  const audioRef = useRef(null)
  const segmentAudioRefs = useRef(new Map())
  const speechControllerRef = useRef(null)
  const speechPlayerRef = useRef(null)
  const speechQueueRef = useRef(null)
  const requestIdentityRef = useRef(0)
  const activeSessionIdRef = useRef(null)
  const sessionOffsetRef = useRef(0)   // messages before the first one shown (long chats load in pages)
  const activeSpeakerIdRef = useRef('primary')
  const aliveRef = useRef(true)
  const lifecycleRef = useRef(0)
  const connectIdentityRef = useRef(0)
  const profileEditVersionRef = useRef(0)
  const connectionEditVersionRef = useRef(0)
  const handledRouteKeyRef = useRef(null)
  const speechCatalogRef = useRef({ ready: false, engines: [], models: [], references: [] })
  const pendingSpeechSettingsRef = useRef(null)
  const messagesViewportRef = useRef(null)
  const followLatestRef = useRef(true)
  const visibleSessionRef = useRef(null)

  useEffect(() => {
    const viewport = messagesViewportRef.current
    if (!viewport) return
    if (visibleSessionRef.current !== session?.id) {
      visibleSessionRef.current = session?.id
      followLatestRef.current = true
    }
    if (followLatestRef.current) viewport.scrollTop = viewport.scrollHeight
  }, [messages, session?.id])

  const engine = ttsEngines.find(item => item.id === ttsEngineId)
  const capabilities = engine?.capabilities || {}
  const engineModels = ttsModels.filter(item => item.engine_id === ttsEngineId && USABLE_TTS.has(item.state))
  const canDescribe = !!capabilities.voice_description
  const canReference = !!capabilities.reference_audio
  const selectedCharacter = characters.find(item => item.id === activeSpeakerId) || characters[0]
  const persistedCharacters = normalizeCharacters(session)
  const currentAssistant = [...messages].reverse().find(item => item.role === 'assistant' && item.content?.trim())
  // The voice a reply is spoken in: the voice it was written with (its snapshot), else its character's.
  const voiceForReply = reply => {
    if (!reply) return { character: null, model: null, capabilities: {}, settings: null, mode: 'description', referenceId: '', problem: '' }
    const rosterCharacter = characterForMessage(reply, persistedCharacters)
    const character = rosterCharacter && reply.profile_snapshot && (sceneCreation || reply.profile_snapshot.voice_settings?.model_id)
      ? { id: rosterCharacter.id, profile: reply.profile_snapshot } : rosterCharacter
    const settings = character?.profile?.voice_settings || null
    const modelId = settings?.model_id || ''
    const model = ttsModels.find(item => item.id === modelId && USABLE_TTS.has(item.state))
    const capabilities = ttsEngines.find(item => item.id === model?.engine_id)?.capabilities || {}
    const mode = settings?.speech_mode === 'reference' ? 'reference' : 'description'
    const referenceId = settings?.reference_id || ''
    const name = character?.profile?.name || 'this character'
    let problem = ''
    if (reply.speaker_id && !characters.some(item => item.id === reply.speaker_id)) {
      problem = `${messageSpeakerLabel(reply, persistedCharacters)} was removed from this roster. Their saved reply remains available as text.`
    } else if (reply.speaker_id && !character) {
      problem = `${messageSpeakerLabel(reply, persistedCharacters)} was removed. Their saved reply remains available as text.`
    } else if (!modelId) {
      problem = sceneCreation ? `${character?.profile?.name || 'This character'} has no saved voice model. Choose and save one in that character's profile.` : 'Choose a chat voice below to speak this reply.'
    } else if (!model) {
      problem = `The saved voice model “${modelId}” is unavailable. Choose and save a replacement${sceneCreation ? ` for ${name}` : ''}.`
    } else if (settings.engine_id && settings.engine_id !== model.engine_id) {
      problem = 'The saved engine does not match this character’s exact model. Choose and save a replacement explicitly.'
    } else if (mode === 'reference' && !capabilities.reference_audio) {
      problem = 'The saved reference voice mode is unavailable for this character’s voice model.'
    } else if (mode === 'description' && !capabilities.voice_description && capabilities.reference_audio) {
      problem = 'The saved description voice mode is unavailable for this character’s voice model.'
    } else if (mode === 'reference' && (!referenceId || !references.some(item => item.reference_id === referenceId))) {
      problem = `The saved reference clip for ${name} is unavailable. Choose and save another clip.`
    }
    return { character, model, capabilities, settings, mode, referenceId, problem }
  }
  const latestVoice = voiceForReply(currentAssistant)
  const replyCharacter = latestVoice.character
  const replyModel = latestVoice.model
  const replySpeechProblem = latestVoice.problem
  sessionOffsetRef.current = session?.messages_offset || 0

  const applySpeechSettings = (settings, catalog = speechCatalogRef.current) => {
    setDelivery({ emotion: 'neutral', style: 'natural', expression: null, ...settings?.delivery })
    if (settings) {
      const modelId = typeof settings.model_id === 'string' ? settings.model_id : ''
      setHasSavedSpeechSettings(!!modelId)
      setSpeechMode(settings.speech_mode === 'reference' ? 'reference' : 'description')
      setReferenceId(typeof settings.reference_id === 'string' ? settings.reference_id : '')
      setTtsModelId(modelId)
      const modelEntry = catalog.models.find(item => item.id === modelId && USABLE_TTS.has(item.state))
      setTtsEngineId(settings.engine_id || modelEntry?.engine_id || '')
      return
    }
    setHasSavedSpeechSettings(false)
    setReferenceId('')
    setSpeechMode('description')
    // No saved voice: use the fast live voice (Kokoro) when it is installed, so spoken replies start in
    // about half a second. Characters with a saved voice (for example a cloned Drama 3 voice) keep it.
    const fast = catalog.models.find(item => item.engine_id === 'kokoro' && USABLE_TTS.has(item.state))
    setTtsEngineId(fast ? 'kokoro' : '')
    setTtsModelId(fast ? fast.id : '')
  }

  // Load the chosen voice as soon as a chat with spoken replies is open, not on the first reply.
  const warmedRef = useRef('')
  useEffect(() => {
    if (!autoSpeak || !ttsModelId || warmedRef.current === ttsModelId) return
    warmedRef.current = ttsModelId
    warmModel(ttsModelId).catch(() => { warmedRef.current = '' })
  }, [autoSpeak, ttsModelId])

  const saveableProfile = (source = profile) => ({
    ...source,
    voice_settings: {
      ...source.voice_settings,
      engine_id: ttsEngineId || null,
      model_id: ttsModelId || null,
      reference_id: referenceId || null,
      speech_mode: speechMode,
      delivery,
    },
  })

  const saveableRoster = () => ({ ...buildRosterPayload(
    updateCharacterProfile(characters, activeSpeakerId, saveableProfile()),
    title.trim() || 'Conversation',
  ), scene: normalizeScene(scene, characters), ...(!sceneCreation ? { spoken_replies: autoSpeak } : {}) })

  const updateScene = next => {
    setScene(next)
    setSceneProgress(null)
    profileEditVersionRef.current += 1
    setProfileDirty(true)
  }

  const setActiveProfile = nextProfile => {
    setProfile(nextProfile)
    setCharacters(current => updateCharacterProfile(current, activeSpeakerId, nextProfile))
    profileEditVersionRef.current += 1
    setProfileDirty(true)
  }

  const updateSavedVoiceSettings = (settings) => {
    const nextProfile = { ...profile, voice_settings: settings }
    setActiveProfile(nextProfile)
    setHasSavedSpeechSettings(!!settings?.model_id)
  }

  const stopAudio = useCallback(() => {
    setAudioPlaying(false)
    speechQueueRef.current?.stop()
    speechQueueRef.current = null
    speechControllerRef.current?.abort()
    speechControllerRef.current = null
    speechPlayerRef.current?.stop()
    speechPlayerRef.current = null
    if (audioRef.current) {
      audioRef.current.pause()
      audioRef.current.currentTime = 0
    }
    for (const audio of segmentAudioRefs.current.values()) { audio.pause(); audio.currentTime = 0 }
  }, [])

  const handleCaptureChange = useCallback(active => {
    setCapturing(active)
    if (active) {
      requestIdentityRef.current += 1
      stopAudio()
      setSpeaking(false)
    }
  }, [stopAudio])

  const stopGeneration = useCallback(() => {
    const wasStreaming = !!speechControllerRef.current
    const stoppedSessionId = activeSessionIdRef.current
    requestIdentityRef.current += 1
    const stoppedIdentity = requestIdentityRef.current
    controllerRef.current?.abort()
    controllerRef.current = null
    stopAudio()
    setSending(false)
    setSceneProgress(current => current?.status === 'running' ? { ...current, status: 'stopped' } : current)
    setSpeaking(false)
    setNotice(wasStreaming ? 'Speech stopped and pending sentences were cleared. Complete-output models finish the active sentence before releasing; incremental models stop at the next decode step.' : 'Reply stopped. Any active complete speech generation may finish on the backend.')
    if (stoppedSessionId) {
      setTimeout(async () => {
        try {
          const refreshed = await getSession(stoppedSessionId)
          if (aliveRef.current && requestIdentityRef.current === stoppedIdentity && activeSessionIdRef.current === stoppedSessionId) {
            setSession(refreshed)
            setMessages(refreshed.messages || [])
            setSessions(current => [refreshed, ...current.filter(item => item.id !== refreshed.id)])
          }
        } catch { /* The persisted partial remains visible until the next selection retry. */ }
      }, 400)
    }
  }, [stopAudio])

  useEffect(() => {
    aliveRef.current = true
    const lifecycle = ++lifecycleRef.current
    const connectionVersion = connectionEditVersionRef.current
    const endpointRequest = getChatConnection().catch(() => null)
    const init = async () => {
      try {
        const sessionData = await listSessions()
        if (!aliveRef.current || lifecycle !== lifecycleRef.current) return
        setLibrarySessions(sessionData.sessions || [])
        const items = (sessionData.sessions || []).filter(item => sceneCreation ? item.kind !== 'chat' : item.kind === 'chat')
        setSessions(items)
        if (items[0] && !location.state?.characterProfile) await selectSession(items[0].id)
        else if (!sceneCreation && !location.state?.characterProfile) {
          const fresh = await createSession({ kind: 'chat', title: 'New chat', spoken_replies: true })
          if (!aliveRef.current || lifecycle !== lifecycleRef.current) return
          setSessions([fresh])
          await selectSession(fresh.id)
        }
      } catch (e) { if (aliveRef.current && lifecycle === lifecycleRef.current) setError(`Could not load conversations: ${ERROR_TEXT(e)}`) }
      finally { if (aliveRef.current && lifecycle === lifecycleRef.current) setLoading(false) }

      // Speech is optional; its service being unavailable must not block typed chat.
      try {
        const [enginesData, modelsData, refs] = await Promise.all([getEngines(), getModels(), getReferences()])
        if (!aliveRef.current || lifecycle !== lifecycleRef.current) return
        setTtsEngines(enginesData.engines || [])
        setTtsModels(modelsData.models || [])
        setReferences(refs || [])
        const catalog = { ready: true, engines: enginesData.engines || [], models: modelsData.models || [], references: refs || [] }
        speechCatalogRef.current = catalog
        const pending = pendingSpeechSettingsRef.current
        if (pending && pending.sessionId === activeSessionIdRef.current && pending.speakerId === activeSpeakerIdRef.current) {
          applySpeechSettings(pending.settings, catalog)
          pendingSpeechSettingsRef.current = null
        } else if (!activeSessionIdRef.current) applySpeechSettings(null, catalog)
      } catch { if (aliveRef.current && lifecycle === lifecycleRef.current) setNotice('Typed chat is available. Speech options could not be loaded from the voice backend.') }

      const result = await endpointRequest
      if (aliveRef.current && lifecycle === lifecycleRef.current && connectionVersion === connectionEditVersionRef.current) {
        if (result?.provider) setProvider(normalizeLlmProvider(result.provider))
        const configuredEndpoint = result?.default_endpoint || result?.base_url
        if (typeof configuredEndpoint === 'string' && configuredEndpoint) setEndpoint(configuredEndpoint)
        setAutoConnect(true)
      }
    }
    init()
    return () => {
      aliveRef.current = false
      lifecycleRef.current += 1
      connectIdentityRef.current += 1
      requestIdentityRef.current += 1
      controllerRef.current?.abort()
      speechControllerRef.current?.abort()
      speechQueueRef.current?.stop()
      speechPlayerRef.current?.stop()
      if (audioRef.current) { audioRef.current.pause(); audioRef.current.removeAttribute('src') }
    }
  }, [])

  useEffect(() => {
    const incoming = location.state?.characterProfile
    if (!incoming || handledRouteKeyRef.current === location.key) return
    handledRouteKeyRef.current = location.key
    if (!sceneCreation) {
      assignChatCharacter({ profile: incoming, character_id: location.state?.characterId || null })
      return
    }
    stopGeneration()
    const normalized = Object.fromEntries(Object.keys(EMPTY_PROFILE).map(key => [key, typeof incoming[key] === 'string' ? incoming[key] : '']))
    const proposedTitle = typeof incoming.name === 'string' && incoming.name.trim() ? `${incoming.name.trim()} chat` : 'Character conversation'
    requestIdentityRef.current += 1
    setError('')
    setProfile(normalized)
    const incomingRoster = [{ id: 'primary', profile: normalized }]
    setCharacters(incomingRoster)
    setActiveSpeakerId('primary')
    activeSpeakerIdRef.current = 'primary'
    applySpeechSettings(null)
    profileEditVersionRef.current += 1
    setTitle(proposedTitle)
    setMessages([])
    setSession(null)
    setScene({ context: '', participant_ids: [], rounds: 1 })
    setSceneProgress(null)
    setProfileDirty(true)
    activeSessionIdRef.current = null
    setNotice('Character details were imported for review. Create this conversation when the profile is ready.')
  }, [location.key, location.state, stopGeneration])

  useEffect(() => {
    const q = chatQuery.trim()
    if (!q) { setChatHits(null); return }
    const timer = setTimeout(async () => {
      try {
        const data = await searchSessions(q)
        setChatHits((data.sessions || []).filter(item => sceneCreation ? item.kind !== 'chat' : item.kind === 'chat'))
      } catch { setChatHits([]) }
    }, 250)
    return () => clearTimeout(timer)
  }, [chatQuery, sceneCreation])

  const renameChat = async (id, text) => {
    const name = text.trim()
    setRenaming(null)
    if (!name) return
    try {
      const updated = await updateSession(id, { title: name })
      setSessions(current => current.map(item => item.id === id ? { ...item, title: updated.title } : item))
      if (session?.id === id) setTitle(updated.title)
    } catch (e) { setError(`Could not rename: ${ERROR_TEXT(e)}`) }
  }

  // Long chats arrive with their newest messages only; older ones load on request.
  const loadEarlier = async () => {
    const offset = session?.messages_offset || 0
    if (!session || offset <= 0 || loadingEarlier || sending) return
    setLoadingEarlier(true)
    const viewport = messagesViewportRef.current
    const before = viewport ? viewport.scrollHeight - viewport.scrollTop : 0
    try {
      const page = await getEarlierMessages(session.id, offset, 200)
      setMessages(current => [...(page.messages || []), ...current])
      setSession(current => current && current.id === session.id ? { ...current, messages_offset: page.offset } : current)
      requestAnimationFrame(() => { if (viewport) viewport.scrollTop = viewport.scrollHeight - before })
    } catch (e) { setError(`Could not load earlier messages: ${ERROR_TEXT(e)}`) }
    finally { setLoadingEarlier(false) }
  }

  const removeChat = async (id) => {
    setConfirmDelete(null)
    try {
      await deleteSession(id)
      const rest = sessions.filter(item => item.id !== id)
      setSessions(rest)
      setChatHits(hits => hits && hits.filter(item => item.id !== id))
      if (session?.id === id) {
        if (rest[0]) await selectSession(rest[0].id)
        else await handleNewSession()
      }
    } catch (e) { setError(`Could not delete: ${ERROR_TEXT(e)}`) }
  }

  const selectSession = async (id) => {
    stopGeneration()
    setSpeechSegments([])
    setSpeechTiming(null)
    activeSessionIdRef.current = null
    setError('')
    setNotice('')
    const identity = ++requestIdentityRef.current
    try {
      const item = await getSession(id)
      if (!aliveRef.current || identity !== requestIdentityRef.current) return
      activeSessionIdRef.current = item.id
      const roster = normalizeCharacters(item)
      const primary = roster.find(character => character.id === 'primary') || roster[0]
      setSession(item)
      setScene(normalizeScene(item.scene, roster))
      setSceneProgress(null)
      if (!sceneCreation) setAutoSpeak(item.spoken_replies !== false)
      setTitle(item.title || '')
      setCharacters(roster)
      setActiveSpeakerId('primary')
      activeSpeakerIdRef.current = 'primary'
      setProfile(primary.profile)
      const savedSpeechSettings = primary.profile?.voice_settings || null
      if (speechCatalogRef.current.ready) applySpeechSettings(savedSpeechSettings)
      else {
        pendingSpeechSettingsRef.current = { sessionId: item.id, speakerId: 'primary', settings: savedSpeechSettings }
        applySpeechSettings(savedSpeechSettings, { models: [], engines: [] })
      }
      setProfileDirty(false)
      profileEditVersionRef.current += 1
      setMessages(item.messages || [])
      setAudioResult(null)
    } catch (e) { if (identity === requestIdentityRef.current) setError(ERROR_TEXT(e)) }
  }

  const handleNewSession = async () => {
    if (creating) return
    if (!sceneCreation && session && session.kind === 'chat' && !session.roleplay && messages.length === 0) return  // already empty
    stopGeneration()
    setSpeechSegments([])
    setSpeechTiming(null)
    setError('')
    const identity = ++requestIdentityRef.current
    setCreating(true)
    try {
      const initialRoster = [{ id: 'primary', profile: { ...EMPTY_PROFILE } }]
      const item = await createSession({ ...buildRosterPayload(initialRoster, sceneCreation ? 'New scene' : 'New chat'), kind: sceneCreation ? 'scene' : 'chat', spoken_replies: !sceneCreation, ...(sceneCreation ? { user_persona: defaultPersona() } : {}) })
      if (!aliveRef.current) return
      setSessions(current => [item, ...current.filter(old => old.id !== item.id)])
      if (identity !== requestIdentityRef.current) return
      activeSessionIdRef.current = item.id
      const fresh = { ...item, messages: item.messages || [] }
      const roster = normalizeCharacters(fresh)
      setScene(normalizeScene(fresh.scene, roster))
      setSceneProgress(null)
      if (!sceneCreation) setAutoSpeak(item.spoken_replies !== false)
      setSession(fresh)
      setTitle(item.title || '')
      setCharacters(roster)
      setActiveSpeakerId('primary')
      activeSpeakerIdRef.current = 'primary'
      setProfile(roster[0].profile)
      if (speechCatalogRef.current.ready) applySpeechSettings(roster[0].profile.voice_settings || null)
      else pendingSpeechSettingsRef.current = { sessionId: item.id, speakerId: 'primary', settings: roster[0].profile.voice_settings || null }
      setProfileDirty(false)
      profileEditVersionRef.current += 1
      setMessages(item.messages || [])
      setAudioResult(null)
    } catch (e) { if (aliveRef.current) setError(`Could not create conversation: ${ERROR_TEXT(e)}`) }
    finally { if (aliveRef.current) setCreating(false) }
  }

  const assignChatCharacter = async ({ profile: selectedProfile, character_id = null, session_id = null }) => {
    if (sceneCreation || creating || sending || saving || capturing) return
    if (session_id) { await selectSession(session_id); return }
    // Identity is provider-scoped; matching names never merges character chats.
    const existing = character_id && sessions.find(item => item.roleplay && item.character_id === character_id)
    if (existing) { await selectSession(existing.id); return }
    stopGeneration()
    const identity = ++requestIdentityRef.current
    setCreating(true)
    setError('')
    try {
      const item = await createSession({ kind: 'chat', roleplay: true, character_id, user_persona: defaultPersona(),
        profile: selectedProfile || { ...EMPTY_PROFILE },
        title: `${selectedProfile?.name || 'Custom character'} chat`, spoken_replies: true })
      if (!aliveRef.current || identity !== requestIdentityRef.current) return
      setSessions(current => [item, ...current.filter(old => old.id !== item.id)])
      await selectSession(item.id)
      setNotice('Character chat is ready. Review the editable context and choose a voice; catalog lore can be incomplete.')
    } catch (e) { if (aliveRef.current && identity === requestIdentityRef.current) setError(`Could not open character chat: ${ERROR_TEXT(e)}`) }
    finally { if (aliveRef.current) setCreating(false) }
  }

  const selectDirectChat = () => {
    const direct = sessions.find(item => !item.roleplay)
    if (direct) selectSession(direct.id)
    else handleNewSession()
  }

  const createFromProfile = async () => {
    if (creating) return
    const requestedTitle = title.trim() || 'Character conversation'
    const requestedRoster = saveableRoster()
    requestedRoster.title = requestedTitle
    const identity = ++requestIdentityRef.current
    setCreating(true)
    setError('')
    try {
      const item = await createSession({ ...requestedRoster, kind: sceneCreation ? 'scene' : 'chat', ...(sceneCreation ? { user_persona: defaultPersona() } : {}) })
      if (!aliveRef.current) return
      const fresh = { ...item, messages: item.messages || [] }
      setSessions(current => [fresh, ...current.filter(old => old.id !== fresh.id)])
      if (identity !== requestIdentityRef.current) return
      activeSessionIdRef.current = fresh.id
      setSession(fresh)
      setTitle(fresh.title || requestedTitle)
      const roster = normalizeCharacters(fresh)
      setScene(normalizeScene(fresh.scene, roster))
      setSceneProgress(null)
      setCharacters(roster)
      setActiveSpeakerId('primary')
      activeSpeakerIdRef.current = 'primary'
      setProfile(roster[0].profile)
      const voiceSettings = roster[0].profile?.voice_settings || null
      if (speechCatalogRef.current.ready) applySpeechSettings(voiceSettings)
      else pendingSpeechSettingsRef.current = { sessionId: fresh.id, speakerId: 'primary', settings: voiceSettings }
      setProfileDirty(false)
      profileEditVersionRef.current += 1
      setMessages(fresh.messages)
      setNotice('Character details were imported for review. No message was sent.')
    } catch (e) { if (identity === requestIdentityRef.current) setError(`Could not create conversation: ${ERROR_TEXT(e)}`) }
    finally { if (aliveRef.current) setCreating(false) }
  }

  const rememberedModel = (prov) => { try { return localStorage.getItem(`vv.chatModel.${prov}`) || '' } catch { return '' } }
  const chooseModel = (value) => {
    setModel(value)
    try { localStorage.setItem(`vv.chatModel.${provider}`, value) } catch { /* private mode */ }
  }

  const connect = async ({ quiet = false } = {}) => {
    const identity = ++connectIdentityRef.current
    const requestedProvider = provider
    const requestedEndpoint = endpoint.trim()
    const requestedKey = apiKey.trim()
    setConnecting(true)
    setConnected(false)
    setError('')
    try {
      const result = await listChatModels({ provider: requestedProvider, base_url: requestedEndpoint, ...(requestedKey ? { api_key: requestedKey } : {}) })
      if (!aliveRef.current || identity !== connectIdentityRef.current) return
      const items = result.models || []
      const visibleItems = modelsForDisplay(requestedProvider, items)
      setModels(visibleItems)
      const remembered = rememberedModel(requestedProvider)
      setModel(current => visibleItems.some(item => item.id === current) ? current
        : visibleItems.some(item => item.id === remembered) ? remembered : visibleItems[0]?.id || '')
      setConnected(true)
      setNotice(visibleItems.length ? `Connected. ${visibleItems.length} model${visibleItems.length === 1 ? '' : 's'} available${isNativeCloudProvider(requestedProvider) ? ' in this bounded list' : ''}.` : 'Connected, but the endpoint returned no models.')
    } catch (e) {
      if (!aliveRef.current || identity !== connectIdentityRef.current) return
      setConnected(false)
      const providerName = LLM_PROVIDERS.find(item => item.id === requestedProvider)?.label || 'chat service'
      if (quiet) setNotice(`${providerName} isn't answering at ${requestedEndpoint || 'its usual address'}. Start it, then press "Test connection & load models".`)
      else setError(`Could not connect to ${providerName}: ${ERROR_TEXT(e)}`)
    } finally { if (aliveRef.current && identity === connectIdentityRef.current) setConnecting(false) }
  }

  useEffect(() => {
    if (!autoConnect || connected || connecting || sending || !endpoint.trim()) return
    setAutoConnect(false)
    if (providerRequiresApiKey(provider) && !apiKey.trim()) return   // cloud services: the user connects
    connect({ quiet: true })
  }, [autoConnect, connected, connecting, sending, endpoint, provider, apiKey])   // eslint-disable-line react-hooks/exhaustive-deps

  const changeChatDestination = (nextProvider, nextEndpoint) => {
    if (sending) return
    connectIdentityRef.current += 1
    connectionEditVersionRef.current += 1
    setConnecting(false)
    setProvider(nextProvider)
    if (nextProvider !== provider) setNameOnlyCloudContext(false)
    setEndpoint(nextEndpoint)
    setApiKey('')
    setModels([])
    setModel('')
    setConnected(false)
    setError('')
    setNotice('')
  }

  const updateChatProvider = value => {
    const nextProvider = normalizeLlmProvider(value)
    if (nextProvider === provider) return
    const defaultEndpoint = providerDefaultEndpoint(nextProvider)
    changeChatDestination(nextProvider, defaultEndpoint ?? endpoint)
  }

  const updateChatEndpoint = value => {
    if (sending) return
    connectIdentityRef.current += 1
    connectionEditVersionRef.current += 1
    setConnecting(false)
    setEndpoint(value)
    setApiKey('')
    setModels([])
    setModel('')
    setConnected(false)
    setError('')
    setNotice('')
  }

  const updateChatApiKey = value => {
    if (sending) return
    connectIdentityRef.current += 1
    connectionEditVersionRef.current += 1
    setConnecting(false)
    setApiKey(value)
    setModels([])
    setModel('')
    setConnected(false)
    setError('')
    setNotice('')
  }

  const saveProfile = async () => {
    if (!session || saving || sending || portraitUploading) return
    const sessionId = session.id
    const editVersion = profileEditVersionRef.current
    const payload = saveableRoster()
    setSaving(true)
    setError('')
    try {
      const updated = await updateSession(sessionId, payload)
      if (!aliveRef.current || sessionId !== updated.id || activeSessionIdRef.current !== updated.id) return
      setSession(current => ({ ...current, ...updated }))
      const roster = normalizeCharacters(updated)
      setCharacters(roster)
      const active = roster.find(item => item.id === activeSpeakerId) || roster[0]
      setProfile(active.profile)
      setSessions(current => [updated, ...current.filter(item => item.id !== updated.id)])
      if (editVersion === profileEditVersionRef.current) {
        setProfileDirty(false)
        setNotice(sceneCreation ? 'Character profile saved.' : 'Chat voice and preferences saved.')
      }
    } catch (e) { setError(`Could not save character profile: ${ERROR_TEXT(e)}`) }
    finally { if (aliveRef.current) setSaving(false) }
  }

  // ── Cast and personas ──
  const refreshCastAndPersonas = useCallback(async () => {
    try {
      const [personaList, cast] = await Promise.all([listPersonas(), listCast()])
      if (!aliveRef.current) return
      setPersonas(personaList.personas || [])
      setDefaultPersonaId(personaList.default_id || null)
      setCastMembers(cast || [])
    } catch { /* The Cast is optional; chats work without it. */ }
    finally { if (aliveRef.current) setPersonasLoaded(true) }
  }, [])
  useEffect(() => { refreshCastAndPersonas() }, [refreshCastAndPersonas])

  function defaultPersona() {
    return personaSnapshot(personas.find(item => item.id === defaultPersonaId)) || null
  }

  // Cast → Chat opens /converse?cast=<id>
  useEffect(() => {
    if (sceneCreation || loading || !personasLoaded || castParamHandledRef.current) return
    const castId = new URLSearchParams(location.search).get('cast')
    if (!castId) return
    castParamHandledRef.current = true
    navigate('/converse', { replace: true })
    getCastMember(castId)
      .then(async member => {
        const existing = sessions.find(item => item.roleplay && item.character_id === `cast:${member.id}`)
        await assignChatCharacter({ profile: member.profile, character_id: `cast:${member.id}` })
        if (existing && aliveRef.current) {
          setNotice(`Opened your existing chat with ${member.profile?.name || 'this character'}. If you changed them in Cast since, choose them under "Load from your Cast…" and press Save profile to update this chat.`)
        }
      })
      .catch(e => setError(`Could not open this cast member: ${ERROR_TEXT(e)}`))
  }, [sceneCreation, loading, personasLoaded, location.search])

  const changePersona = async id => {
    if (!session) return
    const chosen = id ? personaSnapshot(personas.find(item => item.id === id)) : null
    if (id && !chosen) return
    try {
      const updated = await updateSession(session.id, { user_persona: chosen })
      if (!aliveRef.current) return
      setSession(current => current?.id === updated.id ? { ...current, user_persona: updated.user_persona } : current)
      setSessions(current => current.map(item => item.id === updated.id ? { ...item, user_persona: updated.user_persona } : item))
      setNotice(chosen ? `You're now playing ${chosen.name}.` : 'You are now chatting as yourself.')
    } catch (e) { setError(`Could not change persona: ${ERROR_TEXT(e)}`) }
  }

  const loadFromCast = async castId => {
    if (!castId) return
    try {
      const member = await getCastMember(castId)
      if (!aliveRef.current) return
      setActiveProfile({ ...EMPTY_PROFILE, ...member.profile })
      applySpeechSettings(member.profile?.voice_settings || null)
      setHasSavedSpeechSettings(!!member.profile?.voice_settings?.model_id)
      setNotice(`${member.profile?.name || 'Cast member'} loaded from your Cast. Use Save profile to keep it in this conversation.`)
    } catch (e) { setError(`Could not load from the Cast: ${ERROR_TEXT(e)}`) }
  }

  const saveToCast = async () => {
    const current = saveableProfile()
    if (!current.name?.trim()) { setError('Name the character before saving it to your Cast.'); return }
    setCastBusy(true)
    setError('')
    try {
      let existing = current.cast_id ? await getCastMember(current.cast_id).catch(() => null) : null
      if (!existing && current.catalog_identity) existing = castMembers.find(item => item.profile?.catalog_identity === current.catalog_identity) || null
      if (existing && !current.cast_id && !window.confirm(`${existing.profile?.name || 'This character'} is already in your Cast. Replace its saved setup with this one? The current version stays in its History.`)) return
      // Edited outside the lore check: the result needs the user's review again, and an established
      // character's sources no longer vouch for every line.
      const reviewNote = 'Edited in a conversation after the lore check; review it before relying on it.'
      const questions = existing?.kind === 'established' && existing?.sources?.length
        ? [...(existing.open_questions || []).filter(q => q !== reviewNote), reviewNote].slice(-20) : (existing?.open_questions || [])
      const saved = existing
        ? await updateCastMember(existing.id, castPayload(existing, current, { note: 'Saved from a conversation', review_state: 'draft', open_questions: questions }))
        : await createCastMember(castPayload(null, current))
      if (!aliveRef.current) return
      setActiveProfile({ ...profile, cast_id: saved.id })
      await refreshCastAndPersonas()
      setNotice(`Saved to your Cast. Use Save profile to link this conversation to ${saved.profile?.name}.`)
    } catch (e) { setError(`Could not save to the Cast: ${ERROR_TEXT(e)}`) }
    finally { if (aliveRef.current) setCastBusy(false) }
  }

  const applyAudioTranscript = (mode, text) => {
    setDraft(current => applyTranscriptToDraft(current, mode, text))
  }

  const send = async (event, sceneRun = false) => {
    event?.preventDefault()
    const text = draft.trim()
    if ((!sceneRun && !text) || !session || !connected || !model || sending || saving || speaking || capturing || previewPlaying || portraitUploading) return
    const requestedRoster = saveableRoster()
    if (sceneRun && !requestedRoster.scene.participant_ids.length) return
    const identity = ++requestIdentityRef.current
    const sessionId = session.id
    const requestedProvider = provider
    const requestedNameOnlyContext = nameOnlyCloudContext
    const requestedEndpoint = endpoint.trim()
    const requestedModel = model
    const requestedApiKey = apiKey.trim()
    let speakerId = activeSpeakerId
    let speakerName = profile.name || 'Character'
    const selectedVoice = { id: speakerId, profile: saveableProfile() }
    let snapshot = null
    if (autoSpeak) {
      try {
        if (sceneRun) {
          for (const id of requestedRoster.scene.participant_ids) speechSnapshot(requestedRoster.characters.find(item => item.id === id), speechCatalogRef.current)
        }
        snapshot = speechSnapshot(sceneRun ? requestedRoster.characters.find(item => item.id === requestedRoster.scene.participant_ids[0]) : selectedVoice, speechCatalogRef.current)
      }
      catch (e) {
        if (sceneCreation) { setError(`Choose a saved voice before speaking while replying: ${ERROR_TEXT(e)}`); return }
        setNotice(`Text replies are available. Choose a usable voice to hear spoken responses: ${ERROR_TEXT(e)}`)
      }
    }
    // Unlock Web Audio from Send's user gesture, before saving or fetching.
    let clausePlayer = null
    try { if (snapshot) clausePlayer = new StreamedSpeechPlayer({ maxQueuedSeconds: 10 }) }
    catch (e) { setError(`Speech playback is unavailable: ${ERROR_TEXT(e)}`); return }
    stopAudio()
    setAudioResult(null)
    setSpeechSegments([])
    setSpeechTiming(null)
    if (clausePlayer) speechPlayerRef.current = clausePlayer
    const unlock = clausePlayer?.start()
    unlock?.catch(() => {})
    setSending(true)
    setSceneProgress(sceneRun ? { status: 'running', completed: 0, turn_index: 0, total_turns: requestedRoster.scene.participant_ids.length * requestedRoster.scene.rounds } : null)
    setError('')
    if (snapshot || !autoSpeak) setNotice('')
    try {
      if (profileDirty) {
        const updated = await updateSession(sessionId, requestedRoster)
        if (!aliveRef.current || identity !== requestIdentityRef.current || activeSessionIdRef.current !== sessionId) { clausePlayer?.stop(); return }
        setSession(current => ({ ...current, ...updated }))
        const roster = normalizeCharacters(updated)
        setCharacters(roster)
        const active = roster.find(item => item.id === speakerId) || roster[0]
        setProfile(active.profile)
        setSessions(current => [updated, ...current.filter(item => item.id !== updated.id)])
        setProfileDirty(false)
      }
    } catch (e) {
      if (identity === requestIdentityRef.current && aliveRef.current) setError(`Could not save the edited character profile: ${ERROR_TEXT(e)}. Your message is still in the composer.`)
      if (identity === requestIdentityRef.current && aliveRef.current) setSending(false)
      if (sceneRun && identity === requestIdentityRef.current && aliveRef.current) setSceneProgress(current => ({ ...current, status: 'error' }))
      clausePlayer?.stop()
      return
    }
    if (!sceneRun) setDraft('')
    followLatestRef.current = true
    const controller = new AbortController()
    controllerRef.current = controller
    let queue = null
    let speechController = null
    let turnDelivery = snapshot?.body.delivery
    let spokenCharacters = 0
    let segmentCount = 0
    const turnStarted = performance.now()
    let firstAudio = null
    const audioTurns = []          // per speech turn: { speaker_id, text, files, index, complete } to link for replay
    let currentTurnAudio = null
    const replyIndexIn = updated => {
      const list = updated?.messages || []
      return list.length && list[list.length - 1]?.role === 'assistant' ? (updated.messages_offset || 0) + list.length - 1 : null
    }
    let speechOk = true
    const speechFailed = e => {
      speechOk = false
      queue?.stop()
      speechController?.abort()
      clausePlayer?.stop()
      if (aliveRef.current && identity === requestIdentityRef.current) {
        setSpeaking(false)
        setAudioPlaying(false)
        setNotice(`Text continues; speech stopped: ${ERROR_TEXT(e)}`)
      }
    }
    const startSpeechTurn = () => {
      queue = null
      if (!snapshot || clausePlayer?.stopped) return
      turnDelivery = snapshot.body.delivery
      spokenCharacters = 0
      segmentCount = 0
      speechController = new AbortController()
      speechControllerRef.current = speechController
      speechPlayerRef.current = clausePlayer
      setSpeaking(true)
      // Capture values in this worker so a later scene turn cannot change it.
      const turnSnapshot = snapshot
      const turnSpeechController = speechController
      const turnAudio = { speaker_id: speakerId, text: '', files: [], index: null, complete: false }
      audioTurns.push(turnAudio)
      currentTurnAudio = turnAudio
      queue = createSpeechQueue({ firstMinChunk: 16, play: async (clause, { signal }) => {
        if (signal.aborted || turnSpeechController.signal.aborted || identity !== requestIdentityRef.current) return
        if (++segmentCount > 32 || (spokenCharacters += clause.length) > 8000) throw new Error('The 32 sentence / 8,000 character speech limit was reached. The full text remains available.')
        await unlock
        const segmentStarted = performance.now()
        const result = await streamTts({ ...turnSnapshot.body, text: clause,
          delivery: { ...turnDelivery, expression: segmentCount === 1 ? turnDelivery?.expression || null : null },
        }, {
          signal: turnSpeechController.signal, url: `${BACKEND_URL}/api/tts/segment`,
          onAudio: async chunk => {
            if (signal.aborted || turnSpeechController.signal.aborted || identity !== requestIdentityRef.current) return
            await clausePlayer.enqueue(chunk)
            if (firstAudio === null) {
              firstAudio = performance.now() - turnStarted
              setSpeechTiming({ firstAudioMs: firstAudio, segments: segmentCount })
            }
            setAudioPlaying(true)
          },
        })
        if (signal.aborted || turnSpeechController.signal.aborted || identity !== requestIdentityRef.current) return
        setSpeechSegments(current => [...current, { ...result, speaker_name: turnSnapshot.speakerName, segment_ms: performance.now() - segmentStarted }])
        if (result?.file) { turnAudio.files.push(result.file); if (!turnAudio.text) turnAudio.text = clause }
        clausePlayer.pause(PACE_GAP_SECONDS[turnSnapshot.pacing] || 0)
        setSpeechTiming({ firstAudioMs: firstAudio, segments: segmentCount })
        if (result.meta?.delivery_warnings?.length) setNotice(result.meta.delivery_warnings.join(' '))
        if (result.meta?.truncated) throw new Error('The voice model reached its generation limit during a sentence.')
      } })
      speechQueueRef.current = queue
      queue.done.then(outcome => { if (outcome.error) speechFailed(outcome.error) })
    }
    if (!sceneRun) startSpeechTurn()
    if (!sceneRun) setMessages(current => [...current, { role: 'user', content: text }, {
      role: 'assistant', speaker_id: speakerId, speaker_name: speakerName, content: '',
      ...((sceneCreation || roleplay) ? { profile_snapshot: structuredClone(selectedCharacter?.profile || {}) } : {}),
    }])
    const acceptSession = updated => {
      if (!aliveRef.current || identity !== requestIdentityRef.current || sessionId !== activeSessionIdRef.current || !updated) return
      setMessages(updated.messages || [])
      setSession(updated)
      const roster = normalizeCharacters(updated)
      setCharacters(roster)
      const active = roster.find(item => item.id === activeSpeakerId) || roster[0]
      setProfile(active.profile)
      setSessions(current => [updated, ...current.filter(item => item.id !== updated.id)])
    }
    try {
      const stream = sceneRun ? streamScene : streamMessage
      await stream(sessionId, { ...(!sceneRun ? { text, speaker_id: speakerId } : {}), propose_delivery: proposeDelivery,
        provider: requestedProvider, base_url: requestedEndpoint, model: requestedModel,
        name_only_context: isNativeCloudProvider(requestedProvider) && requestedNameOnlyContext,
        ...(contextTokens >= 1024 ? { context_tokens: contextTokens } : {}),
        ...(requestedApiKey ? { api_key: requestedApiKey } : {}) }, {
        signal: controller.signal,
        onTurnStart: payload => {
          if (!aliveRef.current || identity !== requestIdentityRef.current) return
          speakerId = payload.speaker_id
          speakerName = payload.speaker_name
          setSceneProgress(current => ({ ...current, ...payload, status: 'running' }))
          snapshot = autoSpeak && !clausePlayer?.stopped ? speechSnapshot({ id: speakerId, profile: payload.profile_snapshot }, speechCatalogRef.current) : null
          startSpeechTurn()
          setMessages(current => [...current, { role: 'assistant', speaker_id: speakerId, speaker_name: speakerName, profile_snapshot: payload.profile_snapshot, content: '' }])
        },
        onTurnDone: async payload => {
          if (!aliveRef.current || identity !== requestIdentityRef.current) return
          acceptSession(payload.session)
          const turn = currentTurnAudio
          if (turn) turn.index = replyIndexIn(payload.session)
          if (queue && !queue.stopped) {
            try {
              const outcome = await queue.flush()
              if (turn && outcome?.status === 'done' && !clausePlayer?.stopped) turn.complete = true
            } catch (e) { speechFailed(e) }
          }
          if (identity === requestIdentityRef.current) setSceneProgress(current => ({ ...current, completed: payload.turn_index }))
        },
        onSceneDone: payload => {
          if (Array.isArray(payload.memory) && payload.memory.length) setMemoryInfo(payload.memory[payload.memory.length - 1])
          acceptSession(payload.session)
          if (identity === requestIdentityRef.current) setSceneProgress(current => ({ ...current, status: 'complete', completed: payload.total_turns }))
        },
        onDelivery: payload => {
          if (!aliveRef.current || identity !== requestIdentityRef.current || payload.speaker_id !== speakerId) return
          turnDelivery = Object.freeze({ ...payload.delivery })
          setMessages(current => current.map((msg, index) => index === current.length - 1 ? { ...msg, delivery: payload.delivery } : msg))
          if (payload.warnings?.length) setNotice(payload.warnings.join(' '))
        },
        onDelta: async (chunk, metadata = {}) => {
          if (!aliveRef.current || identity !== requestIdentityRef.current) return
          setMessages(current => current.map((msg, index) => index === current.length - 1 ? {
            ...msg,
            speaker_id: metadata.speaker_id || msg.speaker_id || speakerId,
            speaker_name: metadata.speaker_name || msg.speaker_name || speakerName,
            content: msg.content + chunk,
          } : msg))
          if (queue && !queue.stopped) {
            try { await queue.addText(chunk) } catch (e) { speechFailed(e) }
          }
        },
        onDone: (updated, payload) => {
          acceptSession(updated)
          if (currentTurnAudio) currentTurnAudio.index = replyIndexIn(updated)
          if (payload?.memory) setMemoryInfo(payload.memory)
        },
      })
      if (queue && !queue.stopped) {
        if (!sceneRun) setSending(false)
        try {
          const turn = currentTurnAudio
          const outcome = await queue.flush()
          await clausePlayer.finish()
          if (turn && outcome?.status === 'done' && !clausePlayer.stopped) turn.complete = true
        } catch (e) { speechFailed(e) }
      }
    } catch (e) {
      queue?.stop()
      speechController?.abort()
      clausePlayer?.stop()
      if (!controller.signal.aborted && identity === requestIdentityRef.current && aliveRef.current) {
        setError(sceneRun ? `Scene stopped: ${ERROR_TEXT(e)}. Saved turns remain in the conversation.` : `Message failed: ${ERROR_TEXT(e)}. Your message is available in the composer for review.`)
        if (sceneRun) {
          setSceneProgress(current => ({ ...current, status: 'error' }))
          try { acceptSession(await getSession(sessionId)) } catch { /* Retain the visible partial if reload fails. */ }
        } else {
          setDraft(current => current || text)
          try { acceptSession(await getSession(sessionId)) } catch { /* Retain the visible partial if reload fails. */ }
        }
      }
    } finally {
      if (identity === requestIdentityRef.current && aliveRef.current) {
        setSending(false)
        controllerRef.current = null
        setSpeaking(false)
        setAudioPlaying(false)
      }
      clausePlayer?.stop()
      if (speechOk && !controller.signal.aborted) {
        // Only speech that played to the end is kept for replay; a stopped reply offers Speak instead.
        for (const turn of audioTurns) {
          if (!turn.complete || !turn.files.length) continue
          rememberAudio(sessionId, turn.index !== null
            ? { files: turn.files, index: turn.index }
            : { files: turn.files, text: turn.text, speaker_id: turn.speaker_id })
        }
      }
      if (speechQueueRef.current === queue) speechQueueRef.current = null
      if (speechControllerRef.current === speechController) speechControllerRef.current = null
      if (speechPlayerRef.current === clausePlayer) speechPlayerRef.current = null
    }
  }

  const speakLastReply = (target = null) => {
    const reply = target || [...messages].reverse().find(item => item.role === 'assistant' && item.content?.trim())
    return speakReply(reply)
  }

  // Remember which audio was spoken for a reply, so its play button can replay it later.
  const rememberAudio = async (sessionId, link) => {
    for (let attempt = 0; attempt < 60; attempt += 1) {
      try {
        const saved = await linkMessageAudio(sessionId, link)
        if (!aliveRef.current || activeSessionIdRef.current !== sessionId) return
        setMessages(current => {
          const offset = sessionOffsetRef.current
          return current.map((msg, i) => i + offset === saved.index ? { ...msg, audio: saved.audio } : msg)
        })
        return
      } catch (cause) {
        if (!/still writing|409/.test(String(cause?.message))) return   // nothing to retry; replay just won't be offered
        await new Promise(resolve => setTimeout(resolve, 3000))
      }
    }
  }

  const speakReply = async (reply) => {
    const voice = voiceForReply(reply)
    if (!reply || !voice.character || !voice.model || voice.problem || speaking || sending || capturing) return
    const replyIndex = messages.indexOf(reply)
    const sessionAtStart = session?.id
    const absoluteIndex = replyIndex >= 0 ? replyIndex + (session?.messages_offset || 0) : null
    const replyCharacter = voice.character
    const voiceProfile = replyCharacter.profile
    const voiceSettings = voiceProfile.voice_settings || {}
    const voiceModelId = voiceSettings.model_id
    const voiceMode = voice.mode
    const voiceReferenceId = voice.referenceId
    const voiceCapabilities = voice.capabilities
    const identity = ++requestIdentityRef.current
    setSpeaking(true)
    setError('')
    setNotice('')
    try {
      let speechText = spokenText(reply.content)
      if (speechText.length > 5000) {
        speechText = speechText.slice(0, 5000)
        setNotice('This reply is longer than 5,000 characters. Only its first 5,000 characters will be spoken.')
      }
      if (!speechText.trim()) throw new Error('No spoken dialogue remained after filtering stage directions.')
      if (voiceMode === 'reference' && !voiceCapabilities.reference_audio) throw new Error('This character’s saved reference voice mode is unavailable.')
      if (voiceMode === 'description' && !voiceCapabilities.voice_description && voiceCapabilities.reference_audio) throw new Error('This character’s saved description voice mode is unavailable.')
      if (voiceMode === 'reference' && (!voiceReferenceId || !references.some(item => item.reference_id === voiceReferenceId))) throw new Error('This character’s saved reference clip is unavailable. Choose another clip in the character profile.')
      const snapshotForReply = speechSnapshot(replyCharacter, speechCatalogRef.current)
      const body = {
        ...snapshotForReply.body,
        pacing: snapshotForReply.pacing,
        text: speechText,
        delivery: reply.delivery || voiceSettings.delivery || { emotion: 'neutral', style: 'natural', expression: null },
      }
      stopAudio()
      setAudioResult(null)
      setSpeechSegments([])
      setSpeechTiming(null)
      if (voiceCapabilities.streaming) {
        const controller = new AbortController()
        const player = new StreamedSpeechPlayer()
        speechControllerRef.current = controller
        speechPlayerRef.current = player
        try {
          // Unlock playback within the initiating click before fetching audio.
          await player.start()
          if (controller.signal.aborted) return
          const result = await streamTts(body, {
            signal: controller.signal,
            url: `${BACKEND_URL}/api/tts/stream`,
            onAudio: chunk => player.enqueue(chunk),
          })
          await player.finish()
          if (!aliveRef.current || identity !== requestIdentityRef.current) return
          setAudioResult(result)
          if (result?.file && sessionAtStart && absoluteIndex !== null) rememberAudio(sessionAtStart, { files: [result.file], index: absoluteIndex })
          if (result.meta?.truncated) setNotice('The voice model reached its generation limit. The saved audio may be incomplete.')
        } finally {
          player.stop()
          if (speechPlayerRef.current === player) speechPlayerRef.current = null
          if (speechControllerRef.current === controller) speechControllerRef.current = null
        }
        return
      }
      const result = await synthesize(body)
      if (!aliveRef.current || identity !== requestIdentityRef.current) return
      setAudioResult(result)
      if (result?.file && sessionAtStart && absoluteIndex !== null) rememberAudio(sessionAtStart, { files: [result.file], index: absoluteIndex })
      if (result.meta?.delivery_warnings?.length) setNotice(result.meta.delivery_warnings.join(' '))
      requestAnimationFrame(() => {
        if (!aliveRef.current || identity !== requestIdentityRef.current || !audioRef.current) return
        audioRef.current.play().catch(() => {
          if (aliveRef.current && identity === requestIdentityRef.current) setNotice('Speech is ready. Press play in the audio control to start playback.')
        })
      })
    } catch (e) {
      if (aliveRef.current && identity === requestIdentityRef.current) setError(`Text is ready; speech could not be generated: ${ERROR_TEXT(e)}`)
    } finally { if (aliveRef.current && identity === requestIdentityRef.current) setSpeaking(false) }
  }

  const updateProfile = (key, value) => setActiveProfile({ ...profile, [key]: value })
  const updateTitle = (value) => { profileEditVersionRef.current += 1; setTitle(value); setProfileDirty(true) }
  const updateVoiceField = (field, value) => {
    const settings = {
      ...profile.voice_settings,
      model_id: ttsModelId || null,
      reference_id: referenceId || null,
      speech_mode: speechMode,
      delivery,
      [field]: value,
    }
    if (field === 'model_id') setTtsModelId(value || '')
    else if (field === 'reference_id') setReferenceId(value || '')
    else if (field === 'speech_mode') {
      setSpeechMode(value)
      const defaults = { emotion: 'neutral', style: 'natural', expression: null }
      setDelivery(defaults)
      settings.delivery = defaults
    }
    updateSavedVoiceSettings(settings)
  }

  const updateVoiceEngine = id => {
    const nextEngine = ttsEngines.find(item => item.id === id)
    const nextModel = ttsModels.find(item => item.engine_id === id && USABLE_TTS.has(item.state))
    const mode = nextEngine?.capabilities?.reference_audio && !nextEngine?.capabilities?.voice_description ? 'reference' : 'description'
    setTtsEngineId(id)
    setTtsModelId(nextModel?.id || '')
    setReferenceId('')
    setSpeechMode(mode)
    const defaults = { emotion: 'neutral', style: 'natural', expression: null }
    setDelivery(defaults)
    updateSavedVoiceSettings({ engine_id: id, model_id: nextModel?.id || null, reference_id: null, speech_mode: mode, delivery: defaults })
  }

  const applyVoicePreset = preset => {
    requestIdentityRef.current += 1; stopAudio(); setSpeaking(false); setAudioResult(null)
    const settings = presetVoiceSettings(preset)
    applySpeechSettings(settings)
    setActiveProfile({ ...profile, voice_description: preset.voice_description || '', voice_settings: settings })
    setNotice(sceneCreation ? `“${preset.name}” copied to ${profile.name || 'this character'}. Save the profile to keep this assignment.` : `“${preset.name}” selected for chat. Its exact settings will be saved with your next message.`)
  }

  const currentVoicePreset = { engine_id: ttsEngineId, model_id: ttsModelId, speech_mode: speechMode,
    reference_id: referenceId || null, voice_description: profile.voice_description || '',
    reference_text: profile.voice_settings?.reference_text || '', language: profile.voice_settings?.language || '',
    delivery, generation: { ...DEFAULT_GENERATION, ...profile.voice_settings?.generation } }

  const selectSpeaker = id => {
    if (id === activeSpeakerId) return
    const character = characters.find(item => item.id === id)
    if (!character) return
    requestIdentityRef.current += 1
    stopAudio()
    setSpeaking(false)
    setAudioResult(null)
    setActiveSpeakerId(id)
    activeSpeakerIdRef.current = id
    setProfile(character.profile)
    if (speechCatalogRef.current.ready) applySpeechSettings(character.profile.voice_settings || null)
    else {
      applySpeechSettings(null, { models: [], engines: [] })
      pendingSpeechSettingsRef.current = { sessionId: session?.id, speakerId: id, settings: character.profile.voice_settings || null }
    }
  }

  const addCharacter = () => {
    if (!session || characters.length >= MAX_CHAT_CHARACTERS || creating || saving || sending) return
    requestIdentityRef.current += 1
    stopAudio()
    setSpeaking(false)
    setAudioResult(null)
    const character = { id: newCharacterId(), profile: { ...EMPTY_PROFILE } }
    setCharacters(current => [...current, character])
    setActiveSpeakerId(character.id)
    activeSpeakerIdRef.current = character.id
    setProfile(character.profile)
    applySpeechSettings(null)
    profileEditVersionRef.current += 1
    setProfileDirty(true)
    setNotice('New character added. Give them a name and choose a voice before saving the roster.')
  }

  const removeCharacter = id => {
    if (id === 'primary' || characters.length <= 1 || saving || sending) return
    requestIdentityRef.current += 1
    const next = removeSecondaryCharacter(characters, id)
    setCharacters(next)
    setScene(current => normalizeScene(current, next))
    setSceneProgress(null)
    stopAudio()
    setSpeaking(false)
    setAudioResult(null)
    if (activeSpeakerId === id) {
      const primary = next.find(item => item.id === 'primary') || next[0]
      setActiveSpeakerId(primary.id)
      activeSpeakerIdRef.current = primary.id
      setProfile(primary.profile)
      applySpeechSettings(primary.profile.voice_settings || null)
    }
    profileEditVersionRef.current += 1
    setProfileDirty(true)
    setNotice('Character removed from the roster. Their saved message labels and history remain.')
  }

  const stopSpeech = () => {
    const wasStreaming = !!speechControllerRef.current
    if (!sending) requestIdentityRef.current += 1
    setSpeaking(false)
    stopAudio()
    setNotice(wasStreaming ? 'Speech stopped and pending sentences were cleared. Complete-output models finish their active sentence before releasing; text can continue.' : 'Speech synthesis may continue on the voice backend after stopping here.')
  }
  const chosenModelName = useMemo(() => models.find(item => item.id === model)?.id || '', [models, model])

  return (
    <div className="flex-1 min-h-0 flex flex-col p-4 md:p-7 gap-5 animate-fade-in xl:h-full xl:overflow-hidden">
      <header className="flex items-center justify-between gap-4 shrink-0">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-vault-elevated border border-vault-border flex items-center justify-center"><MessageCircle size={20} className="text-vault-accent" /></div>
          <div><div className="flex items-center gap-3"><h1 className="font-display font-bold text-2xl text-vault-text">Converse</h1><div role="tablist" aria-label="Conversation type" className="inline-flex rounded-lg border border-vault-border p-0.5">{[['chat', 'Chat', '/converse'], ['scene', 'Scene', '/scene-creation']].map(([id, label, to]) => <button key={id} type="button" role="tab" aria-selected={(id === 'scene') === !!sceneCreation} onClick={() => navigate(to)} className={`px-2.5 py-0.5 rounded-md font-mono text-[11px] ${(id === 'scene') === !!sceneCreation ? 'bg-vault-elevated text-vault-glow' : 'text-vault-muted hover:text-vault-text'}`}>{label}</button>)}</div></div><p className="font-body text-sm text-vault-muted">{sceneCreation ? 'Build character scenes with saved voices and bounded rounds.' : 'Chat with your LLM or a favorite character. Type, speak, and hear replies.'}</p></div>
        </div>
        <div className="hidden md:flex items-center gap-2 text-xs font-mono text-vault-muted"><span className={`w-2 h-2 rounded-full ${connected ? 'bg-emerald-400' : 'bg-vault-border'}`} />{connected ? `Connected · ${chosenModelName}` : 'Chat model disconnected'}</div>
      </header>

      {error && <div role="alert" className="flex gap-2 items-start border border-red-400/40 bg-red-400/10 rounded-lg px-4 py-3 text-sm text-red-200"><AlertCircle size={16} className="mt-0.5 shrink-0" />{error}</div>}
      {notice && <div role="status" className="rounded-lg border border-vault-border bg-vault-elevated/50 px-4 py-2 text-xs text-vault-muted">{notice}</div>}

      <div className="grid grid-cols-1 xl:grid-cols-[250px_minmax(0,1fr)_310px] gap-4 flex-1 min-h-0 xl:overflow-hidden">
        <aside className="vault-card p-4 flex flex-col min-h-[220px] xl:min-h-0 xl:overflow-hidden">
          <div className="flex items-center justify-between mb-3"><span className="vault-label">{sceneCreation ? 'Scenes' : 'Chats'}</span><button type="button" onClick={handleNewSession} disabled={creating} aria-label={sceneCreation ? 'New scene' : 'New chat'} className="p-2 rounded-lg text-vault-muted hover:text-vault-text hover:bg-vault-elevated disabled:opacity-40"><Plus size={16} /></button></div>
          <label className="relative mb-2 block">
            <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-vault-muted" />
            <input value={chatQuery} onChange={e => setChatQuery(e.target.value)} placeholder={sceneCreation ? 'Search scenes and messages' : 'Search chats and messages'} aria-label="Search chats"
              className="w-full bg-vault-elevated border border-vault-border rounded-lg pl-7 pr-2 py-1.5 text-xs text-vault-text" />
          </label>
          <div className="flex flex-col gap-1 overflow-y-auto">
            {loading && <p className="text-xs text-vault-muted p-2">Loading…</p>}
            {!loading && sessions.length === 0 && <p className="text-xs text-vault-muted p-2">{sceneCreation ? 'Create a scene to choose characters.' : 'Start a new chat.'}</p>}
            {(chatHits ?? sessions).map(item => (
              <div key={item.id} className={`group relative rounded-lg transition-colors ${session?.id === item.id ? 'bg-vault-elevated text-vault-text border border-vault-accent/30' : 'text-vault-muted hover:bg-vault-elevated/60'}`}>
                {renaming?.id === item.id ? (
                  <input autoFocus value={renaming.text} maxLength={160} aria-label="Chat name"
                    onChange={e => setRenaming({ ...renaming, text: e.target.value })}
                    onKeyDown={e => { if (e.key === 'Enter') renameChat(item.id, renaming.text); if (e.key === 'Escape') setRenaming(null) }}
                    onBlur={() => renameChat(item.id, renaming.text)}
                    className="w-full bg-vault-bg border border-vault-accent/40 rounded-lg px-3 py-2 text-sm text-vault-text" />
                ) : (
                  <button type="button" onClick={() => selectSession(item.id)} onDoubleClick={() => setRenaming({ id: item.id, text: item.title || '' })} className="w-full text-left px-3 py-2.5 pr-14">
                    <span className="block truncate text-sm">{sceneCreation ? item.profile?.name || item.title || 'Scene' : item.title || 'Chat'}</span>
                    {sceneCreation && <span className="block truncate mt-1 text-[10px] font-mono opacity-70">{item.title || 'Untitled'}</span>}
                    {item.matches?.[0] && <span className="block mt-1 text-[10px] text-vault-muted line-clamp-2">“{item.matches[0].text}”</span>}
                  </button>
                )}
                {renaming?.id !== item.id && (confirmDelete === item.id ? (
                  <span className="absolute right-1.5 top-2 flex items-center gap-1 text-[10px] font-mono">
                    <button type="button" onClick={() => removeChat(item.id)} className="px-1.5 py-0.5 rounded bg-red-500/20 text-red-200">Delete</button>
                    <button type="button" aria-label="Keep chat" onClick={() => setConfirmDelete(null)} className="px-1 text-vault-muted"><X size={11} /></button>
                  </span>
                ) : (
                  <span className="absolute right-1.5 top-2 hidden group-hover:flex items-center gap-1">
                    <button type="button" title="Rename" aria-label="Rename chat" onClick={() => setRenaming({ id: item.id, text: item.title || '' })} className="p-1 text-vault-muted hover:text-vault-text"><Pencil size={11} /></button>
                    <button type="button" title="Delete" aria-label="Delete chat" disabled={sending} onClick={() => setConfirmDelete(item.id)} className="p-1 text-vault-muted hover:text-red-300 disabled:opacity-30"><Trash2 size={11} /></button>
                  </span>
                ))}
              </div>
            ))}
            {chatHits && chatHits.length === 0 && <p className="text-xs text-vault-muted p-2">No chats match.</p>}
          </div>
          <div className="mt-auto pt-4 text-[10px] leading-relaxed text-vault-muted">Review recorded or uploaded speech before adding it to your message. Spoken replies use the selected voice model.</div>
        </aside>

        <section className="vault-card flex flex-col min-h-[400px] min-w-0 overflow-hidden xl:min-h-0">
          <div className="flex items-center justify-between px-4 md:px-5 py-3 border-b border-vault-border">
            <div className="min-w-0 flex items-center gap-3">
              {(sceneCreation || roleplay) && <CharacterAvatar name={profile.name || 'Character'} imageUrl={profile.portrait_override_url || profile.image_url} size={40} />}
              <div className="min-w-0"><div className="font-display font-semibold text-vault-text truncate">{sceneCreation || roleplay ? profile.name || title || 'Character chat' : title || 'New chat'}</div><div className="text-[11px] text-vault-muted truncate">{sceneCreation ? profile.description || 'Add sourced context in the character profile.' : `${roleplay ? 'Character roleplay' : 'Direct LLM chat'} · ${model || 'Connect your LLM to start.'}`}</div></div>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              {session && sceneCreation && <>
                <label className="sr-only" htmlFor="converse-active-speaker">Reply as</label>
                <select id="converse-active-speaker" aria-label="Reply as" value={activeSpeakerId} onChange={event => selectSpeaker(event.target.value)} disabled={sending || saving || creating} className="max-w-40 bg-vault-elevated border border-vault-border rounded-lg px-2.5 py-2 text-xs text-vault-text disabled:opacity-50">
                  {characters.map(character => <option key={character.id} value={character.id}>{character.profile.name || (character.id === 'primary' ? 'Primary character' : 'New character')}</option>)}
                </select>
                <button type="button" onClick={addCharacter} disabled={characters.length >= MAX_CHAT_CHARACTERS || creating || saving || sending} title={characters.length >= MAX_CHAT_CHARACTERS ? 'A conversation can have up to 6 characters' : 'Add a character'} aria-label="Add character" className="vault-btn-ghost inline-flex items-center gap-1 px-2.5 py-2 text-xs disabled:opacity-40"><Plus size={13} />Add</button>
              </>}
              {session && <button type="button" onClick={() => setMemoryOpen(true)} title="What the AI remembers in this chat" className="vault-btn-ghost inline-flex items-center gap-1.5 px-3 py-2 text-xs"><Brain size={13} /> Memory</button>}
              {sending && <button type="button" onClick={stopGeneration} className="vault-btn-ghost inline-flex items-center gap-1.5 px-3 py-2 text-xs"><Square size={12} /> Stop</button>}
            </div>
          </div>
          <div ref={messagesViewportRef} onScroll={event => {
            const viewport = event.currentTarget
            followLatestRef.current = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight < 64
          }} className="flex-1 min-h-0 overflow-y-auto p-4 md:p-6 space-y-4" aria-live="polite" aria-label="Conversation messages">
            {!session && <div className="h-full flex flex-col items-center justify-center text-center text-vault-muted"><Bot size={38} className="opacity-30 mb-3" /><p className="text-sm">{sceneCreation ? profile.name ? `Review ${profile.name}’s profile, then create the scene.` : 'Create a scene to begin.' : loading ? 'Opening your chat…' : 'Start a new chat to begin.'}</p>{(profileDirty || !sceneCreation) && <button type="button" onClick={sceneCreation ? createFromProfile : handleNewSession} disabled={creating} className="vault-btn-primary mt-4 inline-flex items-center gap-2 disabled:opacity-50">{creating ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}{creating ? 'Creating…' : sceneCreation ? 'Create scene' : 'New chat'}</button>}</div>}
            {session?.messages_offset > 0 && <div className="flex justify-center"><button type="button" onClick={loadEarlier} disabled={loadingEarlier || sending} className="vault-btn-ghost inline-flex items-center gap-1.5 px-3 py-1.5 text-xs disabled:opacity-40">{loadingEarlier ? <Loader2 size={12} className="animate-spin" /> : null}Load earlier messages ({session.messages_offset} more)</button></div>}
            {session && messages.length === 0 && <div className="h-full min-h-40 flex flex-col items-center justify-center text-center text-vault-muted"><Bot size={40} className="opacity-30 mb-3" /><p className="font-display text-lg text-vault-text">{sceneCreation ? profile.name || 'Your character is ready' : 'Let’s chat'}</p><p className="text-sm mt-1 max-w-sm">{sceneCreation ? 'Connect a model endpoint, then send a message to start.' : 'Connect your LLM, then type a message or record one below. Choose a voice for spoken responses.'}</p></div>}
            {messages.map((message, index) => {
              const characterReply = message.role === 'assistant' && (sceneCreation || roleplay)
              const speakerName = messageSpeakerLabel(message, characters)
              const speakerProfile = message.profile_snapshot || characterForMessage(message, characters)?.profile
              return <div key={`${index}-${message.role}`} className={`flex items-start gap-2 ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                {characterReply && <CharacterAvatar name={speakerName} imageUrl={speakerProfile?.portrait_override_url || speakerProfile?.image_url} size={32} />}
                <div className={`min-w-0 ${characterReply ? 'max-w-[calc(100%-3rem)]' : 'max-w-[88%]'} rounded-2xl px-4 py-3 ${message.role === 'user' ? 'bg-vault-accent/20 border border-vault-accent/25 text-vault-text rounded-br-md' : 'bg-vault-elevated border border-vault-border text-vault-text rounded-bl-md'}`}>
                  <div className="text-[10px] font-mono text-vault-muted mb-1">{message.role === 'user' ? (message.persona_name ? message.persona_name.toUpperCase() : 'YOU') : characterReply ? speakerName.toUpperCase() : 'ASSISTANT'}{message.role === 'assistant' && message.status && message.status !== 'complete' && <span className="ml-2 text-amber-300">{message.status === 'interrupted' ? 'PARTIAL · INTERRUPTED' : message.status.toUpperCase()}</span>}
                    {message.role === 'assistant' && message.content?.trim() && !(sending && index === messages.length - 1) && <span className="float-right -mt-0.5 ml-2"><ReplayButton message={message}
                      onSpeak={message.audio?.length || !voiceForReply(message).problem ? speakReply : null} busy={speaking}
                      speakProblem={voiceForReply(message).problem}
                      disabled={sending || capturing} /></span>}</div>
                  <p className="text-sm leading-relaxed whitespace-pre-wrap break-words">{message.content || (sending && index === messages.length - 1 ? <span className="inline-flex items-center gap-2 text-vault-muted"><Loader2 size={13} className="animate-spin" />Thinking…</span> : '')}</p>
                </div>
                {message.role === 'user' && message.persona_name && <CharacterAvatar name={message.persona_name} imageUrl={message.persona_avatar_url} size={32} />}
              </div>
            })}
          </div>
          <div className="border-t border-vault-border">
            <form onSubmit={send} className="p-3 md:p-4 flex items-end gap-2">
              <textarea aria-label="Message" value={draft} onChange={e => setDraft(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(e) } }} rows={2} maxLength={10000} disabled={!session || sending || saving} placeholder={session ? 'Write a message… (Enter to send, Shift+Enter for a new line)' : 'Create a conversation first'} className="flex-1 resize-y max-h-36 min-h-[48px] bg-vault-elevated border border-vault-border rounded-xl px-3 py-3 text-sm text-vault-text placeholder:text-vault-muted/60 focus:outline-none focus:border-vault-accent disabled:opacity-50" />
              <button type="submit" disabled={!session || sending || saving || speaking || portraitUploading || !connected || !model || !draft.trim()} aria-label="Send message" className="vault-btn-primary h-11 w-11 !p-0 flex items-center justify-center disabled:opacity-40"><Send size={16} /></button>
            </form>
            <ChatAudioInput key={session?.id || 'new-conversation'} sessionKey={session?.id || 'new-conversation'} disabled={!session || sending || saving || speaking || audioPlaying || previewPlaying} onApplyTranscript={applyAudioTranscript} onCaptureChange={handleCaptureChange} />
          </div>
        </section>

        <aside className="vault-card p-4 md:p-5 flex flex-col gap-5 overflow-y-auto min-h-[360px] xl:min-h-0">
          <section className="flex flex-col gap-3">
            <div className="flex items-center justify-between"><span className="vault-label">Chat endpoint</span><span className={`text-[10px] font-mono ${connected ? 'text-emerald-300' : 'text-vault-muted'}`}>{connected ? 'ONLINE' : 'OFFLINE'}</span></div>
            <label className="flex flex-col gap-1"><span className="text-[11px] text-vault-muted">LLM service</span><select value={provider} onChange={e => updateChatProvider(e.target.value)} disabled={sending} className="w-full bg-vault-elevated border border-vault-border rounded-lg px-3 py-2 text-xs text-vault-text disabled:opacity-50">{LLM_PROVIDERS.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
            <label className="flex flex-col gap-1"><span className="text-[11px] text-vault-muted">{provider === 'openai_compatible' ? 'Compatible API base URL' : isNativeCloudProvider(provider) ? `${LLM_PROVIDERS.find(item => item.id === provider)?.label || 'Cloud API'} endpoint` : `${LLM_PROVIDERS.find(item => item.id === provider)?.label || 'Service'} URL`}</span><input value={endpoint} onChange={e => updateChatEndpoint(e.target.value)} placeholder={providerDefaultEndpoint(provider) || 'Enter the compatible API address'} disabled={sending} className="w-full bg-vault-elevated border border-vault-border rounded-lg px-3 py-2 text-xs text-vault-text disabled:opacity-50" /></label>
            <label className="flex flex-col gap-1"><span className="text-[11px] text-vault-muted">{providerRequiresApiKey(provider) ? `${LLM_PROVIDERS.find(item => item.id === provider)?.label || 'Cloud API'} key (required, memory only)` : 'API key (optional, memory only)'}</span><input type="password" autoComplete="off" value={apiKey} onChange={e => updateChatApiKey(e.target.value)} placeholder="Not saved in browser storage" disabled={sending} className="w-full bg-vault-elevated border border-vault-border rounded-lg px-3 py-2 text-xs text-vault-text disabled:opacity-50" /></label>
            <label className="flex flex-col gap-1" title="How much the model can read at once. Match the context length set in LM Studio or your model server. Long chats are fitted into it with memory."><span className="text-[11px] text-vault-muted">Context size (tokens) · long chats are fitted into this</span><input type="number" min={1024} step={1024} value={contextTokens} onChange={e => { const v = Number(e.target.value) || 0; setContextTokens(v); try { localStorage.setItem('vv.chatContextTokens', String(v)) } catch { /* private mode */ } }} disabled={sending} className="w-full bg-vault-elevated border border-vault-border rounded-lg px-3 py-2 text-xs text-vault-text disabled:opacity-50" /></label>
            {isNativeCloudProvider(provider) && <p className="text-[10px] leading-relaxed text-vault-muted">This key is sent only to the {LLM_PROVIDERS.find(item => item.id === provider)?.label} destination shown above. It stays in memory and clears when the destination changes.</p>}
            {isNativeCloudProvider(provider) && <div className="rounded-lg border border-vault-border p-2 space-y-1">
              <label className="flex items-start gap-2 text-[11px] text-vault-text">
                <input type="checkbox" checked={nameOnlyCloudContext} onChange={event => setNameOnlyCloudContext(event.target.checked)} disabled={sending} />
                Use names only for cloud character context
              </label>
              <p className="text-[10px] leading-relaxed text-vault-muted">When enabled, imported profile fields and earlier conversation history are omitted from the request. Character names and the new message or scene context remain. Saved profiles and catalog identities are unchanged.</p>
            </div>}
            <button type="button" onClick={connect} disabled={connecting || sending || !endpoint.trim() || providerRequiresApiKey(provider) && !apiKey.trim()} className="vault-btn-ghost inline-flex items-center justify-center gap-2 w-full px-3 py-2 text-xs disabled:opacity-50">{connecting ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}{connecting ? 'Checking service…' : providerRequiresApiKey(provider) && !apiKey.trim() ? `Enter ${LLM_PROVIDERS.find(item => item.id === provider)?.label} key to load models` : 'Test connection & load models'}</button>
            {isNativeCloudProvider(provider) && <p className="text-[10px] leading-relaxed text-vault-muted">Model loading is user initiated and displays the first 100 results. No cloud request runs until you press Test connection &amp; load models.</p>}
            {models.length > 0 && <label className="flex flex-col gap-1"><span className="text-[11px] text-vault-muted">Chat model</span><select value={model} disabled={sending} onChange={e => chooseModel(e.target.value)} className="w-full bg-vault-elevated border border-vault-border rounded-lg px-3 py-2 text-xs text-vault-text disabled:opacity-50">{models.map(item => <option key={item.id} value={item.id}>{item.id}</option>)}</select></label>}
            <p className="text-[10px] leading-relaxed text-vault-muted">Local services start at their standard addresses. Cloud services use the destination shown above. You can enter a compatible server URL or reverse proxy. API keys remain in memory only and clear when the destination changes.</p>
          </section>

          {!sceneCreation && <>
            <ChatCharacterPicker sessions={[...new Map([...librarySessions, ...sessions].map(item => [item.id, item])).values()]}
              onSelect={assignChatCharacter} onCustom={() => assignChatCharacter({ profile: { ...EMPTY_PROFILE, name: 'Custom character' } })}
              disabled={sending || saving || creating || speaking || capturing || previewPlaying} />
            <button type="button" onClick={selectDirectChat} disabled={sending || saving || creating || speaking || capturing} className="vault-btn-ghost text-xs">Chat directly with the LLM</button>
            <label className="flex flex-col gap-1"><span className="text-[11px] text-vault-muted">Chat title</span><input value={title} onChange={e => updateTitle(e.target.value)} maxLength={120} disabled={sending || saving || creating} className="w-full bg-vault-elevated border border-vault-border rounded-lg px-3 py-2 text-xs text-vault-text" /></label>
          </>}

          {sceneCreation && <><SceneControls scene={scene} characters={characters} onChange={updateScene}
            onRun={() => send(null, true)} onStop={stopGeneration}
            disabled={!session || sending || saving || creating || speaking || capturing || previewPlaying}
            canRun={!!session && connected && !!model} progress={sceneProgress} />
          <p className="text-[10px] leading-relaxed text-vault-muted">Each round follows your selected order and saves attributed replies. Run scene saves these choices and continues from the context and existing history. Your message draft is sent only with Send.</p></>}

          {(sceneCreation || roleplay) && session && <section className="border-t border-vault-border pt-4 flex flex-col gap-2">
            <span className="vault-label">You're playing</span>
            <div className="flex items-center gap-2">
              <CharacterAvatar name={session.user_persona?.name || 'You'} imageUrl={session.user_persona?.avatar_url} size={32} />
              <select aria-label="Your persona" value={session.user_persona?.id || ''} onChange={e => changePersona(e.target.value)} disabled={sending || saving || creating}
                className="flex-1 min-w-0 bg-vault-elevated border border-vault-border rounded-lg px-2 py-1.5 text-xs text-vault-text">
                <option value="">Yourself (no persona)</option>
                {personas.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
                {session.user_persona?.name && !personas.some(item => item.id === session.user_persona.id) && <option value={session.user_persona.id || ''} disabled>{session.user_persona.name} (kept in this chat)</option>}
              </select>
            </div>
            <button type="button" onClick={() => navigate('/cast?tab=personas')} className="self-start text-[11px] text-vault-accent hover:text-vault-glow">Create or edit personas</button>
          </section>}

          {(sceneCreation || roleplay) && <section className="border-t border-vault-border pt-4 flex flex-col gap-3">
            <div className="flex items-center justify-between"><span className="vault-label">{sceneCreation && selectedCharacter?.id === 'primary' ? 'Primary character' : 'Character profile'} · {profile.name || 'Unnamed'}</span><div className="flex items-center gap-2">{selectedCharacter?.id !== 'primary' && <button type="button" onClick={() => removeCharacter(activeSpeakerId)} disabled={saving || sending} title="Remove this character; keep their messages" aria-label="Remove character" className="text-vault-muted hover:text-red-300 disabled:opacity-40"><Trash2 size={14} /></button>}<button type="button" onClick={saveProfile} disabled={!session || saving || sending || portraitUploading} className="text-vault-muted hover:text-vault-text disabled:opacity-40" title="Save character profile">{saving ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />}</button></div></div>
            {sceneCreation && <label className="flex flex-col gap-1"><span className="text-[11px] text-vault-muted">Conversation title</span><input value={title} onChange={e => updateTitle(e.target.value)} maxLength={120} disabled={sending || saving || creating} className="w-full bg-vault-elevated border border-vault-border rounded-lg px-3 py-2 text-xs text-vault-text" /></label>}
            <label className="flex flex-col gap-1"><span className="text-[11px] text-vault-muted">Character name</span><input value={profile.name} onChange={e => updateProfile('name', e.target.value)} maxLength={120} disabled={sending || saving || creating} placeholder="Name" className="w-full bg-vault-elevated border border-vault-border rounded-lg px-3 py-2 text-xs text-vault-text disabled:opacity-60" /></label>
            <CharacterPortraitEditor key={`${session?.id || 'new'}:${activeSpeakerId}`} profile={profile}
              onChange={url => setActiveProfile({ ...profile, portrait_override_url: url })}
              onBusyChange={setPortraitUploading}
              disabled={!session || sending || saving || creating} />
            <ProfileSuggestions key={`suggest:${session?.id || 'new'}:${activeSpeakerId}`} profile={profile} provider={provider} endpoint={endpoint} model={model} apiKey={apiKey} nameOnlyContext={isNativeCloudProvider(provider) && nameOnlyCloudContext}
              connected={connected} voiceContext={resolveSuggestionVoice(profile, ttsModels, ttsEngines)} disabled={sending || saving || creating || portraitUploading}
              onApply={next => { setActiveProfile(next); setNotice('Reviewed suggestion applied to the editor. Use Save profile to keep it.') }} />
            <div className="flex flex-wrap items-center gap-2">
              <select aria-label="Load a character from your Cast" value="" onChange={e => loadFromCast(e.target.value)} disabled={!castMembers.length || sending || saving || creating || castBusy}
                className="flex-1 min-w-0 bg-vault-elevated border border-vault-border rounded-lg px-2 py-1.5 text-xs text-vault-text disabled:opacity-50">
                <option value="">{castMembers.length ? 'Load from your Cast…' : 'Your Cast is empty'}</option>
                {castMembers.map(item => <option key={item.id} value={item.id}>{item.profile?.name || 'Unnamed'}{item.variant ? ` (${item.variant})` : ''}</option>)}
              </select>
              <button type="button" onClick={saveToCast} disabled={sending || saving || creating || castBusy || portraitUploading}
                title="Keep this character (persona, portrait and voice) in your Cast to reuse anywhere" className="vault-btn-ghost inline-flex items-center gap-1.5 !px-2.5 !py-1.5 text-xs disabled:opacity-40">
                {castBusy ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}Save to Cast</button>
            </div>
            {[["description", "Description / sourced context", "Who is this character? Include source notes and uncertainty.", 4000], ["personality", "Personality direction", "How should the model speak and respond?", 4000], ["speaking_style", "Way of speaking", "Vocabulary, rhythm, catchphrases.", 2000], ["background", "Background and lore", "History and key events.", 4000], ["relationships", "Relationships", "Who matters to them.", 2000], ["boundaries", "Never does", "Things they would never say or do.", 1000], ["example_lines", "Example lines", "A few lines in their voice.", 2000], ["source_notes", "Sources & uncertainty", "What is sourced, inferred, or unknown?", 4000], ["voice_description", "Voice description", "Optional voice design description for supported TTS models.", 1000]].map(([key, label, placeholder, max]) => <label key={key} className="flex flex-col gap-1"><span className="text-[11px] text-vault-muted">{label}</span><textarea value={profile[key] || ''} onChange={e => updateProfile(key, e.target.value)} rows={key === 'source_notes' ? 3 : 2} maxLength={max} disabled={sending || saving || creating} placeholder={placeholder} className="w-full resize-y bg-vault-elevated border border-vault-border rounded-lg px-3 py-2 text-xs text-vault-text placeholder:text-vault-muted/60" /></label>)}
            <button type="button" onClick={saveProfile} disabled={!session || saving || sending || portraitUploading} className="vault-btn-ghost inline-flex items-center justify-center gap-2 px-3 py-2 text-xs disabled:opacity-40">{saving ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}{saving ? 'Saving…' : 'Save profile'}</button>
            {!sceneCreation && <p className="text-[10px] text-vault-muted">Catalog context can be incomplete. Save to Cast to reuse this character anywhere; in Cast, Craft with AI builds a sourced persona.</p>}
          </section>}

          <section className="border-t border-vault-border pt-4 flex flex-col gap-3">
            <div className="flex items-center gap-2"><Volume2 size={14} className="text-vault-accent" /><span className="vault-label">{sceneCreation ? 'Spoken reply' : 'Chat voice'}</span></div>
            {!sceneCreation && <label className="flex gap-2 items-start text-xs text-vault-text"><input type="checkbox" checked={autoSpeak} onChange={event => { setAutoSpeak(event.target.checked); setProfileDirty(true); profileEditVersionRef.current += 1 }} disabled={sending || saving || speaking} />Spoken responses</label>}
            <SavedVoicePresets current={currentVoicePreset} catalog={{ engines: ttsEngines, models: ttsModels, references }}
              onApply={applyVoicePreset} applyLabel={sceneCreation || roleplay ? 'Assign to this character' : 'Use for chat'} contextKey={`${session?.id || ''}:${activeSpeakerId}`}
              onPlaybackChange={setPreviewPlaying}
              disabled={sending || saving || creating || speaking || capturing || audioPlaying} />
            {!ttsModels.some(item => USABLE_TTS.has(item.state)) ? <p className="text-[11px] leading-relaxed text-vault-muted">No usable speech model is available. Configure a model on Generate first.</p> : <>
              <label className="flex flex-col gap-1"><span className="text-[11px] text-vault-muted">Voice engine</span><select value={ttsEngineId} disabled={sending || saving || creating} onChange={e => updateVoiceEngine(e.target.value)} className="w-full bg-vault-elevated border border-vault-border rounded-lg px-3 py-2 text-xs text-vault-text"><option value="">Choose a voice engine</option>{ttsEngineId && !ttsEngines.some(item => item.id === ttsEngineId) && <option value={ttsEngineId} disabled>Unavailable saved engine · {ttsEngineId}</option>}{ttsEngines.filter(item => ttsModels.some(m => m.engine_id === item.id && USABLE_TTS.has(m.state))).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
              <label className="flex flex-col gap-1"><span className="text-[11px] text-vault-muted">TTS model</span><select value={ttsModelId} disabled={sending || saving || creating} onChange={e => updateVoiceField('model_id', e.target.value)} className="w-full bg-vault-elevated border border-vault-border rounded-lg px-3 py-2 text-xs text-vault-text"><option value="">Choose a TTS model</option>{ttsModelId && !engineModels.some(item => item.id === ttsModelId) && <option value={ttsModelId} disabled>Unavailable saved model · {ttsModelId}</option>}{engineModels.map(item => <option key={item.id} value={item.id}>{item.family || item.id}</option>)}</select></label>
              {hasSavedSpeechSettings && !ttsModels.some(item => item.id === ttsModelId && USABLE_TTS.has(item.state)) && <p role="status" className="text-[11px] text-amber-200">The saved speech model is unavailable. Choose an available TTS model, then save the voice settings.</p>}
              {!sceneCreation && !roleplay && (canDescribe || capabilities.reference_direction) && <label className="flex flex-col gap-1"><span className="text-[11px] text-vault-muted">Voice description / direction</span><textarea value={profile.voice_description} onChange={e => updateProfile('voice_description', e.target.value)} maxLength={1000} rows={2} disabled={sending || saving || creating} className="w-full bg-vault-elevated border border-vault-border rounded-lg px-3 py-2 text-xs text-vault-text" /></label>}
              {(canDescribe || canReference || hasSavedSpeechSettings) && <label className="flex flex-col gap-1"><span className="text-[11px] text-vault-muted">{sceneCreation || roleplay ? 'Voice input for this character' : 'Voice input'}</span><select value={speechMode} disabled={sending || saving || creating} onChange={e => updateVoiceField('speech_mode', e.target.value)} className="w-full bg-vault-elevated border border-vault-border rounded-lg px-3 py-2 text-xs text-vault-text">{speechMode === 'description' && !canDescribe && canReference && <option value="description" disabled>Unavailable saved description mode</option>}{speechMode === 'reference' && !canReference && <option value="reference" disabled>Unavailable saved reference mode</option>}{(canDescribe || !canReference) && <option value="description">{canDescribe ? 'Description' : 'Default voice'}</option>}{canReference && <option value="reference">Reference clip</option>}</select>{((speechMode === 'reference' && !canReference) || (speechMode === 'description' && !canDescribe && canReference)) && <span className="text-[11px] text-amber-200">This saved voice input mode is unavailable for the selected model. Choose a supported mode or model, then save the profile.</span>}</label>}
              {canReference && speechMode === 'reference' && <label className="flex flex-col gap-1"><span className="text-[11px] text-vault-muted">{sceneCreation || roleplay ? 'Reference clip for this character' : 'Reference clip'}</span><select value={referenceId} disabled={sending || saving || creating} onChange={e => updateVoiceField('reference_id', e.target.value)} className="w-full bg-vault-elevated border border-vault-border rounded-lg px-3 py-2 text-xs text-vault-text"><option value="">Choose a saved clip</option>{referenceId && !references.some(ref => ref.reference_id === referenceId) && <option value={referenceId} disabled>Unavailable saved clip · {referenceId}</option>}{references.map(ref => <option key={ref.reference_id} value={ref.reference_id}>{ref.name || ref.reference_id}</option>)}</select>{hasSavedSpeechSettings && referenceId && !references.some(ref => ref.reference_id === referenceId) && <span className="text-[11px] text-amber-200">The saved reference clip is unavailable. Choose another clip, then save the profile.</span>}</label>}
              {canReference && speechMode === 'reference' && !referenceId && <p role="status" className="text-[11px] text-amber-200">Choose a saved reference clip before generating speech.</p>}
              {canReference && speechMode === 'reference' && capabilities.reference_transcript && <label className="flex flex-col gap-1"><span className="text-[11px] text-vault-muted">Reference transcript (optional)</span><textarea value={profile.voice_settings?.reference_text || ''} onChange={e => updateVoiceField('reference_text', e.target.value)} maxLength={2000} rows={2} disabled={sending || saving || creating} className="w-full bg-vault-elevated border border-vault-border rounded-lg px-3 py-2 text-xs text-vault-text" /></label>}
              {capabilities.multilingual && <label className="flex flex-col gap-1"><span className="text-[11px] text-vault-muted">Voice language (optional)</span><input value={profile.voice_settings?.language || ''} onChange={e => updateVoiceField('language', e.target.value)} maxLength={40} disabled={sending || saving || creating} className="w-full bg-vault-elevated border border-vault-border rounded-lg px-3 py-2 text-xs text-vault-text" /></label>}
              {(capabilities.supported_generation_controls || []).length > 0 && <details className="text-[11px] text-vault-muted"><summary className="cursor-pointer">Voice generation settings</summary><div className="grid grid-cols-2 gap-2 mt-2">{[
                ['temperature', 0.1, 2, 0.05], ['top_p', 0.1, 1, 0.05], ['repetition_penalty', 1, 2, 0.05], ['max_new_tokens', 100, 16000, 100], ['seed', 0, 4294967295, 1],
              ].filter(([key]) => capabilities.supported_generation_controls.includes(key)).map(([key, min, max, step]) => <label key={key} className="flex flex-col gap-1"><span>{key}</span><input aria-label={`Voice ${key}`} type="number" min={min} max={max} step={step} value={profile.voice_settings?.generation?.[key] ?? DEFAULT_GENERATION[key]} onChange={e => updateVoiceField('generation', { ...DEFAULT_GENERATION, ...profile.voice_settings?.generation, [key]: Number(e.target.value) })} disabled={sending || saving || creating} className="w-full bg-vault-elevated border border-vault-border rounded px-2 py-1 text-xs text-vault-text" /></label>)}</div></details>}
              <label className="flex flex-col gap-1"><span className="text-[11px] text-vault-muted">Pace between sentences</span>
                <select aria-label="Pace between sentences" value={profile.voice_settings?.pacing || 'natural'} disabled={sending || saving || creating}
                  onChange={e => updateVoiceField('pacing', e.target.value === 'natural' ? null : e.target.value)}
                  className="w-full bg-vault-elevated border border-vault-border rounded-lg px-3 py-2 text-xs text-vault-text">
                  {[['natural', 'Natural (as spoken)'], ['relaxed', 'Relaxed'], ['dramatic', 'Dramatic']].map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select></label>
              <DeliveryControls capabilities={capabilities} referenceMode={speechMode === 'reference'} value={delivery}
                disabled={sending || saving || creating} onChange={updater => {
                  const next = typeof updater === 'function' ? updater(delivery) : updater
                  setDelivery(next)
                  updateSavedVoiceSettings({ ...profile.voice_settings, model_id: ttsModelId || null,
                    reference_id: referenceId || null, speech_mode: speechMode, delivery: next })
                }} />
              <label className="flex gap-2 items-start text-[11px] text-vault-muted"><input type="checkbox" checked={proposeDelivery} onChange={event => setProposeDelivery(event.target.checked)} disabled={sending || saving || speaking} />Let the chat model suggest delivery</label>
              {sceneCreation && <label className="flex gap-2 items-start text-[11px] text-vault-muted"><input type="checkbox" checked={autoSpeak} onChange={event => setAutoSpeak(event.target.checked)} disabled={sending || saving || speaking} />Speak while replying</label>}
              <p className="text-[10px] leading-relaxed text-vault-muted">Spoken replies use the saved speaker’s voice across bounded sentences, up to 32 segments / 8,000 characters. Unsupported suggestions are omitted with a notice. Complete-output engines finish each sentence before its audio arrives.</p>
              {currentAssistant && <p role={replySpeechProblem ? 'status' : undefined} className={`text-[10px] leading-relaxed ${replySpeechProblem ? 'text-amber-200' : 'text-vault-muted'}`}>{replySpeechProblem || (sceneCreation || roleplay ? `Speak latest reply uses ${replyCharacter?.profile.name || 'the character'}’s saved voice.` : 'Replay uses this reply’s saved voice settings.')}</p>}
              <button type="button" onClick={speaking ? stopSpeech : () => speakLastReply()} disabled={!speaking && (!currentAssistant || sending || capturing || !!replySpeechProblem || !replyModel)} className="vault-btn-ghost inline-flex items-center gap-2 justify-center px-3 py-2 text-xs disabled:opacity-40">{speaking ? <Square size={12} /> : <Volume2 size={13} />}{speaking ? 'Stop speech request' : 'Speak latest reply'}</button>
              <p className="text-[10px] leading-relaxed text-vault-muted">Marked actions such as *smiles* and bracketed stage directions are skipped. Playback, mute, and replay use the audio controls.</p>
              {audioResult && <div className="flex flex-col gap-2"><audio ref={audioRef} key={audioResult.id || audioResult.url} controls src={audioUrl(audioResult.url)} className="w-full" onPlay={() => setAudioPlaying(true)} onPause={() => setAudioPlaying(false)} onEnded={() => setAudioPlaying(false)} /><div className="flex items-center justify-between"><span className="text-[10px] text-vault-muted">Replay with the audio controls.</span><button type="button" onClick={() => { stopAudio(); setAudioResult(null) }} className="inline-flex items-center gap-1 text-[10px] text-vault-muted hover:text-vault-text"><VolumeX size={12} /> Stop / clear</button></div></div>}
              {speechTiming?.firstAudioMs != null && <p role="status" className="text-[10px] font-mono text-vault-muted">First audio queued: {(speechTiming.firstAudioMs / 1000).toFixed(2)} s · {speechSegments.length} saved segments</p>}
              {speechSegments.length > 0 && <details className="text-[11px] text-vault-muted"><summary className="cursor-pointer">Replay speech segments ({speechSegments.length})</summary><div className="mt-2 space-y-2">{speechSegments.map((segment, index) => <div key={segment.id || segment.url}><p>{segment.speaker_name ? `${segment.speaker_name} · ` : ''}Segment {index + 1} · {(segment.segment_ms / 1000).toFixed(2)} s to generate and queue</p><audio controls src={audioUrl(segment.url)} className="w-full" ref={element => { if (element) segmentAudioRefs.current.set(index, element); else segmentAudioRefs.current.delete(index) }} onPlay={event => {
                for (const audio of segmentAudioRefs.current.values()) if (audio !== event.currentTarget) audio.pause()
                setAudioPlaying(true)
              }} onPause={() => setAudioPlaying(false)} onEnded={() => setAudioPlaying(false)} /></div>)}</div></details>}
            </>}
          </section>
          {!sceneCreation && <button type="button" onClick={saveProfile} disabled={!session || saving || sending || portraitUploading} className="vault-btn-ghost text-xs">Save chat voice & preferences</button>}
          {(sceneCreation || roleplay) && <p className="text-[10px] leading-relaxed text-vault-muted">Character accuracy depends on the context you provide and the model. No web research is performed. Chat memory keeps long conversations consistent; see Memory.</p>}
        </aside>
      </div>
      {memoryOpen && session && <MemoryPanel sessionId={session.id} lastInfo={memoryInfo} onClose={() => setMemoryOpen(false)} />}
    </div>
  )
}
