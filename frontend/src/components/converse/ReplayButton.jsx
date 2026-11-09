/**
 * The play button on a reply bubble.
 * - A reply with saved audio replays it instantly, sentence by sentence, in order.
 * - A reply without it (older chats, or audio deleted in the Library) offers Speak: it is voiced now
 *   and the audio is kept for next time.
 */
import React, { useEffect, useRef, useState } from 'react'
import { Loader2, Play, Square, Volume2 } from 'lucide-react'
import { audioUrl } from '@/api/tts'

let playing = null   // only one reply plays at a time across the whole chat

export default function ReplayButton({ message, onSpeak, speakProblem = '', busy = false, disabled = false }) {
  const [state, setState] = useState('idle')        // idle | playing | missing
  const stopRef = useRef(null)
  const files = Array.isArray(message?.audio) ? message.audio.map(a => a?.file).filter(Boolean) : []
  const key = files.join('|')

  useEffect(() => { stopRef.current?.(); setState('idle') }, [key])
  useEffect(() => () => stopRef.current?.(), [])

  const stop = () => stopRef.current?.()

  const play = () => {
    if (playing && playing !== stopRef) playing.current?.()
    let index = 0
    let audio = null
    let stopped = false
    const finish = (next = 'idle') => {
      stopped = true
      audio?.pause()
      if (playing === stopRef) playing = null
      stopRef.current = null
      setState(next)
    }
    stopRef.current = () => finish('idle')
    playing = stopRef
    const playNext = () => {
      if (stopped) return
      if (index >= files.length) { finish('idle'); return }
      audio = new Audio(audioUrl(`/api/tts/audio/${encodeURIComponent(files[index])}`))
      index += 1
      audio.onended = playNext
      audio.onerror = () => finish('missing')            // deleted in the Library: offer Speak instead
      // A blocked or interrupted play() isn't missing audio: only a load error (onerror) offers Speak again.
      audio.play().catch(() => finish('idle'))
    }
    setState('playing')
    playNext()
  }

  const base = 'inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] font-mono text-vault-muted hover:text-vault-glow disabled:opacity-40'
  if (files.length && state !== 'missing') {
    return state === 'playing'
      ? <button type="button" onClick={stop} className={base} aria-label="Stop replay"><Square size={11} />Stop</button>
      : <button type="button" onClick={play} disabled={disabled} className={base} aria-label="Replay this reply" title="Replay the saved audio"><Play size={11} />Replay</button>
  }
  if (!onSpeak) return null
  return <button type="button" onClick={() => onSpeak(message)} disabled={disabled || busy || !!speakProblem}
    title={speakProblem || (state === 'missing' ? 'The saved audio was deleted; speak it again' : 'Speak this reply now (the audio is kept for replay)')}
    className={base} aria-label="Speak this reply">
    {busy ? <Loader2 size={11} className="animate-spin" /> : <Volume2 size={11} />}Speak</button>
}
