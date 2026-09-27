import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AiAnswerResponse, ConversationDetail, ConversationSummary, Page } from '../../../lib/api'
import { ChatPage } from './ChatPage'

const { askAiMock, listConversationsMock, getConversationMock } = vi.hoisted(() => ({
  askAiMock: vi.fn(),
  listConversationsMock: vi.fn(),
  getConversationMock: vi.fn(),
}))

vi.mock('../../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../lib/api')>()
  return { ...actual, askAi: askAiMock, listConversations: listConversationsMock, getConversation: getConversationMock }
})

function page(items: ConversationSummary[]): Page<ConversationSummary> {
  return { items, page: 1, page_size: 50, total: items.length }
}

function summary(overrides: Partial<ConversationSummary> = {}): ConversationSummary {
  const now = new Date().toISOString()
  return { id: 'conv-1', title: 'A conversation', created_at: now, updated_at: now, last_activity_at: now, ...overrides }
}

function successResponse(overrides: Partial<AiAnswerResponse> = {}): AiAnswerResponse {
  return { answer: 'An answer.', sources: [], model: 'llama3.2:3b', conversation_id: 'conv-1', request_id: 'req-1', ...overrides }
}

function renderAt(initialPath: string) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/chat/:conversationId" element={<ChatPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  askAiMock.mockReset()
  listConversationsMock.mockReset()
  getConversationMock.mockReset()
  listConversationsMock.mockResolvedValue(page([]))
})

describe('ChatPage - conversation history', () => {
  it('renders the conversation list in the sidebar', async () => {
    listConversationsMock.mockResolvedValue(page([summary({ id: 'conv-1', title: 'Cardiology appointment' })]))
    renderAt('/chat')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Cardiology appointment' })).toBeInTheDocument())
  })

  it('shows a loading state for the history list', () => {
    listConversationsMock.mockReturnValue(new Promise(() => {})) // never resolves
    renderAt('/chat')
    expect(screen.getByText('Loading conversations...')).toBeInTheDocument()
  })

  it('shows an empty history state for a user with no conversations', async () => {
    renderAt('/chat')
    await waitFor(() => expect(screen.getByText('No conversations yet.')).toBeInTheDocument())
  })

  it('shows a history error state with retry', async () => {
    listConversationsMock.mockRejectedValueOnce(new Error('network down'))
    listConversationsMock.mockResolvedValueOnce(page([summary({ title: 'Recovered' })]))
    const user = userEvent.setup()
    renderAt('/chat')

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Recovered' })).toBeInTheDocument())
  })

  it('loads an existing conversation directly by URL (refresh/direct-link restoration)', async () => {
    const detail: ConversationDetail = {
      id: 'conv-77',
      title: 'Hospital visiting hours',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      last_activity_at: new Date().toISOString(),
      messages: [
        { id: 'm1', role: 'USER', content: 'What are visiting hours?', sources: [], created_at: new Date().toISOString() },
        {
          id: 'm2',
          role: 'ASSISTANT',
          content: 'Visiting hours are 9am-8pm. [Source 1]',
          sources: [
            {
              number: 1, id: 'source-1', type: 'document', label: 'Visiting Hours Policy',
              document_id: 'doc-1', document_title: 'Visiting Hours Policy', document_type: 'HOSPITAL_POLICY',
              chunk_id: 'chunk-1', page: 1, section: null,
            },
          ],
          created_at: new Date().toISOString(),
        },
      ],
    }
    getConversationMock.mockResolvedValueOnce(detail)

    renderAt('/chat/conv-77')

    expect(screen.getByText('Loading conversation...')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('What are visiting hours?')).toBeInTheDocument())
    expect(screen.getByText(/Visiting hours are 9am-8pm/)).toBeInTheDocument()
    expect(getConversationMock).toHaveBeenCalledWith('conv-77')

    // Historical sources render through the exact same citation UX.
    await userEvent.setup().click(screen.getByRole('button', { name: 'View Source 1' }))
    expect(screen.getByRole('heading', { name: 'Source 1' })).toBeInTheDocument()
    expect(screen.getAllByText('Visiting Hours Policy').length).toBeGreaterThan(0)
  })

  it('selecting a conversation from history navigates and loads its messages', async () => {
    listConversationsMock.mockResolvedValue(page([summary({ id: 'conv-5', title: 'Blood pressure question' })]))
    getConversationMock.mockResolvedValueOnce({
      id: 'conv-5',
      title: 'Blood pressure question',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      last_activity_at: new Date().toISOString(),
      messages: [{ id: 'm1', role: 'USER', content: 'What was my blood pressure?', sources: [], created_at: new Date().toISOString() }],
    })
    const user = userEvent.setup()
    renderAt('/chat')

    await waitFor(() => expect(screen.getByRole('button', { name: 'Blood pressure question' })).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Blood pressure question' }))

    await waitFor(() => expect(screen.getByText('What was my blood pressure?')).toBeInTheDocument())
    expect(getConversationMock).toHaveBeenCalledWith('conv-5')
    expect(screen.getByRole('button', { name: 'Blood pressure question' })).toHaveClass('chat-history-item--active')
  })

  it('shows a safe error and a way back to /chat for an unauthorized or nonexistent conversation', async () => {
    const { AiRequestError } = await import('../../../lib/api')
    getConversationMock.mockRejectedValueOnce(
      new AiRequestError('This conversation is no longer available. Start a new chat to continue.', 'not_found', 404, null),
    )
    renderAt('/chat/someone-elses-conversation')

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
    expect(screen.getByRole('alert')).toHaveTextContent('no longer available')
    // Never leaks anything about the conversation - only the safe message.
    expect(screen.queryByText(/someone-elses-conversation/)).toBeNull()
    expect(screen.getByRole('button', { name: 'Back to new chat' })).toBeInTheDocument()
  })

  it('new chat navigates away from an open conversation without deleting it from history', async () => {
    listConversationsMock.mockResolvedValue(page([summary({ id: 'conv-77', title: 'Hospital visiting hours' })]))
    getConversationMock.mockResolvedValueOnce({
      id: 'conv-77',
      title: 'Hospital visiting hours',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      last_activity_at: new Date().toISOString(),
      messages: [{ id: 'm1', role: 'USER', content: 'What are visiting hours?', sources: [], created_at: new Date().toISOString() }],
    })
    const user = userEvent.setup()
    renderAt('/chat/conv-77')
    await waitFor(() => expect(screen.getByText('What are visiting hours?')).toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: 'New chat' }))

    await waitFor(() => expect(screen.getByText('How can I help?')).toBeInTheDocument())
    // The conversation is still listed in history - New Chat never deletes anything.
    expect(screen.getByRole('button', { name: 'Hospital visiting hours' })).toBeInTheDocument()
  })

  it('refreshes the history list after a new conversation is created', async () => {
    askAiMock.mockResolvedValueOnce(successResponse({ conversation_id: 'conv-new' }))
    listConversationsMock.mockResolvedValueOnce(page([])) // initial mount
    listConversationsMock.mockResolvedValueOnce(page([summary({ id: 'conv-new', title: 'A question' })])) // after send
    const user = userEvent.setup()
    renderAt('/chat')

    await user.type(screen.getByLabelText('Ask CareSphere AI a question'), 'A question')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => expect(screen.getByRole('button', { name: 'A question' })).toBeInTheDocument())
    expect(listConversationsMock).toHaveBeenCalledTimes(2)
  })
})
