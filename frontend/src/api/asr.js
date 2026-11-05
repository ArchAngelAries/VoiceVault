import { BACKEND_URL } from '@/api/client'
import { postAudioForTranscript } from '@/components/converse/audioInput'

export const transcribeAudioFile = (file, options = {}) => postAudioForTranscript(file, {
  endpoint: `${BACKEND_URL}/api/asr/transcribe`,
  ...options,
})
