/**
 * components/discover/CharacterCard.jsx — Character card in detail modal
 */
import React, { useState } from 'react'
import { User, Mic, Plus, BookmarkPlus, MessageSquare, UserPlus, Loader2 } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { characterChatProfile } from '@/lib/characterChatProfile'
import HeartButton from './HeartButton'
import { createCastMember } from '@/api/cast'
import AddToCollectionModal from './AddToCollectionModal'

// Typed item IDs look like "character:source:id". The backend also sends `source`.
function sourceFromId(id) {
  const [type, source] = String(id ?? '').split(':')
  return type === 'character' && source ? source : 'unknown'
}

export default function CharacterCard({ character, onHarvest, onClick }) {
  const navigate = useNavigate()
  const [addingToCast, setAddingToCast] = useState(false)
  const addToCast = async e => {
    e.stopPropagation()
    setAddingToCast(true)
    try {
      const saved = await createCastMember({ kind: 'established', variant: '', profile: characterChatProfile(character),
        sources: [], open_questions: [], review_state: 'draft' })
      navigate(`/cast?id=${encodeURIComponent(saved.id)}`)
    } catch { setAddingToCast(false) }
  }
  const [imgErr, setImgErr]   = useState(false)
  const [showAdd, setShowAdd] = useState(false)
  const isWikidata = character.source === 'wikidata' || String(character.id || '').startsWith('character:wikidata:')
  const qid = character.source_id || String(character.id || '').match(/^character:wikidata:(Q\d+)$/)?.[1]

  // Normalize character to the shape collectionsStore expects
  const itemShape = {
    id:         String(character.id ?? ''),
    media_type: 'character',
    source:     character.source || sourceFromId(character.id),
    title:      character.name,
    name:       character.name,
    cover:      isWikidata ? '' : character.image,
    photo:      isWikidata ? '' : character.image,
    year:       null,
  }
  const profileShape = { ...character, ...itemShape }

  return (
    <>
      <div onClick={() => onClick?.(profileShape)} role={onClick ? 'button' : undefined} tabIndex={onClick ? 0 : undefined}
        onKeyDown={e => { if (e.target === e.currentTarget && onClick && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); onClick(profileShape) } }}
        className={`vault-card-elevated p-3 flex gap-3 hover:border-vault-accent/50 transition-colors ${onClick ? 'cursor-pointer' : ''}`}>
        {/* Avatar */}
        <div className="w-14 h-14 rounded-lg overflow-hidden bg-vault-border shrink-0">
          {character.image && !isWikidata && !imgErr ? (
            <img src={character.image} alt={character.name}
              onError={() => setImgErr(true)}
              className="w-full h-full object-cover" />
          ) : (
            <div className="w-full h-full flex items-center justify-center">
              <User size={20} className="text-vault-border" />
            </div>
          )}
        </div>

        {/* Info */}
        <div className="flex-1 min-w-0">
          <p className="font-display font-semibold text-sm text-vault-text truncate">{character.name}</p>
          {isWikidata && <p className="font-mono text-[9px] text-vault-accent truncate">Wikidata · {qid || 'QID unavailable'} · CC0</p>}
          {character.name_native && (
            <p className="font-mono text-[10px] text-vault-muted truncate">{character.name_native}</p>
          )}
          {(character.media_title || character.media_titles?.length > 0 || character.originating_works?.length > 0) && (
            <div className="mt-1 font-body text-[10px] text-vault-muted truncate">
              {character.media_title ? `From ${character.media_title}` : character.originating_works?.length
                ? `Works: ${character.originating_works.slice(0, 2).map(work => work.name).filter(Boolean).join(', ')}`
                : `Appears in ${character.media_titles.slice(0, 3).join(', ')}`}
            </div>
          )}
          {!isWikidata && character.voice_actors?.[0] && (
            <div className="flex items-center gap-1 mt-1">
              <Mic size={9} className="text-vault-accent shrink-0" />
              <span className="font-body text-[10px] text-vault-muted truncate">
                {character.voice_actors[0].name}
              </span>
            </div>
          )}
          {!isWikidata && character.actor && (
            <div className="flex items-center gap-1 mt-1">
              <Mic size={9} className="text-vault-accent shrink-0" />
              <span className="font-body text-[10px] text-vault-muted truncate">{character.actor}</span>
            </div>
          )}
        </div>

        {/* Action buttons */}
        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={e => { e.stopPropagation(); navigate('/converse', { state: { characterProfile: characterChatProfile(character), characterId: String(character.id ?? '') } }) }}
            title={`Chat with ${character.name || 'this character'}`} aria-label={`Chat with ${character.name || 'this character'}`}
            className="w-7 h-7 rounded-lg border border-vault-border flex items-center justify-center text-vault-muted hover:text-vault-accent hover:border-vault-accent transition-colors">
            <MessageSquare size={13} />
          </button>
          <button onClick={addToCast} disabled={addingToCast}
            title={`Add ${character.name || 'this character'} to your Cast`} aria-label={`Add ${character.name || 'this character'} to your Cast`}
            className="w-7 h-7 rounded-lg border border-vault-border flex items-center justify-center text-vault-muted hover:text-vault-accent hover:border-vault-accent transition-colors disabled:opacity-50">
            {addingToCast ? <Loader2 size={13} className="animate-spin" /> : <UserPlus size={13} />}
          </button>
          <HeartButton item={itemShape} size={13}
            className="w-7 h-7 rounded-lg border border-vault-border hover:border-vault-accent/50" />
          <button
            onClick={(e) => { e.stopPropagation(); setShowAdd(true) }}
            title="Add to Collection"
            className="w-7 h-7 rounded-lg border border-vault-border flex items-center justify-center
                       text-vault-muted hover:text-vault-accent hover:border-vault-accent transition-colors">
            <BookmarkPlus size={13} />
          </button>
          {onHarvest && (
            <button
              onClick={(e) => { e.stopPropagation(); onHarvest(character) }}
              title="Queue for harvest"
              className="w-7 h-7 rounded-lg border border-vault-border flex items-center justify-center
                         text-vault-muted hover:text-vault-accent hover:border-vault-accent transition-colors">
              <Plus size={13} />
            </button>
          )}
        </div>
      </div>

      {showAdd && <AddToCollectionModal item={itemShape} onClose={() => setShowAdd(false)} />}
    </>
  )
}
