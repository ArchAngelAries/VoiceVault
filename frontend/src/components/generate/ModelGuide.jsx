/**
 * components/generate/ModelGuide.jsx — What the selected engine takes: voice description, reference clip, tags.
 * Folded away by default; the open/closed choice is remembered.
 */
import React from 'react'
import { BookOpen } from 'lucide-react'
import Disclosure from '@/components/generate/Disclosure'

export default function ModelGuide({ name, guide }) {
  if (!guide) return null

  const rows = [
    ['Voice description', guide.voice],
    ['Reference clip', guide.reference],
    ['Tags', guide.tags],
  ].filter(([, text]) => text)

  return (
    <Disclosure storageKey="vv.generate.guideOpen" title={`How to use ${name}`}
      icon={<BookOpen size={14} className="text-vault-accent shrink-0" />}>
      <div className="flex flex-col gap-2 font-body text-xs text-vault-muted">
        {guide.intro && <p>{guide.intro}</p>}
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2">
          {rows.map(([label, text]) => (
            <React.Fragment key={label}>
              <dt className="font-mono text-[10px] uppercase tracking-wide text-vault-muted pt-0.5">{label}</dt>
              <dd className="text-vault-text/90">{text}</dd>
            </React.Fragment>
          ))}
        </dl>
        {guide.notes?.length > 0 && (
          <ul className="list-disc pl-5 flex flex-col gap-1">
            {guide.notes.map(note => <li key={note}>{note}</li>)}
          </ul>
        )}
      </div>
    </Disclosure>
  )
}
