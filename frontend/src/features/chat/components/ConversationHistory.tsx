import type { ConversationSummary } from '../../../lib/api'
import { groupConversationsByRecency } from '../groupConversations'

interface ConversationHistoryProps {
  conversations: ConversationSummary[]
  activeConversationId: string | null
  loading: boolean
  error: string | null
  onSelect: (conversationId: string) => void
  onNewChat: () => void
  onRetry: () => void
}

/**
 * Conversation history list - summary data only (id/title/timestamps),
 * exactly what `GET /api/conversations` returns; never fetches or holds
 * message content for conversations the user hasn't opened (see
 * docs/CONVERSATION_HISTORY.md, "History data").
 */
export function ConversationHistory({
  conversations,
  activeConversationId,
  loading,
  error,
  onSelect,
  onNewChat,
  onRetry,
}: ConversationHistoryProps) {
  const groups = groupConversationsByRecency(conversations)

  return (
    <nav className="chat-history" aria-label="Conversation history">
      <button type="button" className="chat-history-new" onClick={onNewChat}>
        + New chat
      </button>

      {loading && <p className="chat-history-status">Loading conversations...</p>}

      {!loading && error && (
        <div className="chat-history-status chat-history-error" role="alert">
          <p>{error}</p>
          <button type="button" className="chat-retry-button" onClick={onRetry}>
            Try again
          </button>
        </div>
      )}

      {!loading && !error && conversations.length === 0 && (
        <div className="chat-history-status">
          <p>No conversations yet.</p>
          <p className="chat-empty-hint">Start a new conversation with CareSphere AI.</p>
        </div>
      )}

      {!loading &&
        !error &&
        groups.map((group) => (
          <div key={group.label} className="chat-history-group">
            <h2 className="chat-history-group-label">{group.label}</h2>
            <ul className="chat-history-list">
              {group.conversations.map((conversation) => (
                <li key={conversation.id}>
                  <button
                    type="button"
                    className={`chat-history-item${conversation.id === activeConversationId ? ' chat-history-item--active' : ''}`}
                    onClick={() => onSelect(conversation.id)}
                    aria-current={conversation.id === activeConversationId ? 'true' : undefined}
                  >
                    {conversation.title ?? 'New conversation'}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ))}
    </nav>
  )
}
