/**
 * components/tiers/ExportBoard.jsx — The board laid out for export. It is drawn off-screen at a fixed design
 * width (EXPORT_DESIGN_WIDTH), then scaled to the size the user picked. Everything is styled inline so the
 * picture does not depend on the page's CSS.
 */
import React from 'react'
import { textOn } from '@/data/tierColors'
import { exportImageSrc } from '@/components/tiers/exportImages'

export const EXPORT_DESIGN_WIDTH = 1600

export const EXPORT_WIDTHS = [
  { value: 1600, label: 'Small · 1600 px wide' },
  { value: 3200, label: 'Large · 3200 px wide' },
  { value: 4800, label: 'Print · 4800 px wide' },
]
const PAD = 40
const LABEL_WIDTH = 220
const TILE_W = 200
const TILE_H = 280

function ExportTile({ tile }) {
  const src = exportImageSrc(tile.cover_url)
  return (
    <div style={{ width: TILE_W, display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ width: TILE_W, height: TILE_H, borderRadius: 10, overflow: 'hidden', background: '#1a1a26',
        border: '1px solid rgba(255,255,255,0.12)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        {src ? (
          <img src={src} crossOrigin="anonymous" alt="" style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }} />
        ) : (
          <span style={{ padding: 12, textAlign: 'center', fontSize: 20, color: '#8a85a0' }}>{tile.title}</span>
        )}
      </div>
      {/* Reserve three lines so longer titles stay inside the tier row. */}
      <div style={{ fontSize: 22, lineHeight: 1.22, color: '#ece8f5', height: 82, wordBreak: 'break-word',
        display: '-webkit-box', WebkitBoxOrient: 'vertical', WebkitLineClamp: 3, overflow: 'hidden' }}>
        {tile.title || ''}
      </div>
    </div>
  )
}

export default function ExportBoard({ title, subtitle, rows }) {
  return (
    <div style={{ width: EXPORT_DESIGN_WIDTH, boxSizing: 'border-box', padding: PAD, background: '#0c0c12',
      color: '#ece8f5', fontFamily: '"DM Sans", Arial, sans-serif' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 24, marginBottom: 28 }}>
        <div style={{ fontFamily: '"Syne", Arial, sans-serif', fontWeight: 800, fontSize: 58, lineHeight: 1.05 }}>{title}</div>
        {subtitle && <div style={{ fontSize: 24, color: '#8a85a0' }}>{subtitle}</div>}
      </div>
      {rows.map((row, index) => (
        <div key={index} style={{ display: 'flex', minHeight: 232, marginBottom: 14, border: '1px solid #2a2a3d',
          borderRadius: 14, overflow: 'hidden', background: '#12121a' }}>
          <div style={{ width: LABEL_WIDTH, flex: `0 0 ${LABEL_WIDTH}px`, background: row.color, color: textOn(row.color),
            display: 'flex', alignItems: 'center', justifyContent: 'center', textAlign: 'center', padding: 14,
            fontFamily: '"Syne", Arial, sans-serif', fontWeight: 800, fontSize: 46, lineHeight: 1.05, wordBreak: 'break-word' }}>
            {row.label}
          </div>
          <div style={{ flex: 1, display: 'flex', flexWrap: 'wrap', alignContent: 'flex-start', gap: 16, padding: 16 }}>
            {row.items.map(tile => <ExportTile key={tile.id} tile={tile} />)}
          </div>
        </div>
      ))}
    </div>
  )
}
