import React, { useEffect, useRef, useState } from 'react'
import { ImagePlus, Images, Loader2, X } from 'lucide-react'
import api from '@/api/client'
import { importTierImages, imageFileUrl } from '@/api/tiers'
import CharacterAvatar from '@/components/converse/CharacterAvatar'

const MAX_BYTES = 10 * 1024 * 1024
const TYPES = new Set(['image/png', 'image/jpeg', 'image/webp', 'image/gif'])

/**
 * A portrait or avatar: upload a new image or pick one already in the image library
 * (Collections → images, other portraits). Stores the app-relative path so it survives port changes.
 */
export default function ImageField({ name, value, fallback = '', onChange, extraChoices = [], disabled = false, label = 'Portrait' }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [choosing, setChoosing] = useState(false)
  const [images, setImages] = useState(null)
  const inputRef = useRef(null)
  const alive = useRef(true)
  useEffect(() => () => { alive.current = false }, [])

  const upload = async event => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    if (file.type && !TYPES.has(file.type.toLowerCase())) { setError('Choose a PNG, JPEG, WebP or GIF image.'); return }
    if (file.size > MAX_BYTES) { setError('Choose an image smaller than 10 MB.'); return }
    setBusy(true); setError('')
    try {
      const result = await importTierImages([file])
      const url = result?.imported?.[0]?.url
      if (!url) throw new Error(result?.failed?.[0]?.reason || 'The image library did not return an image.')
      if (alive.current) onChange(url)
    } catch (cause) {
      if (alive.current) setError(cause?.response?.data?.detail || cause?.message || 'Could not upload this image.')
    } finally { if (alive.current) setBusy(false) }
  }

  const openChooser = async () => {
    setChoosing(open => !open)
    if (images) return
    try {
      const response = await api.get('/tiers/images')
      if (alive.current) setImages(response.data.items || [])
    } catch { if (alive.current) setImages([]) }
  }

  const seen = new Set()
  const choices = [...extraChoices, ...(images || []).map(image => ({ url: image.url, label: image.label || '' }))]
    .filter(choice => choice?.url && !seen.has(choice.url) && seen.add(choice.url))

  return <div className="flex flex-col gap-2 rounded-lg border border-vault-border/70 p-3">
    <div className="flex items-center gap-3">
      <CharacterAvatar name={name} imageUrl={value || fallback} size={56} />
      <div className="flex flex-col gap-1.5 min-w-0">
        <span className="text-[11px] text-vault-muted">{label}</span>
        <div className="flex flex-wrap gap-1.5">
          <input ref={inputRef} type="file" accept="image/png,image/jpeg,image/webp,image/gif" onChange={upload} className="hidden" aria-label={`Upload ${label.toLowerCase()}`} />
          <button type="button" disabled={disabled || busy} onClick={() => inputRef.current?.click()} className="vault-btn-ghost inline-flex items-center gap-1.5 !px-2.5 !py-1.5 text-xs disabled:opacity-40">
            {busy ? <Loader2 size={13} className="animate-spin" /> : <ImagePlus size={13} />}{busy ? 'Uploading…' : 'Upload'}</button>
          <button type="button" disabled={disabled} onClick={openChooser} aria-expanded={choosing} className="vault-btn-ghost inline-flex items-center gap-1.5 !px-2.5 !py-1.5 text-xs disabled:opacity-40"><Images size={13} />Choose saved</button>
          {value && <button type="button" disabled={disabled} onClick={() => onChange(null)} className="vault-btn-ghost inline-flex items-center gap-1.5 !px-2.5 !py-1.5 text-xs disabled:opacity-40"><X size={13} />{fallback ? 'Use catalog image' : 'Remove'}</button>}
        </div>
      </div>
    </div>
    {choosing && <div className="flex flex-wrap gap-2 max-h-40 overflow-y-auto pt-1" role="listbox" aria-label="Saved images">
      {images === null && <Loader2 size={14} className="animate-spin text-vault-muted" />}
      {images !== null && !choices.length && <p className="text-[11px] text-vault-muted">No saved images yet. Upload one, or import images in Collections → Tier lists.</p>}
      {choices.map(choice => <button key={choice.url} type="button" role="option" aria-selected={choice.url === value} title={choice.label || 'Saved image'}
        onClick={() => { onChange(choice.url); setChoosing(false) }}
        className={`h-12 w-12 overflow-hidden rounded-full border ${choice.url === value ? 'border-vault-accent' : 'border-vault-border hover:border-vault-accent'}`}>
        <img src={imageFileUrl(choice.url)} alt="" className="h-full w-full object-cover" /></button>)}
    </div>}
    {error && <p role="alert" className="text-[11px] text-red-300">{error}</p>}
  </div>
}
