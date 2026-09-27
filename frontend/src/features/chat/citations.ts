/**
 * Presentation-only citation parsing - see docs/FRONTEND_AI_CHAT.md,
 * "Citation implementation". This never re-validates or re-derives
 * citation authority; backend/app/sources/citations.py already decided
 * which `[Source N]` markers are valid before this code ever runs. This
 * module only decides how to *render* the text: an interactive element
 * for a marker that matches a real entry in the response's own `sources`
 * list, plain inert text otherwise. It never invents a source from a
 * number alone.
 *
 * Mirrors backend/app/sources/citations.py's exact regex
 * (`\[Source\s+(-?\d+)\]`, case-insensitive) so a citation this code
 * recognizes is always the same shape the backend recognizes.
 */

const CITATION_RE = /\[Source\s+(-?\d+)\]/gi
const SOURCE_ANCHOR_RE = /^#source-(-?\d+)$/

/**
 * Rewrites every `[Source N]` occurrence in raw markdown text into a
 * markdown link pointing at a `#source-N` fragment, so the markdown
 * renderer's own link element (already a safe, well-supported extension
 * point) can be overridden to render it as a citation button. Nothing
 * else about the text is touched - nested/malformed markdown around a
 * citation marker is preserved as-is (a known, documented limitation for
 * the rare case where injected content already contains link-shaped text
 * immediately following a citation marker - see docs/FRONTEND_AI_CHAT.md,
 * "Known limitations").
 */
export function linkifyCitations(text: string): string {
  return text.replace(CITATION_RE, (match, num: string) => `[${match.slice(1, -1)}](#source-${num})`)
}

/** Extracts N from a `#source-N` fragment href, or null if the href isn't
 * shaped like one at all (a genuine external link). */
export function parseSourceAnchor(href: string): number | null {
  const match = SOURCE_ANCHOR_RE.exec(href)
  if (!match) return null
  return Number.parseInt(match[1], 10)
}

/** Only http(s) links are ever rendered as a real, clickable anchor -
 * never `javascript:`, `data:`, or any other scheme a hostile document or
 * an injected instruction might try to produce (docs/FRONTEND_AI_CHAT.md,
 * "Security", section on source URLs). */
export function isSafeExternalUrl(href: string): boolean {
  try {
    const url = new URL(href, window.location.origin)
    return url.protocol === 'http:' || url.protocol === 'https:'
  } catch {
    return false
  }
}
