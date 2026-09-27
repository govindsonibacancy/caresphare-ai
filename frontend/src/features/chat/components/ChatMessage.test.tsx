import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { SourceReference } from '../../../lib/api'
import type { ChatMessage } from '../types'
import { ChatMessageItem } from './ChatMessage'

function makeSource(overrides: Partial<SourceReference> = {}): SourceReference {
  return {
    number: 1,
    id: 'source-1',
    type: 'document',
    label: 'Hospital Policy',
    document_id: 'doc-1',
    document_title: 'Infection Control Policy',
    document_type: 'HOSPITAL_POLICY',
    chunk_id: 'chunk-1',
    page: 2,
    section: 'Training',
    ...overrides,
  }
}

const noop = () => {}

describe('ChatMessageItem - user messages', () => {
  it('renders user content as plain text, never executing embedded HTML', () => {
    const message: ChatMessage = {
      id: '1',
      role: 'user',
      content: "<script>window.__xss = true</script> what's my policy?",
      sources: [],
    }
    render(<ChatMessageItem message={message} selectedSourceNumber={null} onSelectSource={noop} />)
    expect(screen.getByTestId('chat-message-user')).toHaveTextContent('<script>window.__xss = true</script>')
    expect((window as unknown as { __xss?: boolean }).__xss).toBeUndefined()
    expect(document.querySelector('script')).toBeNull()
  })
})

describe('ChatMessageItem - assistant messages', () => {
  it('renders markdown formatting', () => {
    const message: ChatMessage = { id: '1', role: 'assistant', content: 'This is **bold** text.', sources: [] }
    render(<ChatMessageItem message={message} selectedSourceNumber={null} onSelectSource={noop} />)
    const strong = screen.getByText('bold')
    expect(strong.tagName).toBe('STRONG')
  })

  it('never executes raw HTML in the model output', () => {
    const message: ChatMessage = {
      id: '1',
      role: 'assistant',
      content: '<img src=x onerror="window.__xss2 = true">Some answer text.',
      sources: [],
    }
    render(<ChatMessageItem message={message} selectedSourceNumber={null} onSelectSource={noop} />)
    expect(document.querySelector('img')).toBeNull()
    expect((window as unknown as { __xss2?: boolean }).__xss2).toBeUndefined()
  })

  it('renders a valid citation as a clickable element matching the source', async () => {
    const source = makeSource()
    const message: ChatMessage = {
      id: '1',
      role: 'assistant',
      content: 'Policy requires annual training. [Source 1]',
      sources: [source],
    }
    const onSelectSource = vi.fn()
    render(<ChatMessageItem message={message} selectedSourceNumber={null} onSelectSource={onSelectSource} />)
    const citation = screen.getByRole('button', { name: 'View Source 1' })
    await userEvent.click(citation)
    expect(onSelectSource).toHaveBeenCalledWith(1)
  })

  it('does not invent a source for an unmatched citation number', () => {
    const source = makeSource({ number: 1 })
    const message: ChatMessage = {
      id: '1',
      role: 'assistant',
      content: 'Policy requires annual training. [Source 999]',
      sources: [source],
    }
    render(<ChatMessageItem message={message} selectedSourceNumber={null} onSelectSource={noop} />)
    expect(screen.queryByRole('button', { name: /View Source 999/ })).toBeNull()
    const invalidRef = screen.getByText('Source 999')
    expect(invalidRef.tagName).toBe('SPAN')
    expect(invalidRef).toHaveClass('citation-ref--invalid')
  })

  it('renders normally when there are no sources at all', () => {
    const message: ChatMessage = { id: '1', role: 'assistant', content: 'A plain answer with no citations.', sources: [] }
    render(<ChatMessageItem message={message} selectedSourceNumber={null} onSelectSource={noop} />)
    expect(screen.getByText('A plain answer with no citations.')).toBeInTheDocument()
    expect(screen.queryByLabelText('Sources referenced in this answer')).toBeNull()
  })

  it('shows a pending/typing state', () => {
    const message: ChatMessage = { id: '1', role: 'assistant', content: '', sources: [], pending: true }
    render(<ChatMessageItem message={message} selectedSourceNumber={null} onSelectSource={noop} />)
    expect(screen.getByTestId('chat-typing-indicator')).toBeInTheDocument()
  })

  it('shows a safe failed-message state with a retry action', async () => {
    const message: ChatMessage = {
      id: '1',
      role: 'assistant',
      content: 'Something went wrong. Please try again.',
      sources: [],
      failed: true,
    }
    const onRetry = vi.fn()
    render(<ChatMessageItem message={message} selectedSourceNumber={null} onSelectSource={noop} onRetry={onRetry} />)
    expect(screen.getByRole('alert')).toHaveTextContent('Something went wrong')
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(onRetry).toHaveBeenCalled()
  })
})
