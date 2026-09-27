import type { ComponentPropsWithoutRef } from 'react'
import ReactMarkdown from 'react-markdown'
import type { Components } from 'react-markdown'
import type { SourceReference } from '../../../lib/api'
import { isSafeExternalUrl, linkifyCitations, parseSourceAnchor } from '../citations'
import type { ChatMessage as ChatMessageModel } from '../types'

interface ChatMessageProps {
  message: ChatMessageModel
  /** Only meaningful for an assistant message. */
  selectedSourceNumber: number | null
  onSelectSource: (sourceNumber: number) => void
  onRetry?: () => void
}

/** react-markdown never interprets raw HTML in its input as real elements
 * (no `rehype-raw`/`rehype-sanitize` plugin is used here) - a literal
 * `<script>`/`<img onerror=...>` in model output renders as inert text,
 * never executes. See docs/FRONTEND_AI_CHAT.md, "Security". */
export function ChatMessageItem({ message, selectedSourceNumber, onSelectSource, onRetry }: ChatMessageProps) {
  if (message.role === 'user') {
    return (
      <div className="chat-message chat-message--user" data-testid="chat-message-user">
        <span className="chat-message-role">You</span>
        <p className="chat-message-content chat-message-content--plain">{message.content}</p>
      </div>
    )
  }

  if (message.pending) {
    return (
      <div className="chat-message chat-message--assistant chat-message--pending" aria-live="polite">
        <span className="chat-message-role">CareSphere AI</span>
        <p className="chat-message-content chat-typing" data-testid="chat-typing-indicator">
          CareSphere is thinking
          <span className="chat-typing-dots" aria-hidden="true">
            <span />
            <span />
            <span />
          </span>
        </p>
      </div>
    )
  }

  if (message.failed) {
    return (
      <div className="chat-message chat-message--assistant chat-message--error" role="alert">
        <span className="chat-message-role">CareSphere AI</span>
        <p className="chat-message-content">{message.content}</p>
        {onRetry && (
          <button type="button" className="chat-retry-button" onClick={onRetry}>
            Try again
          </button>
        )}
      </div>
    )
  }

  const sourcesByNumber = new Map(message.sources.map((source) => [source.number, source]))
  const components: Components = {
    a: (props) => renderMarkdownLink(props, sourcesByNumber, selectedSourceNumber, onSelectSource),
  }

  return (
    <div className="chat-message chat-message--assistant" data-testid="chat-message-assistant">
      <span className="chat-message-role">CareSphere AI</span>
      <div className="chat-message-content">
        <ReactMarkdown components={components}>{linkifyCitations(message.content)}</ReactMarkdown>
      </div>
      {message.sources.length > 0 && (
        <div className="chat-message-sources" aria-label="Sources referenced in this answer">
          {message.sources.map((source) => (
            <button
              key={source.id}
              type="button"
              className={`citation-chip${selectedSourceNumber === source.number ? ' citation-chip--active' : ''}`}
              onClick={() => onSelectSource(source.number)}
              aria-label={`View Source ${source.number}: ${source.label}`}
            >
              Source {source.number}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function renderMarkdownLink(
  { href, children }: ComponentPropsWithoutRef<'a'>,
  sourcesByNumber: Map<number, SourceReference>,
  selectedSourceNumber: number | null,
  onSelectSource: (sourceNumber: number) => void,
) {
  if (href) {
    const sourceNumber = parseSourceAnchor(href)
    if (sourceNumber !== null) {
      const source = sourcesByNumber.get(sourceNumber)
      if (source) {
        return (
          <button
            type="button"
            className={`citation-ref${selectedSourceNumber === sourceNumber ? ' citation-ref--active' : ''}`}
            onClick={() => onSelectSource(sourceNumber)}
            aria-label={`View Source ${sourceNumber}`}
          >
            {children}
          </button>
        )
      }
      // A citation-shaped marker with no matching entry in `sources` -
      // e.g. a hallucinated [Source 999]. Never invented, never
      // interactive - see docs/FRONTEND_AI_CHAT.md, "Source authority".
      return (
        <span className="citation-ref citation-ref--invalid" title="This reference could not be verified">
          {children}
        </span>
      )
    }
    if (isSafeExternalUrl(href)) {
      return (
        <a href={href} target="_blank" rel="noopener noreferrer">
          {children}
        </a>
      )
    }
  }
  // An unsafe or unrecognized scheme (javascript:, data:, ...) - render
  // the link text only, never a navigable element.
  return <span>{children}</span>
}
