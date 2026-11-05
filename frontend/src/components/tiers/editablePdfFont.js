/** Register the embedded Noto Sans JP font and report characters outside its cmap. */
export const EDITABLE_PDF_FONT = 'VoiceVaultPdfJapanese'
export const EDITABLE_PDF_REGULAR_FILE = 'VoiceVaultPdfJP-Regular.ttf'
export const EDITABLE_PDF_BOLD_FILE = 'VoiceVaultPdfJP-Bold.ttf'

export function registerEditablePdfFonts(doc, regularBase64, boldBase64) {
  doc.addFileToVFS(EDITABLE_PDF_REGULAR_FILE, regularBase64)
  doc.addFileToVFS(EDITABLE_PDF_BOLD_FILE, boldBase64)
  doc.addFont(EDITABLE_PDF_REGULAR_FILE, EDITABLE_PDF_FONT, 'normal')
  doc.addFont(EDITABLE_PDF_BOLD_FILE, EDITABLE_PDF_FONT, 'bold')
}

/** Return unique unsupported Unicode code points in the text, preserving their first-seen order. */
export function missingPdfGlyphs(doc, textValues) {
  const font = doc.internal.getFont(EDITABLE_PDF_FONT, 'normal')
  const codeMap = font?.metadata?.cmap?.unicode?.codeMap
  if (!codeMap) throw new Error('The embedded PDF font does not expose a Unicode character map.')

  const missing = new Set()
  for (const text of textValues) {
    for (const character of String(text ?? '')) {
      if (/^[\u0000-\u001f\u007f]$/.test(character)) continue
      const codePoint = character.codePointAt(0)
      if (codeMap[codePoint] === undefined || codeMap[codePoint] === 0) missing.add(character)
    }
  }
  return [...missing]
}

/** Convert fetched font bytes to a base64 string without spreading a large array into a call. */
export function fontBytesToBase64(bytes) {
  const chunkSize = 0x8000
  const chunks = []
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    chunks.push(String.fromCharCode(...bytes.subarray(offset, offset + chunkSize)))
  }
  return btoa(chunks.join(''))
}
