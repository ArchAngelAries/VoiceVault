/**
 * components/music/Waveform.jsx — Song waveform with section bands, playhead and a
 * draggable selection. Click to seek; drag to select a range; drag the selection edges
 * to adjust it; double-click a section band to select that section.
 */
import { themeColor } from '@/data/themes'
import React, { useEffect, useRef, useState } from 'react'

const SECTION_COLORS = ['#7c5cbf33', '#bf5c9b33', '#5c9bbf33', '#5cbf8a33', '#bfa35c33']
const fmt = (t) => `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, '0')}`

export default function Waveform({ peaks, duration, time, selection, onSeek, onSelect, sections = [], markers = [], height = 140 }) {
  const canvasRef = useRef(null)
  const wrapRef = useRef(null)
  const [width, setWidth] = useState(800)
  const drag = useRef(null)

  useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const ro = new ResizeObserver(([entry]) => setWidth(Math.max(200, Math.floor(entry.contentRect.width))))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  useEffect(() => {
    const c = canvasRef.current
    if (!c) return
    const dpr = window.devicePixelRatio || 1
    c.width = width * dpr
    c.height = height * dpr
    const g = c.getContext('2d')
    g.setTransform(dpr, 0, 0, dpr, 0, 0)
    g.clearRect(0, 0, width, height)
    const x = (t) => (duration ? (t / duration) * width : 0)
    sections.forEach((s, i) => {
      g.fillStyle = SECTION_COLORS[i % SECTION_COLORS.length]
      g.fillRect(x(s.start), 0, Math.max(1, x(s.end) - x(s.start)), 16)
      g.fillStyle = themeColor('text')
      g.font = '10px "JetBrains Mono", monospace'
      g.fillText(s.name, x(s.start) + 4, 11)
    })
    if (selection) {
      g.fillStyle = 'rgba(157, 123, 232, 0.22)'
      g.fillRect(x(selection[0]), 16, x(selection[1]) - x(selection[0]), height - 16)
      g.fillStyle = themeColor('glow')
      g.fillRect(x(selection[0]) - 1, 16, 2, height - 16)
      g.fillRect(x(selection[1]) - 1, 16, 2, height - 16)
    }
    if (peaks?.length) {
      const mid = 16 + (height - 16) / 2
      const amp = (height - 22) / 2
      const step = width / peaks.length
      g.fillStyle = themeColor('muted')
      peaks.forEach(([lo, hi], i) => {
        const t = (i / peaks.length) * duration
        const inSel = selection && t >= selection[0] && t <= selection[1]
        g.fillStyle = inSel ? themeColor('text') : t <= time ? themeColor('accent2') : themeColor('muted')
        g.fillRect(i * step, mid - hi * amp, Math.max(1, step - 0.3), Math.max(1, (hi - lo) * amp))
      })
    }
    markers.forEach(m => {
      g.fillStyle = '#5cbf8a'
      g.fillRect(x(m) - 0.5, 16, 1, height - 16)
    })
    g.fillStyle = themeColor('text')
    g.fillRect(x(time) - 1, 0, 2, height)
  }, [peaks, duration, time, selection, sections, markers, width, height])

  const toTime = (e) => {
    const r = canvasRef.current.getBoundingClientRect()
    return Math.min(duration, Math.max(0, ((e.clientX - r.left) / r.width) * duration))
  }
  const edgeAt = (t) => {
    if (!selection || !duration) return null
    const tol = (6 / width) * duration
    if (Math.abs(t - selection[0]) < tol) return 0
    if (Math.abs(t - selection[1]) < tol) return 1
    return null
  }
  const down = (e) => {
    if (!duration) return
    const t = toTime(e)
    const edge = edgeAt(t)
    drag.current = { start: t, edge, moved: false }
    e.currentTarget.setPointerCapture(e.pointerId)
  }
  const move = (e) => {
    if (!drag.current) return
    const t = toTime(e)
    const d = drag.current
    if (Math.abs(t - d.start) > duration / width * 2) d.moved = true
    if (!d.moved) return
    if (d.edge !== null && selection) {
      const next = [...selection]
      next[d.edge] = t
      onSelect?.([Math.min(...next), Math.max(...next)])
    } else {
      onSelect?.([Math.min(d.start, t), Math.max(d.start, t)])
    }
  }
  const up = (e) => {
    const d = drag.current
    drag.current = null
    if (d && !d.moved) {
      const t = toTime(e)
      if (e.clientY - canvasRef.current.getBoundingClientRect().top < 16) {
        const sec = sections.find(s => t >= s.start && t < s.end)
        if (sec) { onSelect?.([sec.start, sec.end]); return }
      }
      onSeek?.(t)
    }
  }

  return (
    <div ref={wrapRef} className="w-full select-none">
      <canvas ref={canvasRef} style={{ width: '100%', height }} className="rounded-lg bg-vault-elevated cursor-crosshair"
        onPointerDown={down} onPointerMove={move} onPointerUp={up}
        role="slider" aria-label="Song position and selection" aria-valuemin={0} aria-valuemax={duration || 0}
        aria-valuenow={time || 0} tabIndex={0}
        onKeyDown={e => {
          if (e.key === 'ArrowRight') onSeek?.(Math.min(duration, time + 5))
          if (e.key === 'ArrowLeft') onSeek?.(Math.max(0, time - 5))
          if (e.key === 'Escape') onSelect?.(null)
        }} />
      <div className="flex justify-between font-mono text-[10px] text-vault-muted mt-1">
        <span>{fmt(time || 0)}</span>
        {selection && <span className="text-vault-glow">selection {fmt(selection[0])} – {fmt(selection[1])} ({(selection[1] - selection[0]).toFixed(1)} s)</span>}
        <span>{fmt(duration || 0)}</span>
      </div>
    </div>
  )
}
