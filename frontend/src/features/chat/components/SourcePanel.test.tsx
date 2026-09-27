import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { SourceReference } from '../../../lib/api'
import { SourcePanel } from './SourcePanel'

describe('SourcePanel', () => {
  it('renders nothing when no source is selected', () => {
    const { container } = render(<SourcePanel source={null} onClose={() => {}} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders safe document metadata for a document source', () => {
    const source: SourceReference = {
      number: 2,
      id: 'source-2',
      type: 'document',
      label: 'Infection Control Policy',
      document_id: 'doc-1',
      document_title: 'Infection Control Policy',
      document_type: 'HOSPITAL_POLICY',
      chunk_id: 'chunk-1',
      page: 4,
      section: 'Training Requirements',
    }
    render(<SourcePanel source={source} onClose={() => {}} />)
    expect(screen.getByRole('heading', { name: 'Source 2' })).toBeInTheDocument()
    expect(screen.getByText('Document')).toBeInTheDocument()
    expect(screen.getAllByText('Infection Control Policy')).toHaveLength(2) // label + title field
    expect(screen.getByText('Hospital Policy')).toBeInTheDocument()
    expect(screen.getByText('4')).toBeInTheDocument()
    expect(screen.getByText('Training Requirements')).toBeInTheDocument()
    // Never a raw internal id in the visible panel text.
    expect(screen.queryByText('doc-1')).toBeNull()
    expect(screen.queryByText('chunk-1')).toBeNull()
  })

  it('renders a structured/administrative source using only the backend label', () => {
    const source: SourceReference = {
      number: 1,
      id: 'source-1',
      type: 'administrative_summary',
      label: 'Employee Summary — Total: 15',
      document_id: null,
      document_title: null,
      document_type: null,
      chunk_id: null,
      page: null,
      section: null,
    }
    render(<SourcePanel source={source} onClose={() => {}} />)
    expect(screen.getByText('Administrative summary')).toBeInTheDocument()
    expect(screen.getByText('Employee Summary — Total: 15')).toBeInTheDocument()
  })

  it('calls onClose when the close button is activated', async () => {
    const onClose = vi.fn()
    const source: SourceReference = {
      number: 1,
      id: 'source-1',
      type: 'document',
      label: 'A policy',
      document_id: null,
      document_title: null,
      document_type: null,
      chunk_id: null,
      page: null,
      section: null,
    }
    const user = userEvent.setup()
    render(<SourcePanel source={source} onClose={onClose} />)
    await user.click(screen.getByRole('button', { name: 'Close source details' }))
    expect(onClose).toHaveBeenCalled()
  })
})
