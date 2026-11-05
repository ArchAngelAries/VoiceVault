import regularFontUrl from '@/assets/fonts/VoiceVaultPdfJP-Regular.ttf?url'
import boldFontUrl from '@/assets/fonts/VoiceVaultPdfJP-Bold.ttf?url'
import { fontBytesToBase64 } from './editablePdfFont'

let fontPromise

export function loadEditablePdfFonts() {
  if (!fontPromise) {
    fontPromise = Promise.all([regularFontUrl, boldFontUrl].map(async url => {
      const response = await fetch(url)
      if (!response.ok) throw new Error(`Could not load the embedded PDF font (${response.status}).`)
      return fontBytesToBase64(new Uint8Array(await response.arrayBuffer()))
    }))
      .catch(error => {
        fontPromise = null
        throw error
      })
  }
  return fontPromise
}
