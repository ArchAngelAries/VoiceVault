import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { jsPDF } from 'jspdf'
import {
  fontBytesToBase64,
  missingPdfGlyphs,
  registerEditablePdfFonts,
} from './editablePdfFont.js'

function ttfWeightClass(bytes) {
  const tableCount = bytes.readUInt16BE(4)
  for (let index = 0; index < tableCount; index += 1) {
    const entry = 12 + index * 16
    if (bytes.toString('ascii', entry, entry + 4) === 'OS/2') {
      return bytes.readUInt16BE(bytes.readUInt32BE(entry + 8) + 4)
    }
  }
  throw new Error('The font has no OS/2 table.')
}

test('static embedded faces preserve Japanese and report scripts outside their character map', async () => {
  const regularBuffer = await readFile(new URL('../../assets/fonts/VoiceVaultPdfJP-Regular.ttf', import.meta.url))
  const boldBuffer = await readFile(new URL('../../assets/fonts/VoiceVaultPdfJP-Bold.ttf', import.meta.url))
  assert.equal(ttfWeightClass(regularBuffer), 400)
  assert.equal(ttfWeightClass(boldBuffer), 700)
  const regularBytes = new Uint8Array(regularBuffer)
  const boldBytes = new Uint8Array(boldBuffer)
  const doc = new jsPDF()
  registerEditablePdfFonts(doc, fontBytesToBase64(regularBytes), fontBytesToBase64(boldBytes))

  assert.deepEqual(missingPdfGlyphs(doc, ['日本語タイトル', 'あいうえお', 'Café Привет']), [])
  assert.deepEqual(missingPdfGlyphs(doc, ['😀 العربية']), ['😀', 'ا', 'ل', 'ع', 'ر', 'ب', 'ي', 'ة'])

  doc.setFont('VoiceVaultPdfJapanese', 'normal')
  doc.text('日本語タイトル あいうえお Café Привет', 20, 20)
  doc.setFont('VoiceVaultPdfJapanese', 'bold')
  doc.text('太字の見出し', 20, 34)
  const pdf = doc.output()
  assert.match(pdf, /\/FontFile2\b/, 'PDF must embed the TrueType font data')
  assert.match(pdf, /\/ToUnicode\b/, 'PDF must include mappings so text remains selectable and editable')
  assert.match(pdf, /<65e5>/i, 'PDF character map must preserve the Unicode code point for 日')
  assert.ok((pdf.match(/\/FontFile2 \d+ 0 R/g) || []).length >= 2, 'PDF must embed both regular and bold face data')
  assert.ok(pdf.length < regularBytes.length + boldBytes.length, 'jsPDF should embed only used glyph subsets')
})
