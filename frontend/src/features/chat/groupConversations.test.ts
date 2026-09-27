import { describe, expect, it } from 'vitest'
import type { ConversationSummary } from '../../lib/api'
import { groupConversationsByRecency } from './groupConversations'

const NOW = new Date('2026-10-05T12:00:00Z')

function summary(id: string, lastActivityAt: string): ConversationSummary {
  return { id, title: `Conversation ${id}`, created_at: lastActivityAt, updated_at: lastActivityAt, last_activity_at: lastActivityAt }
}

describe('groupConversationsByRecency', () => {
  it('groups conversations into Today/Yesterday/Previous', () => {
    const conversations = [
      summary('today', '2026-10-05T09:00:00Z'),
      summary('yesterday', '2026-10-04T09:00:00Z'),
      summary('older', '2026-09-01T09:00:00Z'),
    ]
    const groups = groupConversationsByRecency(conversations, NOW)
    expect(groups.map((g) => g.label)).toEqual(['Today', 'Yesterday', 'Previous'])
    expect(groups[0].conversations.map((c) => c.id)).toEqual(['today'])
    expect(groups[1].conversations.map((c) => c.id)).toEqual(['yesterday'])
    expect(groups[2].conversations.map((c) => c.id)).toEqual(['older'])
  })

  it('omits empty groups', () => {
    const conversations = [summary('today', '2026-10-05T09:00:00Z')]
    const groups = groupConversationsByRecency(conversations, NOW)
    expect(groups).toHaveLength(1)
    expect(groups[0].label).toBe('Today')
  })

  it('preserves the backend-provided order within a group', () => {
    const conversations = [summary('b', '2026-10-05T10:00:00Z'), summary('a', '2026-10-05T08:00:00Z')]
    const groups = groupConversationsByRecency(conversations, NOW)
    expect(groups[0].conversations.map((c) => c.id)).toEqual(['b', 'a']) // not re-sorted
  })

  it('returns no groups for an empty list', () => {
    expect(groupConversationsByRecency([], NOW)).toEqual([])
  })
})
