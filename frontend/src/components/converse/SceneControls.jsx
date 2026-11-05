import React from 'react'
import { ArrowDown, ArrowUp, Play, Square } from 'lucide-react'
import { normalizeScene, moveSceneParticipant, toggleSceneParticipant } from './sceneSettings'

const statusCopy = {
  running: 'Scene in progress', complete: 'Scene completed', stopped: 'Scene stopped', error: 'Scene ended with an error',
}

export default function SceneControls({ scene, characters = [], onChange, onRun, onStop, disabled = false, canRun = true, progress }) {
  const value = normalizeScene(scene, characters)
  const selected = new Set(value.participant_ids)
  const characterById = new Map(characters.map(character => [String(character.id), character]))
  const orderedCharacters = [
    ...value.participant_ids.map(id => characterById.get(id)).filter(Boolean),
    ...characters.filter(character => !selected.has(String(character.id))),
  ]
  const update = next => onChange?.(normalizeScene(next, characters))
  const runDisabled = disabled || !canRun || value.participant_ids.length === 0
  const totalTurns = value.participant_ids.length * value.rounds
  const status = progress?.status
  const inProgress = status === 'running'
  const currentTurn = Number(progress?.turn_index) || 0
  const progressTotal = Number(progress?.total_turns) || totalTurns
  const completed = Number(progress?.completed) || 0

  return <section className="flex flex-col gap-3 rounded-xl border border-vault-border bg-vault-elevated/40 p-3" aria-label="Scene controls">
    <div className="flex items-center justify-between gap-3">
      <h3 className="text-sm font-semibold text-vault-text">Scene</h3>
      <span className="text-[11px] text-vault-muted">Up to 18 turns</span>
    </div>

    <div className="flex flex-col gap-1.5">
      <label htmlFor="scene-context" className="text-xs text-vault-muted">Scene context</label>
      <textarea id="scene-context" value={value.context} maxLength={4000} rows={3} disabled={disabled}
        onChange={event => update({ ...value, context: event.target.value })}
        className="w-full resize-y rounded-lg border border-vault-border bg-vault-bg px-3 py-2 text-sm text-vault-text focus:border-vault-accent focus:outline-none disabled:opacity-50"
        placeholder="Describe the setting, situation, or what the characters should discuss." />
      <span className="self-end text-[10px] text-vault-muted">{value.context.length}/4000</span>
    </div>

    <fieldset className="flex flex-col gap-2" disabled={disabled}>
      <legend className="mb-1 text-xs text-vault-muted">Participants · choose and arrange the speaking order</legend>
      {orderedCharacters.map(character => {
        const id = String(character.id)
        const checked = selected.has(id)
        const name = character.profile?.name || character.name || 'Character'
        const selectedIndex = value.participant_ids.indexOf(id)
        const unavailableByLimit = !checked && value.participant_ids.length >= 6
        return <div key={id} className="flex items-center gap-2 rounded-lg border border-vault-border/70 px-2.5 py-2">
          <label className="flex min-w-0 flex-1 cursor-pointer items-center gap-2 text-sm text-vault-text">
            <input type="checkbox" checked={checked} disabled={unavailableByLimit}
              onChange={() => update(toggleSceneParticipant(value, id))}
              className="h-4 w-4 accent-vault-accent disabled:opacity-40" />
            <span className="truncate">{name}</span>
          </label>
          {checked && <>
            <span className="text-[10px] text-vault-muted">{selectedIndex + 1}</span>
            <button type="button" aria-label={`Move ${name} earlier`} title="Move earlier" disabled={selectedIndex === 0}
              onClick={() => update(moveSceneParticipant(value, id, -1))}
              className="vault-btn-ghost inline-flex h-7 w-7 !p-0 items-center justify-center disabled:opacity-30"><ArrowUp size={13} className="shrink-0" /></button>
            <button type="button" aria-label={`Move ${name} later`} title="Move later" disabled={selectedIndex === value.participant_ids.length - 1}
              onClick={() => update(moveSceneParticipant(value, id, 1))}
              className="vault-btn-ghost inline-flex h-7 w-7 !p-0 items-center justify-center disabled:opacity-30"><ArrowDown size={13} className="shrink-0" /></button>
          </>}
        </div>
      })}
      {characters.length === 0 && <p className="text-xs text-vault-muted">Add characters to choose scene participants.</p>}
    </fieldset>

    <div className="flex flex-wrap items-center justify-between gap-3">
      <label htmlFor="scene-rounds" className="flex items-center gap-2 text-xs text-vault-muted">Rounds
        <select id="scene-rounds" value={value.rounds} disabled={disabled} onChange={event => update({ ...value, rounds: Number(event.target.value) })}
          className="rounded-md border border-vault-border bg-vault-bg px-2 py-1.5 text-sm text-vault-text disabled:opacity-50">
          {[1, 2, 3].map(round => <option key={round} value={round}>{round}</option>)}
        </select>
      </label>
      <span className="text-xs text-vault-muted">{totalTurns} {totalTurns === 1 ? 'turn' : 'turns'}</span>
      <button type="button" disabled={runDisabled} onClick={() => onRun?.(value)}
        className="vault-btn-primary inline-flex items-center gap-1.5 px-3 py-2 text-xs disabled:opacity-40"><Play size={13} />Run scene</button>
    </div>

    {status && <div role="status" aria-live="polite" className="rounded-lg border border-vault-border px-3 py-2 text-xs text-vault-muted">
      <p className="font-medium text-vault-text">{statusCopy[status] || 'Scene status'}</p>
      {inProgress && <div className="mt-1 flex flex-wrap items-center justify-between gap-2">
        <p>Turn {Math.min(currentTurn, progressTotal)} of {progressTotal}{progress?.speaker_name ? ` · ${progress.speaker_name}` : ''}</p>
        <button type="button" onClick={onStop} className="vault-btn-ghost inline-flex items-center gap-1 px-2.5 py-1.5 text-xs"><Square size={12} />Stop scene</button>
      </div>}
      {status === 'complete' && <p className="mt-1">{completed || progressTotal} of {progressTotal} turns completed.</p>}
      {status === 'stopped' && <p className="mt-1">{completed} of {progressTotal} turns completed before stopping.</p>}
      {status === 'error' && progress?.message && <p className="mt-1">{progress.message}</p>}
    </div>}
  </section>
}
