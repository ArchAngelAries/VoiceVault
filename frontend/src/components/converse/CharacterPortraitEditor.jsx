import React, { useEffect, useRef, useState } from 'react'
import { ImagePlus, Loader2, RotateCcw } from 'lucide-react'
import { importTierImages } from '@/api/tiers'
import CharacterAvatar from './CharacterAvatar'

export const MAX_CHARACTER_PORTRAIT_BYTES = 10 * 1024 * 1024
const IMAGE_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp', 'image/gif'])
const IMAGE_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'webp', 'gif'])

function supportedImage(file) {
  if (file.type) return IMAGE_TYPES.has(file.type.toLowerCase())
  const extension = file.name?.split('.').pop()?.toLowerCase()
  return IMAGE_EXTENSIONS.has(extension)
}

function errorMessage(error) {
  const detail = error?.response?.data?.detail
  return typeof detail === 'string' ? detail : error?.message || 'Could not upload this portrait.'
}

export default function CharacterPortraitEditor({ profile, onChange, disabled = false, sessionKey, onBusyChange }) {
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const inputRef = useRef(null)
  const aliveRef = useRef(true)
  const attemptRef = useRef(0)
  const sessionKeyRef = useRef(sessionKey)
  const onChangeRef = useRef(onChange)
  const onBusyChangeRef = useRef(onBusyChange)
  const busyRef = useRef(false)
  sessionKeyRef.current = sessionKey
  onChangeRef.current = onChange
  onBusyChangeRef.current = onBusyChange

  const setBusy = value => {
    if (busyRef.current === value) return
    busyRef.current = value
    onBusyChangeRef.current?.(value)
  }

  useEffect(() => {
    aliveRef.current = true
    return () => {
      aliveRef.current = false
      attemptRef.current += 1
      setBusy(false)
    }
  }, [])

  const previousSessionRef = useRef(sessionKey)
  useEffect(() => {
    if (previousSessionRef.current === sessionKey) return
    previousSessionRef.current = sessionKey
    attemptRef.current += 1
    setUploading(false)
    setError('')
    setBusy(false)
  }, [sessionKey])

  const upload = async event => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    if (!supportedImage(file)) {
      setError('Choose a PNG, JPEG, WebP, or GIF image.')
      return
    }
    if (file.size > MAX_CHARACTER_PORTRAIT_BYTES) {
      setError('Choose an image smaller than 10 MB.')
      return
    }

    const attempt = ++attemptRef.current
    const sessionAtStart = sessionKeyRef.current
    setError('')
    setUploading(true)
    setBusy(true)
    try {
      const result = await importTierImages([file])
      if (!aliveRef.current || attempt !== attemptRef.current || sessionAtStart !== sessionKeyRef.current) return
      const url = result?.imported?.[0]?.url
      if (typeof url !== 'string' || !url) throw new Error(result?.failed?.[0]?.reason || 'The image library did not return a portrait URL.')
      onChangeRef.current?.(url)
    } catch (cause) {
      if (!aliveRef.current || attempt !== attemptRef.current || sessionAtStart !== sessionKeyRef.current) return
      setError(errorMessage(cause))
    } finally {
      if (aliveRef.current && attempt === attemptRef.current && sessionAtStart === sessionKeyRef.current) {
        setUploading(false)
        setBusy(false)
      }
    }
  }

  const overrideUrl = typeof profile?.portrait_override_url === 'string' ? profile.portrait_override_url : ''
  const catalogUrl = typeof profile?.image_url === 'string' ? profile.image_url : ''
  const displayUrl = overrideUrl || catalogUrl
  const name = typeof profile?.name === 'string' ? profile.name : ''

  return <div className="flex flex-col gap-2 rounded-lg border border-vault-border/70 p-3">
    <div className="flex items-center gap-3">
      <CharacterAvatar name={name} imageUrl={displayUrl} size={48} />
      <div className="min-w-0 flex-1">
        <p className="text-xs font-medium text-vault-text">Character portrait</p>
        <p className="text-[11px] text-vault-muted">{overrideUrl ? 'Custom portrait' : catalogUrl ? 'Catalog portrait' : 'Initials shown until a portrait is added'}</p>
      </div>
      <input ref={inputRef} type="file" accept="image/png,image/jpeg,image/webp,image/gif" onChange={upload} className="hidden" aria-label="Upload a character portrait" />
      <button type="button" aria-label="Upload portrait" disabled={disabled || uploading} onClick={() => inputRef.current?.click()}
        className="vault-btn-ghost inline-flex shrink-0 items-center gap-1.5 px-2.5 py-2 text-xs disabled:opacity-40">
        {uploading ? <Loader2 size={13} className="animate-spin" /> : <ImagePlus size={13} />}
        {uploading ? 'Uploading…' : 'Upload'}
      </button>
    </div>
    {overrideUrl && <button type="button" disabled={disabled || uploading} onClick={() => { setError(''); onChangeRef.current?.(null) }}
      className="vault-btn-ghost inline-flex self-start items-center gap-1.5 px-2.5 py-1.5 text-xs disabled:opacity-40"><RotateCcw size={12} />{catalogUrl ? 'Use catalog portrait' : 'Remove uploaded portrait'}</button>}
    <p className="text-[10px] leading-relaxed text-vault-muted">Portraits are saved in the shared image library. Saving the character profile keeps this portrait assigned to the character.</p>
    {error && <p role="alert" className="text-xs text-red-300">{error}</p>}
  </div>
}
