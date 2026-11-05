/**
 * components/harvest/Waveform.jsx — Source waveform with draggable start/end handles.
 *
 * The audio file is fetched once and decoded at a low sample rate (peaks only need a rough
 * shape), then cached per URL. Click anywhere on the wave to listen from that point; drag the
 * handles (or focus one and use the arrow keys: ±0.1 s, Shift ±1 s) to choose the part.
 */
import { themeColor } from '@/data/themes'
import React, { useCallback, useEffect, useRef, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { clampSelection, clock, computePeaks, nudge, selectionStatus, snap, timeAt, xAt } from './harvestMath'

const BUCKETS = 1200
const MAX_BYTES = 120 * 1024 * 1024
const DECODE_RATE = 8000
const peakCache = new Map()   // url -> Promise<{ min, max, duration }>

function loadPeaks(url) {
  if (peakCache.has(url)) return peakCache.get(url)
  const job = (async () => {
    const response = await fetch(url)
    if (!response.ok) throw new Error(`Could not load the audio (${response.status}).`)
    const size = Number(response.headers.get('content-length') || 0)
    if (size > MAX_BYTES) throw new Error('This source is too large to draw. Use the number fields below.')
    const data = await response.arrayBuffer()
    const Offline = window.OfflineAudioContext || window.webkitOfflineAudioContext
    if (!Offline) throw new Error('This browser cannot draw waveforms.')
    // Decoding through a low-rate offline context resamples on the fly and keeps memory small.
    const context = new Offline(1, 1, DECODE_RATE)
    const buffer = await context.decodeAudioData(data)
    const channels = []
    for (let i = 0; i < Math.min(2, buffer.numberOfChannels); i += 1) channels.push(buffer.getChannelData(i))
    return { ...computePeaks(channels, BUCKETS), duration: buffer.duration }
  })()
  peakCache.set(url, job)
  job.catch(() => peakCache.delete(url))
  return job
}

export default function Waveform({ url, duration, start, end, onChange, onSeek, playhead = null }) {
  const wrapRef = useRef(null)
  const canvasRef = useRef(null)
  const dragRef = useRef(null)
  const [peaks, setPeaks] = useState(null)
  const [state, setState] = useState('loading')
  const [message, setMessage] = useState('')
  const [width, setWidth] = useState(0)
  const total = Math.max(0.1, Number(duration) || peaks?.duration || 0.1)
  const s = Number.isFinite(Number(start)) ? Number(start) : 0
  const e = Number.isFinite(Number(end)) ? Number(end) : 0
  const status = selectionStatus(e - s)
  const bad = status === 'short' || status === 'long' || status === 'empty'

  useEffect(() => {
    let alive = true
    setPeaks(null); setState('loading'); setMessage('')
    if (!url) { setState('error'); setMessage('No audio for this source.'); return undefined }
    loadPeaks(url).then(result => { if (alive) { setPeaks(result); setState('ready') } })
      .catch(err => { if (alive) { setState('error'); setMessage(err?.message || 'Could not draw the waveform.') } })
    return () => { alive = false }
  }, [url])

  useEffect(() => {
    const el = wrapRef.current
    if (!el) return undefined
    const update = () => setWidth(el.clientWidth)
    update()
    if (typeof ResizeObserver === 'undefined') return undefined
    const observer = new ResizeObserver(update)
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  // Draw the wave: bars inside the selection are bright, outside are dim.
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !width) return
    const height = 96
    const ratio = window.devicePixelRatio || 1
    canvas.width = Math.round(width * ratio)
    canvas.height = Math.round(height * ratio)
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0)
    ctx.clearRect(0, 0, width, height)
    const mid = height / 2
    if (!peaks) {
      ctx.fillStyle = 'rgba(148,163,184,0.25)'
      ctx.fillRect(0, mid - 0.5, width, 1)
      return
    }
    const inX0 = xAt(s, width, total)
    const inX1 = xAt(e, width, total)
    const count = peaks.max.length
    let top = 0
    for (let i = 0; i < count; i += 1) top = Math.max(top, peaks.max[i], -peaks.min[i])
    const gain = top > 0 ? (mid - 4) / top : 1
    for (let x = 0; x < width; x += 1) {
      const from = Math.floor((x / width) * count)
      const to = Math.max(from + 1, Math.floor(((x + 1) / width) * count))
      let lo = 0
      let hi = 0
      for (let i = from; i < to && i < count; i += 1) { if (peaks.min[i] < lo) lo = peaks.min[i]; if (peaks.max[i] > hi) hi = peaks.max[i] }
      const inside = x >= inX0 && x <= inX1
      ctx.fillStyle = inside ? themeColor('glow') : themeColor('muted', 0.45)
      const y0 = mid - hi * gain
      const y1 = mid - lo * gain
      ctx.fillRect(x, y0, 1, Math.max(1, y1 - y0))
    }
  }, [peaks, width, s, e, total])

  const toTime = clientX => {
    const rect = wrapRef.current?.getBoundingClientRect()
    return rect ? snap(timeAt(clientX - rect.left, rect.width, total)) : 0
  }

  const move = useCallback((edge, time) => {
    const next = edge === 'start' ? clampSelection(time, e, total, 'start') : clampSelection(s, time, total, 'end')
    onChange(next.start, next.end)
  }, [e, s, total, onChange])

  const onHandleDown = edge => event => {
    event.preventDefault()
    event.stopPropagation()
    event.currentTarget.focus()
    event.currentTarget.setPointerCapture?.(event.pointerId)
    dragRef.current = edge
  }
  const onHandleMove = edge => event => { if (dragRef.current === edge) move(edge, toTime(event.clientX)) }
  const onHandleUp = event => { dragRef.current = null; event.currentTarget.releasePointerCapture?.(event.pointerId) }
  const onHandleKey = edge => event => {
    const delta = nudge(event.key, event.shiftKey)
    if (event.key === 'Home') { event.preventDefault(); move(edge, edge === 'start' ? 0 : s + 0.1); return }
    if (event.key === 'End') { event.preventDefault(); move(edge, edge === 'end' ? total : e - 0.1); return }
    if (delta == null) return
    event.preventDefault()
    move(edge, (edge === 'start' ? s : e) + delta)
  }

  const handle = (edge, value) => (
    <div role="slider" tabIndex={0} aria-label={edge === 'start' ? 'Clip start' : 'Clip end'}
      aria-valuemin={0} aria-valuemax={Number(total.toFixed(1))} aria-valuenow={Number(value.toFixed(1))} aria-valuetext={clock(value)}
      onPointerDown={onHandleDown(edge)} onPointerMove={onHandleMove(edge)} onPointerUp={onHandleUp} onPointerCancel={onHandleUp}
      onKeyDown={onHandleKey(edge)} onClick={event => event.stopPropagation()}
      title={`${edge === 'start' ? 'Start' : 'End'} · drag, or use arrow keys (Shift = 1 s)`}
      className="group absolute top-0 bottom-0 z-10 -ml-2.5 flex w-5 cursor-ew-resize touch-none justify-center outline-none"
      style={{ left: `${xAt(value, width, total)}px` }}>
      <span className={`h-full w-0.5 ${bad ? 'bg-red-400' : 'bg-vault-glow'} group-focus-visible:w-1`} />
      <span className={`absolute ${edge === 'start' ? 'top-0' : 'bottom-0'} h-4 w-3 rounded-sm border ${bad ? 'border-red-300 bg-red-400' : 'border-white/70 bg-vault-accent'} shadow group-focus-visible:ring-2 group-focus-visible:ring-white`} />
    </div>
  )

  return (
    <div className="flex flex-col gap-1.5">
      <div ref={wrapRef} className="relative h-24 w-full cursor-pointer select-none overflow-hidden rounded-lg border border-vault-border bg-vault-elevated/60"
        onClick={event => onSeek?.(toTime(event.clientX))} title="Click to listen from here">
        <canvas ref={canvasRef} className="absolute inset-0 h-full w-full" aria-hidden="true" />
        {width > 0 && <div className={`pointer-events-none absolute top-0 bottom-0 ${bad ? 'bg-red-400/10' : 'bg-vault-accent/10'}`}
          style={{ left: `${xAt(s, width, total)}px`, width: `${Math.max(0, xAt(e, width, total) - xAt(s, width, total))}px` }} />}
        {width > 0 && Number.isFinite(playhead) && <div className="pointer-events-none absolute top-0 bottom-0 w-px bg-white/80" style={{ left: `${xAt(playhead, width, total)}px` }} />}
        {width > 0 && handle('start', Math.min(s, total))}
        {width > 0 && handle('end', Math.min(e, total))}
        {state === 'loading' && <div className="pointer-events-none absolute inset-0 flex items-center justify-center gap-2 text-[11px] text-vault-muted"><Loader2 size={13} className="animate-spin" />Drawing the sound…</div>}
        {state === 'error' && <div className="pointer-events-none absolute inset-0 flex items-center justify-center px-3 text-center text-[11px] text-vault-muted">{message}</div>}
      </div>
      <div className="flex justify-between font-mono text-[10px] text-vault-muted"><span>0.0 s</span><span>{clock(total)}</span></div>
    </div>
  )
}
