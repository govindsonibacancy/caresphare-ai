import { type FormEvent, type KeyboardEvent, useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  AiRequestError,
  type AiAnswerResponse,
  type ConversationDetail,
  type ConversationMessage as ApiConversationMessage,
  type ConversationSummary,
  type SourceReference,
  askAi,
  getConversation,
  listConversations,
} from '../../../lib/api'
import { ChatMessageItem } from '../components/ChatMessage'
import { ConversationHistory } from '../components/ConversationHistory'
import { SourcePanel } from '../components/SourcePanel'
import type { ChatMessage } from '../types'

const SUGGESTED_QUESTIONS = [
  'What appointments do I have?',
  'What departments are available at this hospital?',
  'What is the hospital policy on visiting hours?',
]

let messageIdCounter = 0
function nextMessageId(): string {
  messageIdCounter += 1
  return `msg-${messageIdCounter}`
}

function userMessage(content: string): ChatMessage {
  return { id: nextMessageId(), role: 'user', content, sources: [] }
}
function pendingAssistantMessage(): ChatMessage {
  return { id: nextMessageId(), role: 'assistant', content: '', sources: [], pending: true }
}
function assistantMessageFrom(response: AiAnswerResponse): ChatMessage {
  return { id: nextMessageId(), role: 'assistant', content: response.answer, sources: response.sources }
}
function failedAssistantMessage(error: unknown): ChatMessage {
  const message = error instanceof AiRequestError ? error.message : 'Something went wrong. Please try again.'
  const requestId = error instanceof AiRequestError ? error.requestId : null
  return {
    id: nextMessageId(),
    role: 'assistant',
    content: requestId ? `${message}\n\nReference ID: ${requestId}` : message,
    sources: [],
    failed: true,
  }
}
function fromHistoryMessage(message: ApiConversationMessage): ChatMessage {
  return {
    id: message.id,
    role: message.role === 'USER' ? 'user' : 'assistant',
    content: message.content,
    sources: message.sources,
  }
}
function safeErrorMessage(error: unknown, fallback: string): string {
  return error instanceof AiRequestError ? error.message : fallback
}

/**
 * CareSphere AI's chat page. Consumes the existing, unmodified
 * POST /api/rag/answer contract plus (Phase 17) the read-only
 * GET /api/conversations[/:id] endpoints - this component makes no
 * authorization decisions and holds no clinical/business logic of its
 * own; every answer, source, and historical message shown here is
 * exactly what the backend already decided to authorize and return. See
 * docs/FRONTEND_AI_CHAT.md and docs/CONVERSATION_HISTORY.md.
 *
 * The URL (`/chat` or `/chat/:conversationId`) is the source of truth for
 * which conversation is active - never localStorage, never component
 * state alone - so a refresh or a direct link restores the same
 * conversation via a fresh, ownership-checked backend fetch.
 */
