/** Split ranked items into page-sized groups without changing their order. */
export function tierPdfPageCapacity(pageHeight, margin, lineStride, itemsPerLine) {
  if (![pageHeight, margin, lineStride, itemsPerLine].every(Number.isFinite)
      || pageHeight <= 2 * margin || lineStride <= 0 || !Number.isInteger(itemsPerLine) || itemsPerLine < 1) {
    throw new RangeError('Page dimensions and item layout must be positive numbers')
  }
  const availableLines = Math.max(1, Math.floor((pageHeight - 2 * margin - 8) / lineStride))
  return availableLines * itemsPerLine
}

/** Maximum tile count that fits below the current content position. */
export function tierPdfRemainingCapacity(pageHeight, bottomMargin, y, lineStride, itemsPerLine, labelHeight = 0) {
  if (![pageHeight, bottomMargin, y, lineStride, itemsPerLine, labelHeight].every(Number.isFinite)
      || pageHeight <= bottomMargin || bottomMargin < 0 || y < 0 || lineStride <= 0
      || !Number.isInteger(itemsPerLine) || itemsPerLine < 1 || labelHeight < 0) {
    throw new RangeError('Page dimensions and item layout must be positive numbers')
  }
  const available = pageHeight - bottomMargin - y
  const bottomGap = 6
  const rowPadding = 8
  if (labelHeight + bottomGap > available) return 0
  const lines = Math.floor((available - bottomGap - rowPadding) / lineStride)
  return Math.max(0, lines) * itemsPerLine
}

export function chunkTierItems(items, pageCapacity) {
  if (!Number.isInteger(pageCapacity) || pageCapacity < 1) {
    throw new RangeError('pageCapacity must be a positive integer')
  }
  if (items.length === 0) return [[]]
  const pages = []
  for (let start = 0; start < items.length; start += pageCapacity) {
    pages.push(items.slice(start, start + pageCapacity))
  }
  return pages
}
