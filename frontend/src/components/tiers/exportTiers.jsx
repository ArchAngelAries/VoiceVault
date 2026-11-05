/**
 * components/tiers/exportTiers.jsx — Writes a board to a PNG, a board PDF, and an editable PDF.
 * PNG: ExportBoard is drawn off-screen at the design width and captured with html2canvas, then scaled to the
 *      width the user picked (1600, 3200 or 4800 px).
 * Board PDF: that PNG, fitted to an A4 page.
 * Editable PDF: plain text (title, tiers, ranked slots with their titles, small thumbnails, notes), so it can be
 *      edited in a PDF editor. Embedded Noto Sans JP covers Japanese, Latin and Cyrillic; unsupported glyphs are reported.
 * The board PDF is a raster image and can preserve scripts rendered by the local system fonts.
 */
import React from 'react'
import { createRoot } from 'react-dom/client'
import { flushSync } from 'react-dom'
import html2canvas from 'html2canvas'
import { jsPDF } from 'jspdf'
import ExportBoard, { EXPORT_DESIGN_WIDTH } from '@/components/tiers/ExportBoard'
import { exportImageSrc } from '@/components/tiers/exportImages'
import { tierPdfRemainingCapacity } from '@/lib/tierPdfPagination'
import { hexToRgb, textOn } from '@/data/tierColors'
import { loadEditablePdfFonts } from './editablePdfFontAsset'
import { missingPdfGlyphs, registerEditablePdfFonts } from './editablePdfFont'

const IMAGE_TIMEOUT_MS = 20000
const COVER_TIMEOUT_MS = 20000
const THUMB_CONCURRENCY = 6
const MAX_TIER_LABEL_LINES = 10

function waitForImages(node) {
  const images = [...node.querySelectorAll('img')]
  return Promise.all(images.map(img => new Promise(resolve => {
    if (img.complete) return resolve(img.naturalWidth > 0)
    let timer
    const finish = (loaded) => {
      clearTimeout(timer)
      img.onload = null
      img.onerror = null
      resolve(loaded)
    }
    img.onload = () => finish(true)
    img.onerror = () => finish(false)
    timer = setTimeout(() => finish(false), IMAGE_TIMEOUT_MS)
  })))
}

// Draws the board off-screen and returns a PNG data URL. failedCovers counts images that did not load.
export async function renderBoardPng(board, width) {
  const host = document.createElement('div')
  Object.assign(host.style, { position: 'fixed', left: '-20000px', top: '0', pointerEvents: 'none' })
  document.body.appendChild(host)
  const root = createRoot(host)
  try {
    flushSync(() => root.render(<ExportBoard title={board.title} subtitle={board.subtitle} rows={board.rows} />))
    const node = host.firstElementChild
    const loaded = await waitForImages(node)
    const canvas = await html2canvas(node, {
      scale: width / EXPORT_DESIGN_WIDTH, useCORS: true, backgroundColor: '#0c0c12', logging: false,
    })
    return {
      dataUrl: canvas.toDataURL('image/png'),
      canvas,
      width: canvas.width,
      height: canvas.height,
      failedCovers: loaded.filter(ok => !ok).length,
    }
  } finally {
    root.unmount()
    host.remove()
  }
}

// Loads one cover and returns a small JPEG data URL (white behind it), so the PDF stays light.
function coverAsJpeg(src, maxSide = 240) {
  return new Promise((resolve, reject) => {
    const img = new Image()
    let settled = false
    const finish = (callback, value) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      img.onload = null
      img.onerror = null
      callback(value)
    }
    const timer = setTimeout(() => {
      finish(reject, new Error('The cover took too long to load.'))
      img.src = ''
    }, COVER_TIMEOUT_MS)
    img.crossOrigin = 'anonymous'
    img.onload = () => {
      const scale = Math.min(1, maxSide / Math.max(img.naturalWidth, img.naturalHeight))
      const canvas = document.createElement('canvas')
      canvas.width = Math.max(1, Math.round(img.naturalWidth * scale))
      canvas.height = Math.max(1, Math.round(img.naturalHeight * scale))
      const ctx = canvas.getContext('2d')
      ctx.fillStyle = '#ffffff'
      ctx.fillRect(0, 0, canvas.width, canvas.height)
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
      try { finish(resolve, canvas.toDataURL('image/jpeg', 0.85)) } catch (e) { finish(reject, e) }
    }
    img.onerror = () => finish(reject, new Error('The cover could not be loaded.'))
    img.src = src
  })
}

