/**
 * data/tierColors.js — Row colours and the labels for the item groups on a tier board.
 * The first four rows of the default template are S, A, B and C, in the colours of the owner's reference board.
 */

export const DEFAULT_ROWS = [
  { label: 'S', color: '#ff7f7f' },
  { label: 'A', color: '#ffbf7f' },
  { label: 'B', color: '#ffdf7f' },
  { label: 'C', color: '#ffff7f' },
]

// Swatches offered when a row's colour is changed, in rainbow order, then neutrals.
export const ROW_PALETTE = [
  '#ff7f7f', '#ffbf7f', '#ffdf7f', '#ffff7f', '#bfff7f', '#7fff7f',
  '#7fffff', '#7fbfff', '#bf7fff', '#ff7fff', '#c4c4c4', '#6b6b7d',
]

export const CATEGORY_LABEL = {
  anime: 'Anime',
  movie: 'Movies',
  tv: 'TV',
  game: 'Games',
  character: 'Characters',
  person: 'People',
  image: 'Imported images',
}

// Order of the groups in the pool (imported images have their own warehouse section).
export const CATEGORY_ORDER = ['anime', 'movie', 'tv', 'game', 'character', 'person']

// Dark text on light colours, light text on dark ones (the same rule the rows and the export use)
export const textOn = (hex) => {
  const [r, g, b] = hexToRgb(hex)
  return (0.299 * r + 0.587 * g + 0.114 * b) > 150 ? '#14141c' : '#f4f1fa'
}

export function hexToRgb(hex) {
  const n = parseInt(hex.slice(1), 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}
