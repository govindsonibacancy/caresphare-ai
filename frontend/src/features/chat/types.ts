import type { SourceReference } from '../../lib/api'

export type ChatRole = 'user' | 'assistant'

/**
 * One rendered chat bubble. This is frontend-only presentation state - the
 * backend conversation (`conversation_messages`, Phase 13) is the actual
 * source of truth and is never read back from here (see
 * docs/FRONTEND_AI_CHAT.md, "Conversation handling"). `id` is a
 * client-generated key for React's list rendering only, never sent to the
 * backend and never a substitute for anything backend-assigned.
 */
export interface ChatMessage {
  id: string
  role: ChatRole
  content: string
  /** Only ever populated for `role: 'assistant'`. */
  sources: SourceReference[]
  /** True while this message represents a request still in flight (the
   * assistant's "thinking" placeholder) - see docs/FRONTEND_AI_CHAT.md,
   * "Loading state". */
  pending?: boolean
  /** True if this message represents a failed request - rendered as a
   * safe error bubble, never a raw exception. */
  failed?: boolean
}