async function loadThumbnails(tiles) {
  const unique = [...new Map(tiles.map(tile => [tile.id, tile])).values()]
    .filter(tile => exportImageSrc(tile.cover_url))
  const found = new Map()
  let failedCovers = 0
  let next = 0
  const worker = async () => {
    while (next < unique.length) {
      const tile = unique[next++]
      const data = await coverAsJpeg(exportImageSrc(tile.cover_url)).catch(() => null)
      if (data) found.set(tile.id, data)
      else failedCovers += 1
    }
  }
  await Promise.all(Array.from({ length: Math.min(THUMB_CONCURRENCY, unique.length) }, worker))
  return { thumbnails: found, failedCovers }
}

// The board fitted to an A4 page (landscape for wide boards, portrait otherwise). JPEG keeps the file small.
export function boardPdf(png, filename) {
  const doc = new jsPDF({ orientation: png.width >= png.height ? 'landscape' : 'portrait', unit: 'pt', format: 'a4' })
  const pageW = doc.internal.pageSize.getWidth()
  const pageH = doc.internal.pageSize.getHeight()
  const margin = 24
  const scale = Math.min((pageW - 2 * margin) / png.width, (pageH - 2 * margin) / png.height)
  const w = png.width * scale
  const h = png.height * scale
  doc.addImage(png.canvas.toDataURL('image/jpeg', 0.92), 'JPEG', (pageW - w) / 2, (pageH - h) / 2, w, h)
  doc.save(filename)
}

