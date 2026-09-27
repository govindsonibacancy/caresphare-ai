import type { SourceReference, SourceType } from '../../../lib/api'

interface SourcePanelProps {
  source: SourceReference | null
  onClose: () => void
}

/** Human-readable labels for the backend's own closed SourceType enum
 * (backend/app/sources/models.py) - display only, never used to infer
 * trust or to reconstruct anything the backend didn't already provide. */
const SOURCE_TYPE_LABELS: Record<SourceType, string> = {
  document: 'Document',
  appointment: 'Appointment',
  medical_record: 'Medical record',
  lab_report: 'Lab report',
  prescription: 'Prescription',
  doctor: 'Doctor directory',
  department: 'Department directory',
  administrative_summary: 'Administrative summary',
}

/**
 * Renders only fields the backend actually returned on this
 * `SourceReference` - see docs/FRONTEND_AI_CHAT.md, "Source UI". Never
 * fetches anything else independently; a source's authority is entirely
 * whatever the backend already sent with the answer (docs/SOURCES_AND_CITATIONS.md).
 */
export function SourcePanel({ source, onClose }: SourcePanelProps) {
  if (!source) return null

  return (
    <aside className="source-panel" role="dialog" aria-label={`Source ${source.number} details`}>
      <div className="source-panel-header">
        <h2>Source {source.number}</h2>
        <button type="button" className="source-panel-close" onClick={onClose} aria-label="Close source details">
          Close
        </button>
      </div>
      <p className="source-panel-type">{SOURCE_TYPE_LABELS[source.type] ?? source.type}</p>
      <p className="source-panel-label">{source.label}</p>
      {source.type === 'document' && (
        <dl className="source-panel-details">
          {source.document_title && (
            <>
              <dt>Title</dt>
              <dd>{source.document_title}</dd>
            </>
          )}
          {source.document_type && (
            <>
              <dt>Document type</dt>
              <dd>{formatDocumentType(source.document_type)}</dd>
            </>
          )}
          {source.page !== null && (
            <>
              <dt>Page</dt>
              <dd>{source.page}</dd>
            </>
          )}
          {source.section && (
            <>
              <dt>Section</dt>
              <dd>{source.section}</dd>
            </>
          )}
        </dl>
      )}
    </aside>
  )
}

function formatDocumentType(documentType: string): string {
  return documentType
    .toLowerCase()
    .split('_')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ')
}