export function ChatPage() {
  const { conversationId: urlConversationId } = useParams<{ conversationId?: string }>()
  const navigate = useNavigate()

  const [conversationId, setConversationId] = useState<string | null>(urlConversationId ?? null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)

  const [conversations, setConversations] = useState<ConversationSummary[]>([])
  const [historyLoading, setHistoryLoading] = useState(true)
  const [historyError, setHistoryError] = useState<string | null>(null)
  const [isHistoryOpen, setIsHistoryOpen] = useState(false)

  const [input, setInput] = useState('')
  const [isSending, setIsSending] = useState(false)
  const [lastQuery, setLastQuery] = useState<string | null>(null)
  const [selectedSourceNumber, setSelectedSourceNumber] = useState<number | null>(null)

  const inputRef = useRef<HTMLTextAreaElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const shouldAutoScrollRef = useRef(true)
  const skipNextLoadRef = useRef(false)

  const loadHistory = useCallback(() => {
    setHistoryLoading(true)
    setHistoryError(null)
    listConversations()
      .then((page) => setConversations(page.items))
      .catch((error) => setHistoryError(safeErrorMessage(error, 'Could not load your conversation history.')))
      .finally(() => setHistoryLoading(false))
  }, [])

  useEffect(() => {
    loadHistory()
  }, [loadHistory])

  // The active conversation always follows the URL - see this component's
  // own docstring. Skipped exactly once, right after this page itself
  // navigates to a brand-new conversation id it just received from
  // askAi() below, since it already has that conversation's one and only
  // turn in local state and re-fetching it would be a redundant request.
  useEffect(() => {
    if (skipNextLoadRef.current) {
      skipNextLoadRef.current = false
      return
    }
    if (!urlConversationId) {
      setConversationId(null)
      setMessages([])
      setDetailError(null)
      return
    }

    let cancelled = false
    setDetailLoading(true)
    setDetailError(null)
    getConversation(urlConversationId)
      .then((detail: ConversationDetail) => {
        if (cancelled) return
        setConversationId(detail.id)
        setMessages(detail.messages.map(fromHistoryMessage))
      })
      .catch((error) => {
        if (cancelled) return
        setDetailError(safeErrorMessage(error, 'This conversation could not be loaded.'))
        setMessages([])
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [urlConversationId])

  useEffect(() => {
    const el = listRef.current
    if (shouldAutoScrollRef.current && el && typeof el.scrollTo === 'function') {
      el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
    }
  }, [messages])

  function handleScroll() {
    const el = listRef.current
    if (!el) return
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
    shouldAutoScrollRef.current = distanceFromBottom < 120
  }

  async function runQuery(query: string, { appendUserMessage }: { appendUserMessage: boolean }) {
    if (isSending || detailLoading || !query.trim()) return
    setIsSending(true)
    setLastQuery(query)
    shouldAutoScrollRef.current = true

    setMessages((prev) => {
      const withoutTrailingFailure = !appendUserMessage && prev.at(-1)?.failed ? prev.slice(0, -1) : prev
      const withUser = appendUserMessage ? [...withoutTrailingFailure, userMessage(query)] : withoutTrailingFailure
      return [...withUser, pendingAssistantMessage()]
    })

    try {
      const response = await askAi({ query, conversation_id: conversationId })
      setMessages((prev) => [...prev.slice(0, -1), assistantMessageFrom(response)])
      if (response.conversation_id && response.conversation_id !== conversationId) {
        skipNextLoadRef.current = true
        setConversationId(response.conversation_id)
        navigate(`/chat/${response.conversation_id}`, { replace: true })
      }
      loadHistory() // reflect the new/updated conversation (title, recency order) in the sidebar
    } catch (error) {
      setMessages((prev) => [...prev.slice(0, -1), failedAssistantMessage(error)])
    } finally {
      setIsSending(false)
    }
  }

  function submitInput() {
    const query = input
    if (!query.trim() || isSending) return
    setInput('')
    void runQuery(query, { appendUserMessage: true })
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    submitInput()
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      submitInput()
    }
  }

  function handleRetry() {
    if (!lastQuery) return
    void runQuery(lastQuery, { appendUserMessage: false })
  }

  function handleNewChat() {
    setSelectedSourceNumber(null)
    setLastQuery(null)
    setIsHistoryOpen(false)
    navigate('/chat') // the URL-driven effect above resets messages/conversationId
    inputRef.current?.focus()
  }

  function handleSelectConversation(id: string) {
    setSelectedSourceNumber(null)
    setLastQuery(null)
    setIsHistoryOpen(false)
    navigate(`/chat/${id}`)
  }

  function handleSuggestion(question: string) {
    if (isSending) return
    void runQuery(question, { appendUserMessage: true })
  }

  const allSources: SourceReference[] = messages.flatMap((m) => m.sources)
  const selectedSource = allSources.find((s) => s.number === selectedSourceNumber) ?? null
  const composerDisabled = isSending || detailLoading

  return (
    <section className="page chat-page">
      <header className="chat-header">
        <div className="chat-header-title">
          <button
            type="button"
            className="chat-history-toggle"
            onClick={() => setIsHistoryOpen((open) => !open)}
            aria-expanded={isHistoryOpen}
            aria-controls="chat-history-panel"
          >
            History
          </button>
          <div>
            <h1>CareSphere AI</h1>
            <p className="chat-subtitle">Your intelligent healthcare data assistant</p>
          </div>
        </div>
        <button type="button" className="chat-new-button" onClick={handleNewChat}>
          New chat
        </button>
      </header>

      <p className="chat-disclaimer">
        CareSphere AI answers questions using information your account is authorized to see. It is an information
        assistant, not a doctor - it does not diagnose, prescribe, or provide emergency medical advice.
      </p>

      <div className="chat-body">
        <div id="chat-history-panel" className={`chat-history-wrapper${isHistoryOpen ? ' chat-history-wrapper--open' : ''}`}>
          <ConversationHistory
            conversations={conversations}
            activeConversationId={conversationId}
            loading={historyLoading}
            error={historyError}
            onSelect={handleSelectConversation}
            onNewChat={handleNewChat}
            onRetry={loadHistory}
          />
        </div>

        <div className="chat-panel">
          <div className="chat-message-list" ref={listRef} onScroll={handleScroll}>
            {detailLoading ? (
              <p className="chat-history-status">Loading conversation...</p>
            ) : detailError ? (
              <div className="chat-history-status chat-history-error" role="alert">
                <p>{detailError}</p>
                <button type="button" className="chat-retry-button" onClick={() => navigate('/chat')}>
                  Back to new chat
                </button>
              </div>
            ) : messages.length === 0 ? (
              <div className="chat-empty-state">
                <p>How can I help?</p>
                <p className="chat-empty-hint">
                  Ask about your appointments, hospital policies, departments, or other information you're
                  authorized to access.
                </p>
                <div className="chat-suggestions">
                  {SUGGESTED_QUESTIONS.map((question) => (
                    <button
                      key={question}
                      type="button"
                      className="chat-suggestion"
                      onClick={() => handleSuggestion(question)}
                    >
                      {question}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              messages.map((message, index) => {
                const isLast = index === messages.length - 1
                const item = (
                  <ChatMessageItem
                    message={message}
                    selectedSourceNumber={selectedSourceNumber}
                    onSelectSource={setSelectedSourceNumber}
                    onRetry={message.failed ? handleRetry : undefined}
                  />
                )
                return isLast ? (
                  <div key={message.id} aria-live="polite">
                    {item}
                  </div>
                ) : (
                  <div key={message.id}>{item}</div>
                )
              })
            )}
          </div>

          <form className="chat-input-form" onSubmit={handleSubmit}>
            <label htmlFor="chat-input" className="sr-only">
              Ask CareSphere AI a question
            </label>
            <textarea
              id="chat-input"
              ref={inputRef}
              className="chat-input"
              placeholder="Ask CareSphere..."
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              rows={1}
              disabled={composerDisabled}
            />
            <button type="submit" className="chat-send-button" disabled={composerDisabled || !input.trim()}>
              {isSending ? 'Sending...' : 'Send'}
            </button>
          </form>
        </div>

        {selectedSource && <SourcePanel source={selectedSource} onClose={() => setSelectedSourceNumber(null)} />}
      </div>
    </section>
  )
}
