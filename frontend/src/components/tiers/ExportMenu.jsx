/**
 * components/tiers/ExportMenu.jsx — Export a board: a PNG at the chosen width, a board PDF, or an editable PDF.
 * The board is read at the moment of the click, so the export matches what is on screen.
 */
import React, { useState } from 'react'
import { Download, Loader2 } from 'lucide-react'
import { EXPORT_WIDTHS } from '@/components/tiers/ExportBoard'

const errorText = (e) => e?.message || 'The export failed.'

export default function ExportMenu({ getBoard }) {
  const [width, setWidth] = useState(EXPORT_WIDTHS[0].value)
  const [busy, setBusy]   = useState(null)           // 'png' | 'pdf' | 'editable'
  const [status, setStatus] = useState(null)         // { tone: 'ok' | 'warn' | 'error', text }

  const run = async (kind) => {
    const board = getBoard()
    setBusy(kind)
    setStatus(null)
    try {
      // Loaded on first use: the PDF and canvas code is large and most visits never export.
      const exporter = await import('@/components/tiers/exportTiers')
      const base = exporter.exportFileBase(board.title)
      if (kind === 'editable') {
        const result = await exporter.editablePdf(board, `${base}-editable.pdf`)
        const unsupported = result?.missingGlyphs || []
        const unsupportedNote = unsupported.length
          ? ` The editable font does not include ${unsupported.slice(0, 6).join(' ')}${unsupported.length > 6 ? ' …' : ''}; those characters may be substituted. The board PDF keeps the rendered board as an image.`
          : null
        const limitations = [
          unsupportedNote,
          result?.truncatedLabels > 0 && `${result.truncatedLabels} long tier label${result.truncatedLabels === 1 ? ' was' : 's were'} shortened with an ellipsis to fit.`,
          result?.failedCovers > 0 && `${result.failedCovers} cover${result.failedCovers === 1 ? ' did' : 's did'} not load and ${result.failedCovers === 1 ? 'is' : 'are'} blank.`,
        ].filter(Boolean)
        setStatus(limitations.length
          ? { tone: 'warn', text: `Saved editable PDF. ${limitations.join(' ')}` }
          : { tone: 'ok', text: 'Saved the editable PDF.' })
      } else {
        const png = await exporter.renderBoardPng(board, width)
        if (kind === 'png') exporter.savePng(png.dataUrl, `${base}-${width}px.png`)
        else exporter.boardPdf(png, `${base}.pdf`)
        const saved = kind === 'png' ? 'Saved the PNG.' : 'Saved the PDF.'
        setStatus(png.failedCovers
          ? { tone: 'warn', text: `${saved} ${png.failedCovers} cover${png.failedCovers === 1 ? ' did' : 's did'} not load and ${png.failedCovers === 1 ? 'is' : 'are'} blank.` }
          : { tone: 'ok', text: saved })
      }
    } catch (e) {
      setStatus({ tone: 'error', text: errorText(e) })
    } finally {
      setBusy(null)
    }
  }

  const button = 'inline-flex items-center gap-1.5 rounded-md border border-vault-border px-3 py-1.5 font-body text-xs text-vault-muted hover:text-vault-text hover:border-vault-accent disabled:opacity-40 disabled:cursor-not-allowed'

  return (
    <div className="flex flex-wrap items-center gap-2">
      <label className="flex items-center gap-2 font-body text-xs text-vault-muted">
        Export size
        <select value={width} onChange={e => setWidth(Number(e.target.value))} disabled={busy !== null}
          className="bg-vault-elevated border border-vault-border rounded-lg px-2 py-1 font-body text-xs text-vault-text">
          {EXPORT_WIDTHS.map(w => <option key={w.value} value={w.value}>{w.label}</option>)}
        </select>
      </label>
      <button type="button" onClick={() => run('png')} disabled={busy !== null} className={button}>
        {busy === 'png' ? <Loader2 size={12} className="animate-spin" /> : <Download size={12} />} PNG
      </button>
      <button type="button" onClick={() => run('pdf')} disabled={busy !== null} className={button}>
        {busy === 'pdf' ? <Loader2 size={12} className="animate-spin" /> : <Download size={12} />} Board PDF
      </button>
      <button type="button" onClick={() => run('editable')} disabled={busy !== null} className={button}>
        {busy === 'editable' ? <Loader2 size={12} className="animate-spin" /> : <Download size={12} />} Editable PDF
      </button>
      {status && (
        <span role="status" className={
          status.tone === 'error' ? 'font-body text-xs text-red-300'
            : status.tone === 'warn' ? 'font-body text-xs text-amber-300'
            : 'font-body text-xs text-emerald-400'}>
          {status.text}
        </span>
      )}
    </div>
  )
}
