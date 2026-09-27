import type { ConversationSummary } from '../../lib/api'

export interface ConversationGroup {
  label: string
  conversations: ConversationSummary[]
}

function startOfDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime()
}

/**
 * Groups conversations already ordered by the backend (`last_activity_at
 * DESC` - see docs/CONVERSATION_HISTORY.md, "History order") into
 * "Today"/"Yesterday"/"Previous" buckets for display - a presentation
 * grouping only, never re-sorting what the backend already ordered.
 */
export function groupConversationsByRecency(
  conversations: ConversationSummary[],
  now: Date = new Date(),
): ConversationGroup[] {
  const today = startOfDay(now)
  const yesterday = today - 24 * 60 * 60 * 1000

  const groups: ConversationGroup[] = [
    { label: 'Today', conversations: [] },
    { label: 'Yesterday', conversations: [] },
    { label: 'Previous', conversations: [] },
  ]

  for (const conversation of conversations) {
    const activityDay = startOfDay(new Date(conversation.last_activity_at))
    if (activityDay === today) {
      groups[0].conversations.push(conversation)
    } else if (activityDay === yesterday) {
      groups[1].conversations.push(conversation)
    } else {
      groups[2].conversations.push(conversation)
    }
  }

  return groups.filter((group) => group.conversations.length > 0)
}
