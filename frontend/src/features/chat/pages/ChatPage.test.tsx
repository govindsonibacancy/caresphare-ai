import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AiAnswerResponse, Page } from '../../../lib/api'
import type { ConversationSummary } from '../../../lib/api'
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

function emptyConversationPage(): Page<ConversationSummary> {
  return { items: [], page: 1, page_size: 50, total: 0 }
}

function successResponse(overrides: Partial<AiAnswerResponse> = {}): AiAnswerResponse {
  return {
    answer: 'You have one appointment tomorrow.',
    sources: [],
    model: 'llama3.2:3b',
    conversation_id: 'conv-1',
    request_id: 'req-1',
    ...overrides,
  }
}

function renderChatPage(initialPath = '/chat') {
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
  listConversationsMock.mockResolvedValue(emptyConversationPage())
  localStorage.clear()
  sessionStorage.clear()
})

afterEach(() => {
  localStorage.clear()
  sessionStorage.clear()
})

describe('ChatPage', () => {
  it('renders the empty state with suggested questions when there are no messages', async () => {
    renderChatPage()
    expect(screen.getByText('How can I help?')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'What appointments do I have?' })).toBeInTheDocument()
    await waitFor(() => expect(listConversationsMock).toHaveBeenCalled())
  })

  it('sends a question and renders the grounded answer', async () => {
    askAiMock.mockResolvedValueOnce(successResponse({ answer: 'You have one appointment tomorrow.' }))
    const user = userEvent.setup()
    renderChatPage()

    await user.type(screen.getByLabelText('Ask CareSphere AI a question'), 'What appointments do I have?')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => expect(screen.getByText('You have one appointment tomorrow.')).toBeInTheDocument())
    expect(askAiMock).toHaveBeenCalledWith({ query: 'What appointments do I have?', conversation_id: null })
  })

  it('continues the same conversation on the next turn using the backend-assigned id', async () => {
    askAiMock.mockResolvedValueOnce(successResponse({ conversation_id: 'conv-42', answer: 'First answer.' }))
    askAiMock.mockResolvedValueOnce(successResponse({ conversation_id: 'conv-42', answer: 'Second answer.' }))
    const user = userEvent.setup()
    renderChatPage()

    const input = screen.getByLabelText('Ask CareSphere AI a question')
    await user.type(input, 'First question')
    await user.click(screen.getByRole('button', { name: 'Send' }))
    await waitFor(() => expect(screen.getByText('First answer.')).toBeInTheDocument())

    await user.type(input, 'Second question')
    await user.click(screen.getByRole('button', { name: 'Send' }))
    await waitFor(() => expect(screen.getByText('Second answer.')).toBeInTheDocument())

    expect(askAiMock).toHaveBeenLastCalledWith({ query: 'Second question', conversation_id: 'conv-42' })
  })

  it('disables sending while a request is in flight and shows a thinking indicator', async () => {
    let resolveResponse: (value: AiAnswerResponse) => void = () => {}
    askAiMock.mockReturnValueOnce(new Promise((resolve) => { resolveResponse = resolve }))
    const user = userEvent.setup()
    renderChatPage()

    await user.type(screen.getByLabelText('Ask CareSphere AI a question'), 'A question')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    expect(screen.getByTestId('chat-typing-indicator')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sending...' })).toBeDisabled()

    resolveResponse(successResponse())
    await waitFor(() => expect(screen.queryByTestId('chat-typing-indicator')).toBeNull())
  })

  it('does not submit a blank message', async () => {
    const user = userEvent.setup()
    renderChatPage()
    await user.click(screen.getByRole('button', { name: 'Send' }))
    expect(askAiMock).not.toHaveBeenCalled()
  })

  it('shows a safe error message and preserves the user message on failure', async () => {
    const { AiRequestError } = await import('../../../lib/api')
    askAiMock.mockRejectedValueOnce(new AiRequestError('The assistant could not complete that request.', 'server', 503, 'req-err-1'))
    const user = userEvent.setup()
    renderChatPage()

    await user.type(screen.getByLabelText('Ask CareSphere AI a question'), 'What appointments do I have?')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
    expect(screen.getByRole('alert')).toHaveTextContent('The assistant could not complete that request.')
    expect(screen.getByRole('alert')).toHaveTextContent('req-err-1')
    expect(screen.getByText('What appointments do I have?')).toBeInTheDocument()
  })

  it('retries without duplicating the original user message', async () => {
    const { AiRequestError } = await import('../../../lib/api')
    askAiMock.mockRejectedValueOnce(new AiRequestError('Server error.', 'server', 503, null))
    askAiMock.mockResolvedValueOnce(successResponse({ answer: 'Recovered answer.' }))
    const user = userEvent.setup()
    renderChatPage()

    await user.type(screen.getByLabelText('Ask CareSphere AI a question'), 'My question')
    await user.click(screen.getByRole('button', { name: 'Send' }))
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(screen.getByText('Recovered answer.')).toBeInTheDocument())

    expect(screen.getAllByText('My question')).toHaveLength(1)
    expect(askAiMock).toHaveBeenCalledTimes(2)
  })

  it('new chat clears messages and conversation state without deleting the conversation', async () => {
    askAiMock.mockResolvedValueOnce(successResponse({ answer: 'An answer.' }))
    const user = userEvent.setup()
    renderChatPage()

    await user.type(screen.getByLabelText('Ask CareSphere AI a question'), 'A question')
    await user.click(screen.getByRole('button', { name: 'Send' }))
    await waitFor(() => expect(screen.getByText('An answer.')).toBeInTheDocument())

    askAiMock.mockClear()
    await user.click(screen.getByRole('button', { name: 'New chat' }))

    await waitFor(() => expect(screen.getByText('How can I help?')).toBeInTheDocument())
    expect(askAiMock).not.toHaveBeenCalled() // New Chat never calls the answer/delete API
  })

  it('never persists chat content to localStorage or sessionStorage', async () => {
    askAiMock.mockResolvedValueOnce(
      successResponse({ answer: 'Sensitive answer content should stay backend-only.' }),
    )
    const user = userEvent.setup()
    renderChatPage()

    await user.type(screen.getByLabelText('Ask CareSphere AI a question'), 'A sensitive question')
    await user.click(screen.getByRole('button', { name: 'Send' }))
    await waitFor(() => expect(screen.getByText('Sensitive answer content should stay backend-only.')).toBeInTheDocument())

    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
  })
})
