/**
 * components/common/TermsDialog.jsx — First-run terms of use summary.
 *
 * Shown once. Acceptance is remembered in localStorage (vv.termsAccepted.v1); when storage is
 * blocked the dialog simply shows again next time. The full text lives in data/terms.js
 * (kept in sync with docs/TERMS_OF_USE.md) and on Help → Terms of use.
 */
import React, { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { ScrollText } from 'lucide-react'
import { TERMS_SUMMARY, termsAccepted, rememberTermsAccepted } from '@/data/terms'

export default function TermsDialog() {
  const [open, setOpen] = useState(() => !termsAccepted())
  const button = useRef(null)

  useEffect(() => { if (open) button.current?.focus() }, [open])
  if (!open) return null

  const accept = () => { rememberTermsAccepted(); setOpen(false) }

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/70 p-4">
      <div role="dialog" aria-modal="true" aria-labelledby="vv-terms-title" data-testid="terms-dialog"
        className="vault-card w-full max-w-lg max-h-[90vh] overflow-y-auto p-6 flex flex-col gap-4 font-body text-sm text-vault-text">
        <div className="flex items-center gap-2">
          <ScrollText size={20} className="text-vault-accent" />
          <h2 id="vv-terms-title" className="font-display text-lg">Before you start</h2>
        </div>
        <ul className="list-disc pl-5 flex flex-col gap-2 text-vault-text/90">
          {TERMS_SUMMARY.map(line => <li key={line}>{line}</li>)}
        </ul>
        <p className="text-xs text-vault-muted">
          This is a summary. The full terms are always in{' '}
          <Link to="/help?section=terms" onClick={() => setOpen(false)} className="text-vault-glow underline">Help → Terms of use</Link>
          {' '}(this box will show again until you press “I understand”).
        </p>
        <div className="flex justify-end">
          <button ref={button} type="button" onClick={accept} className="vault-btn-primary px-4 py-2">I understand</button>
        </div>
      </div>
    </div>
  )
}
