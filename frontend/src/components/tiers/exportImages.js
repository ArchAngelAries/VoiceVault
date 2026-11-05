/**
 * components/tiers/exportImages.js — Where an export reads a tile's cover from.
 * Imported images are served by the backend. AniList, TMDB and IGDB covers go through the cover proxy,
 * because those hosts do not allow the cross-origin reads a canvas needs. On-screen tiles keep the
 * original links.
 */
import { BACKEND_URL } from '@/api/client'

export function exportImageSrc(url) {
  if (!url) return null
  // Electron's file:// origin cannot resolve backend-relative paths on its own.
  if (url.startsWith(`${BACKEND_URL}/api/`)) return url
  if (url.startsWith('/api/')) return `${BACKEND_URL}${url}`
  if (url.startsWith('api/')) return `${BACKEND_URL}/${url}`
  if (url.startsWith('https://')) return `${BACKEND_URL}/api/tiers/image-proxy?url=${encodeURIComponent(url)}`
  return null
}
