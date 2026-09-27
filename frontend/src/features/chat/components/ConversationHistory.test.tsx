import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ConversationSummary } from '../../../lib/api'
import { ConversationHistory } from './ConversationHistory'

const NOOP = () => {}

function summary(overrides: Partial<ConversationSummary> = {}): ConversationSummary {
  const now = new Date().toISOString()
  return { id: 'conv-1', title: 'A conversation', created_at: now, updated_at: now, last_activity_at: now, ...overrides }
}

describe('ConversationHistory', () => {
  it('shows a loading state', () => {
    render(
      <ConversationHistory
        conversations={[]}
        activeConversationId={null}
        loading={true}
        error={null}
        onSelect={NOOP}
        onNewChat={NOOP}
        onRetry={NOOP}
      />,
    )
    expect(screen.getByText('Loading conversations...')).toBeInTheDocument()
  })

  it('shows an empty state with no conversations', () => {
    render(
      <ConversationHistory
        conversations={[]}
        activeConversationId={null}
        loading={false}
        error={null}
        onSelect={NOOP}
        onNewChat={NOOP}
        onRetry={NOOP}
      />,
    )
    expect(screen.getByText('No conversations yet.')).toBeInTheDocument()
  })

  it('shows an error state with a retry action', async () => {
    const onRetry = vi.fn()
    const user = userEvent.setup()
    render(
      <ConversationHistory
        conversations={[]}
        activeConversationId={null}
        loading={false}
        error="Could not load your conversation history."
        onSelect={NOOP}
        onNewChat={NOOP}
        onRetry={onRetry}
      />,
    )
    expect(screen.getByRole('alert')).toHaveTextContent('Could not load your conversation history.')
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(onRetry).toHaveBeenCalled()
  })

  it('renders conversations and highlights the active one', () => {
    const conversations = [summary({ id: 'conv-1', title: 'First conversation' }), summary({ id: 'conv-2', title: 'Second conversation' })]
    render(
      <ConversationHistory
        conversations={conversations}
        activeConversationId="conv-2"
        loading={false}
        error={null}
        onSelect={NOOP}
        onNewChat={NOOP}
        onRetry={NOOP}
      />,
    )
    const active = screen.getByRole('button', { name: 'Second conversation' })
    expect(active).toHaveClass('chat-history-item--active')
    expect(active).toHaveAttribute('aria-current', 'true')
    expect(screen.getByRole('button', { name: 'First conversation' })).not.toHaveClass('chat-history-item--active')
  })

  it('falls back to a generic label when a conversation has no title yet', () => {
    render(
      <ConversationHistory
        conversations={[summary({ title: null })]}
        activeConversationId={null}
        loading={false}
        error={null}
        onSelect={NOOP}
        onNewChat={NOOP}
        onRetry={NOOP}
      />,
    )
    expect(screen.getByRole('button', { name: 'New conversation' })).toBeInTheDocument()
  })

  it('calls onSelect when a conversation is clicked', async () => {
    const onSelect = vi.fn()
    const user = userEvent.setup()
    render(
      <ConversationHistory
        conversations={[summary({ id: 'conv-9', title: 'Pick me' })]}
        activeConversationId={null}
        loading={false}
        error={null}
        onSelect={onSelect}
        onNewChat={NOOP}
        onRetry={NOOP}
      />,
    )
    await user.click(screen.getByRole('button', { name: 'Pick me' }))
    expect(onSelect).toHaveBeenCalledWith('conv-9')
  })

  it('calls onNewChat when "+ New chat" is clicked', async () => {
    const onNewChat = vi.fn()
    const user = userEvent.setup()
    render(
      <ConversationHistory
        conversations={[]}
        activeConversationId={null}
        loading={false}
        error={null}
        onSelect={NOOP}
        onNewChat={onNewChat}
        onRetry={NOOP}
      />,
    )
    await user.click(screen.getByRole('button', { name: '+ New chat' }))
    expect(onNewChat).toHaveBeenCalled()
  })
})
