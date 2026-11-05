import React, { useState } from 'react'
import { imageFileUrl } from '@/api/tiers'

function initialsFor(name) {
  const parts = String(name || '').trim().split(/\s+/).filter(Boolean)
  if (!parts.length) return '?'
  if (parts.length === 1) return parts[0].slice(0, 1).toLocaleUpperCase()
  return `${parts[0].slice(0, 1)}${parts.at(-1).slice(0, 1)}`.toLocaleUpperCase()
}

export default function CharacterAvatar({ name = '', imageUrl = '', size = 40, alt }) {
  const [brokenUrl, setBrokenUrl] = useState('')
  const normalizedUrl = imageFileUrl(imageUrl)
  const showImage = !!normalizedUrl && brokenUrl !== normalizedUrl
  const dimension = Number.isFinite(Number(size)) ? Math.max(16, Number(size)) : 40

  return <div role="img" aria-label={alt || `${name || 'Character'} portrait`}
    className="inline-flex shrink-0 items-center justify-center overflow-hidden rounded-full border border-vault-border bg-vault-elevated text-vault-accent"
    style={{ width: dimension, height: dimension, fontSize: Math.max(10, Math.round(dimension * 0.38)) }}>
    {showImage
      ? <img src={normalizedUrl} alt="" onError={() => setBrokenUrl(normalizedUrl)} className="h-full w-full object-cover" />
      : <span aria-hidden="true" className="font-semibold leading-none">{initialsFor(name)}</span>}
  </div>
}