// Text-based PDF: title, tiers, ranked slots with titles and small thumbnails, then notes.
export async function editablePdf(board, filename) {
  const [regularFontData, boldFontData] = await loadEditablePdfFonts()
  const { thumbnails: thumbs, failedCovers } = await loadThumbnails(board.rows.flatMap(row => row.items))
  const doc = new jsPDF({ unit: 'pt', format: 'a4' })
  registerEditablePdfFonts(doc, regularFontData, boldFontData)
  const missingGlyphs = missingPdfGlyphs(doc, [
    board.title, board.subtitle, board.notes,
    ...board.rows.flatMap(row => [row.label, ...row.items.map(tile => tile.title)]),
    'Exported', 'Nothing ranked here yet.', 'Notes', 'No notes.',
  ])
  const pageW = doc.internal.pageSize.getWidth()
  const pageH = doc.internal.pageSize.getHeight()
  const M = 40
  const LABEL_W = 70
  const GRID_X = M + LABEL_W + 12
  const SLOT_W = 62
  const THUMB_W = 46
  const THUMB_H = 64
  const TITLE_LINES = 3
  const LINE = 8.5
  const SLOT_H = THUMB_H + 12 + TITLE_LINES * LINE
  const LINE_STRIDE = SLOT_H + 12
  const PER_LINE = Math.max(1, Math.floor((pageW - M - GRID_X) / SLOT_W))
  const PDF_FONT = 'VoiceVaultPdfJapanese'
  const headerWidth = pageW - 2 * M
  let y = M

  const newPageIf = (needed) => {
    if (y + needed > pageH - M) {
      doc.addPage()
      y = M
    }
  }

  doc.setFont(PDF_FONT, 'bold')
  doc.setFontSize(22)
  doc.setTextColor(20)
  const titleLines = doc.splitTextToSize(String(board.title ?? ''), headerWidth)
  doc.text(titleLines, M, y + 18)
  y += 30 + Math.max(0, titleLines.length - 1) * 26
  if (board.subtitle) {
    doc.setFont(PDF_FONT, 'normal')
    doc.setFontSize(10)
    doc.setTextColor(90)
    const subtitleLines = doc.splitTextToSize(String(board.subtitle), headerWidth)
    doc.text(subtitleLines, M, y + 10)
    y += 18 + Math.max(0, subtitleLines.length - 1) * 12
  }
  doc.setFont(PDF_FONT, 'normal')
  doc.setFontSize(9)
  doc.setTextColor(120)
  doc.text(`Exported ${new Date().toISOString().slice(0, 10)}`, M, y + 10)
  y += 26

  let truncatedLabels = 0
  for (const row of board.rows) {
    let itemOffset = 0
    let segmentIndex = 0
    let wroteEmptySegment = false
    while (itemOffset < row.items.length || (!row.items.length && !wroteEmptySegment)) {
      // splitTextToSize uses the active face and size; set the row label style before measuring.
      doc.setFont(PDF_FONT, 'bold')
      doc.setFontSize(14)
      const allLabelLines = doc.splitTextToSize(String(row.label ?? ''), LABEL_W - 10)
      const labelLines = allLabelLines.length > MAX_TIER_LABEL_LINES
        ? [...allLabelLines.slice(0, MAX_TIER_LABEL_LINES - 1), '…']
        : allLabelLines
      if (allLabelLines.length > MAX_TIER_LABEL_LINES && segmentIndex === 0) truncatedLabels += 1
      const labelHeight = labelLines.length * 14 + 18
      const availableItems = tierPdfRemainingCapacity(pageH, M, y, LINE_STRIDE, PER_LINE, labelHeight)
      const remainingItems = row.items.length - itemOffset
      if ((remainingItems > 0 && availableItems === 0 || y + labelHeight + 6 > pageH - M) && y > M) {
        doc.addPage()
        y = M
        continue
      }
      const segmentCapacity = availableItems || (remainingItems > 0 ? PER_LINE : 0)
      const items = row.items.slice(itemOffset, itemOffset + segmentCapacity)
      const lines = Math.max(1, Math.ceil(items.length / PER_LINE))
      const rowH = Math.max(lines * LINE_STRIDE + 8, labelHeight)
      if (y + rowH + 6 > pageH - M && y > M) {
        doc.addPage()
        y = M
        continue
      }
      const [r, g, b] = hexToRgb(row.color)
      doc.setFillColor(r, g, b)
      doc.rect(M, y, LABEL_W, rowH, 'F')
      const [tr, tg, tb] = hexToRgb(textOn(row.color))
      doc.setTextColor(tr, tg, tb)
      doc.setFont(PDF_FONT, 'bold')
      doc.text(labelLines, M + LABEL_W / 2, y + rowH / 2 - ((labelLines.length - 1) * 7) / 2 + 5, {
        align: 'center', lineHeightFactor: 1,
      })

      if (items.length === 0) {
        doc.setFont(PDF_FONT, 'normal')
        doc.setFontSize(9)
        doc.setTextColor(140)
        doc.text('Nothing ranked here yet.', GRID_X, y + 22)
      }
      items.forEach((tile, index) => {
        const x = GRID_X + (index % PER_LINE) * SLOT_W
        const top = y + Math.floor(index / PER_LINE) * (SLOT_H + 12) + 4
        doc.setFont(PDF_FONT, 'bold')
        doc.setFontSize(7)
        doc.setTextColor(110)
        doc.text(`#${itemOffset + index + 1}`, x, top + 6)
        const thumb = thumbs.get(tile.id)
        if (thumb) {
          doc.addImage(thumb, 'JPEG', x, top + 9, THUMB_W, THUMB_H)
        } else {
          doc.setDrawColor(190)
          doc.rect(x, top + 9, THUMB_W, THUMB_H)
        }
        doc.setFont(PDF_FONT, 'normal')
        doc.setTextColor(30)
        const titleLines = doc.splitTextToSize(tile.title || '', SLOT_W - 4).slice(0, TITLE_LINES)
        doc.text(titleLines, x, top + 9 + THUMB_H + 10)
      })
      y += rowH + 8
      itemOffset += items.length
      wroteEmptySegment = true
      segmentIndex += 1
    }
  }

  newPageIf(40)
  y += 8
  doc.setFont(PDF_FONT, 'bold')
  doc.setFontSize(12)
  doc.setTextColor(20)
  doc.text('Notes', M, y + 12)
  y += 22
  doc.setFont(PDF_FONT, 'normal')
  doc.setFontSize(10)
  doc.setTextColor(40)
  for (const line of doc.splitTextToSize(board.notes || 'No notes.', pageW - 2 * M)) {
    newPageIf(14)
    doc.text(line, M, y + 10)
    y += 14
  }
  doc.save(filename)
  return { missingGlyphs, truncatedLabels, failedCovers }
}

export const exportFileBase = (title) => {
  const slug = (title || 'tier-list').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'tier-list'
  return `${slug}-${new Date().toISOString().slice(0, 10)}`
}

export function savePng(dataUrl, filename) {
  const link = document.createElement('a')
  link.href = dataUrl
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
}
