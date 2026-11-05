const INTERACTIVE_SELECTOR = 'button, input, select, textarea, summary, a, [contenteditable="true"], [role="button"]'

export function isInteractiveTarget(target, currentTarget) {
  if (target === currentTarget) return false
  const interactive = target.closest?.(INTERACTIVE_SELECTOR)
  return Boolean(interactive && interactive !== currentTarget && currentTarget.contains(interactive))
}
