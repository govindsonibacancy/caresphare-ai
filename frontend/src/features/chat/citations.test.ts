import { describe, expect, it } from 'vitest'
import { isSafeExternalUrl, linkifyCitations, parseSourceAnchor } from './citations'

describe('linkifyCitations', () => {
  it('turns a valid citation marker into a markdown link to a source anchor', () => {
    expect(linkifyCitations('Policy requires annual training. [Source 1]')).toBe(
      'Policy requires annual training. [Source 1](#source-1)',
    )
  })

  it('handles multiple citations', () => {
    expect(linkifyCitations('[Source 1] and [Source 2]')).toBe('[Source 1](#source-1) and [Source 2](#source-2)')
  })

  it('is case-insensitive, matching the backend regex exactly', () => {
    expect(linkifyCitations('see [source 3]')).toBe('see [source 3](#source-3)')
  })

  it('leaves text with no citation markers unchanged', () => {
    expect(linkifyCitations('No citations here.')).toBe('No citations here.')
  })
})

describe('parseSourceAnchor', () => {
  it('extracts the source number from a source anchor href', () => {
    expect(parseSourceAnchor('#source-1')).toBe(1)
    expect(parseSourceAnchor('#source-42')).toBe(42)
  })

  it('returns null for a non-source href', () => {
    expect(parseSourceAnchor('https://example.com')).toBeNull()
    expect(parseSourceAnchor('#something-else')).toBeNull()
  })
})

describe('isSafeExternalUrl', () => {
  it('allows http and https', () => {
    expect(isSafeExternalUrl('https://example.com')).toBe(true)
    expect(isSafeExternalUrl('http://example.com')).toBe(true)
  })

  it('rejects javascript: and data: schemes', () => {
    expect(isSafeExternalUrl("javascript:alert('xss')")).toBe(false)
    expect(isSafeExternalUrl('data:text/html,<script>alert(1)</script>')).toBe(false)
  })
})
