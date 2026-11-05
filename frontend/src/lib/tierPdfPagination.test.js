import test from 'node:test'
import assert from 'node:assert/strict'
import { chunkTierItems, tierPdfPageCapacity, tierPdfRemainingCapacity } from './tierPdfPagination.js'

test('A4 portrait geometry fits six tile lines per page', () => {
  assert.equal(tierPdfPageCapacity(842, 40, 113.5, 6), 36)
})

test('first tier can fill the space left after the PDF header', () => {
  assert.equal(tierPdfRemainingCapacity(842, 40, 110, 113.5, 6, 32), 30)
  assert.equal(tierPdfRemainingCapacity(842, 40, 780, 113.5, 6, 32), 0)
  assert.equal(tierPdfRemainingCapacity(842, 40, 110, 113.5, 6, 740), 0)
})

test('keeps an empty tier as one empty page segment', () => {
  assert.deepEqual(chunkTierItems([], 6), [[]])
})

test('keeps a short tier together', () => {
  const items = ['a', 'b', 'c']
  assert.deepEqual(chunkTierItems(items, 6), [items])
})

test('splits a long tier into bounded ordered segments', () => {
  const items = Array.from({ length: 25 }, (_, index) => `item-${index + 1}`)
  const pages = chunkTierItems(items, 6)

  assert.deepEqual(pages.map(page => page.length), [6, 6, 6, 6, 1])
  assert.deepEqual(pages.flat(), items)
  assert.ok(pages.every(page => page.length <= 6))
})

test('rejects a zero or fractional capacity', () => {
  assert.throws(() => chunkTierItems([], 0), RangeError)
  assert.throws(() => chunkTierItems([], 1.5), RangeError)
  assert.throws(() => tierPdfPageCapacity(842, 40, 0, 6), RangeError)
})
