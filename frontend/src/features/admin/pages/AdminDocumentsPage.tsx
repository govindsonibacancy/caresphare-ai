import { type FormEvent, useCallback, useEffect, useState } from 'react'
import {
  ALLOWED_ROLE_NAMES,
  type AllowedRoleName,
  DOCUMENT_SENSITIVITIES,
  DOCUMENT_TYPES,
  type Department,
  type DocumentDetail,
  type DocumentProcessingStatus,
  type DocumentSensitivity,
  type DocumentSummary,
  type DocumentType,
  type DoctorOption,
  type StaffOption,
  archiveDocument,
  getDocument,
  listAdminDoctors,
  listAdminStaff,
  listDepartments,
  listDocuments,
  updateDocument,
  uploadDocument,
} from '../../../lib/api'

// Mirrors backend/app/services/documents/validation.py - shown to the admin
// so a rejected file is not a surprise, but the backend independently
// re-validates every one of these regardless of what this form displays.
const SUPPORTED_EXTENSIONS = '.pdf, .docx, .txt, .md'
const MAX_UPLOAD_SIZE_MB = 20

const STATUS_FILTERS = ['ALL', 'PENDING', 'PROCESSING', 'COMPLETED', 'FAILED'] as const
const ACTIVE_FILTERS = ['ALL', 'ACTIVE', 'ARCHIVED'] as const

function statusBadgeClass(status: DocumentProcessingStatus): string {
  return `document-status document-status--${status.toLowerCase()}`
}

export function AdminDocumentsPage() {
  const [documents, setDocuments] = useState<DocumentSummary[]>([])
  const [departments, setDepartments] = useState<Department[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const pageSize = 20

  const [statusFilter, setStatusFilter] = useState<(typeof STATUS_FILTERS)[number]>('ALL')
  const [activeFilter, setActiveFilter] = useState<(typeof ACTIVE_FILTERS)[number]>('ACTIVE')
  const [loading, setLoading] = useState(true)
  const [listError, setListError] = useState<string | null>(null)

  const [selectedId, setSelectedId] = useState<string | null>(null)

  const departmentName = useCallback(
    (id: string | null) => (id ? (departments.find((d) => d.id === id)?.name ?? id) : 'Hospital-wide'),
    [departments],
  )

  const refresh = useCallback(async () => {
    setLoading(true)
    setListError(null)
    try {
      const [result, departmentsResult] = await Promise.all([
        listDocuments({
          page,
          page_size: pageSize,
          status: statusFilter === 'ALL' ? undefined : statusFilter,
          is_active: activeFilter === 'ALL' ? undefined : activeFilter === 'ACTIVE',
        }),
        listDepartments(),
      ])
      setDocuments(result.items)
      setTotal(result.total)
      setDepartments(departmentsResult)
    } catch (err) {
      setListError(err instanceof Error ? err.message : 'Failed to load documents.')
    } finally {
      setLoading(false)
    }
  }, [page, statusFilter, activeFilter])

  useEffect(() => {
    void refresh()
  }, [refresh])

  async function handleArchive(id: string) {
    try {
      await archiveDocument(id)
      await refresh()
      if (selectedId === id) setSelectedId(null)
    } catch (err) {
      setListError(err instanceof Error ? err.message : 'Could not archive document.')
    }
  }

  return (
    <section className="page">
      <h1>Document management</h1>
      <p>
        Upload hospital knowledge-base documents for the RAG assistant. Admin
        uploads never require manually creating chunks or embeddings - the
        backend extracts, cleans, chunks, and embeds the file automatically
        (see <code>docs/RAG_INGESTION.md</code>). This page only manages
        documents and their access scope; it does not answer questions from
        them - that is a later phase.
      </p>

      <UploadForm departments={departments} onUploaded={refresh} />

      <div className="invite-filters">
        <label>
          Status
          <select
            value={statusFilter}
            onChange={(e) => {
              setPage(1)
              setStatusFilter(e.target.value as typeof statusFilter)
            }}
          >
            {STATUS_FILTERS.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label>
          Active
          <select
            value={activeFilter}
            onChange={(e) => {
              setPage(1)
              setActiveFilter(e.target.value as typeof activeFilter)
            }}
          >
            {ACTIVE_FILTERS.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
      </div>

      {listError && <p className="auth-error">{listError}</p>}
      {loading ? (
        <p>Loading...</p>
      ) : (
        <>
          <table className="invite-table">
            <thead>
              <tr>
                <th>Title</th>
                <th>Type</th>
                <th>Department</th>
                <th>Sensitivity</th>
                <th>Status</th>
                <th>Chunks</th>
                <th>Active</th>
                <th>Updated</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {documents.map((doc) => (
                <tr key={doc.id}>
                  <td>
                    <button type="button" className="link-button" onClick={() => setSelectedId(doc.id)}>
                      {doc.title}
                    </button>
                  </td>
                  <td>{doc.document_type}</td>
                  <td>{departmentName(doc.department_id)}</td>
                  <td>{doc.sensitivity}</td>
                  <td>
                    <span className={statusBadgeClass(doc.status)}>{doc.status}</span>
                  </td>
                  <td>{doc.chunk_count}</td>
                  <td>{doc.is_active ? 'Yes' : 'Archived'}</td>
                  <td>{new Date(doc.updated_at).toLocaleString()}</td>
                  <td>
                    {doc.is_active && (
                      <button type="button" onClick={() => handleArchive(doc.id)}>
                        Archive
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {documents.length === 0 && (
                <tr>
                  <td colSpan={9}>No documents found.</td>
                </tr>
              )}
            </tbody>
          </table>
          <div className="invite-filters">
            <button type="button" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
              Previous
            </button>
            <span>
              Page {page} of {Math.max(1, Math.ceil(total / pageSize))} ({total} total)
            </span>
            <button type="button" disabled={page * pageSize >= total} onClick={() => setPage((p) => p + 1)}>
              Next
            </button>
          </div>
        </>
      )}

      {selectedId && (
        <DocumentDetailPanel
          documentId={selectedId}
          departments={departments}
          onClose={() => setSelectedId(null)}
          onChanged={refresh}
        />
      )}
    </section>
  )
}

function UploadForm({
  departments,
  onUploaded,
}: {
  departments: Department[]
  onUploaded: () => Promise<void>
}) {
  const [file, setFile] = useState<File | null>(null)
  const [title, setTitle] = useState('')
  const [documentType, setDocumentType] = useState<DocumentType>('HOSPITAL_POLICY')
  const [sensitivity, setSensitivity] = useState<DocumentSensitivity>('PUBLIC')
  const [description, setDescription] = useState('')
  const [departmentId, setDepartmentId] = useState('')
  const [allowedRoles, setAllowedRoles] = useState<AllowedRoleName[]>([])
  const [doctors, setDoctors] = useState<DoctorOption[]>([])
  const [staff, setStaff] = useState<StaffOption[]>([])
  const [authorizedDoctorIds, setAuthorizedDoctorIds] = useState<string[]>([])
  const [authorizedStaffIds, setAuthorizedStaffIds] = useState<string[]>([])

  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [result, setResult] = useState<DocumentDetail | null>(null)

  useEffect(() => {
    listAdminDoctors().then(setDoctors).catch(() => setDoctors([]))
    listAdminStaff().then(setStaff).catch(() => setStaff([]))
  }, [])

  function toggleRole(role: AllowedRoleName) {
    setAllowedRoles((current) => (current.includes(role) ? current.filter((r) => r !== role) : [...current, role]))
  }

  function toggleDoctor(id: string) {
    setAuthorizedDoctorIds((current) => (current.includes(id) ? current.filter((d) => d !== id) : [...current, id]))
  }

  function toggleStaff(id: string) {
    setAuthorizedStaffIds((current) => (current.includes(id) ? current.filter((s) => s !== id) : [...current, id]))
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setFormError(null)
    setResult(null)
    if (!file) {
      setFormError('Choose a file to upload.')
      return
    }
    setSubmitting(true)
    try {
      const uploaded = await uploadDocument({
        file,
        title,
        document_type: documentType,
        sensitivity,
        description: description || undefined,
        department_id: departmentId || undefined,
        allowed_roles: allowedRoles,
        authorized_doctor_ids: authorizedDoctorIds,
        authorized_staff_ids: authorizedStaffIds,
      })
      setResult(uploaded)
      setFile(null)
      setTitle('')
      setDescription('')
      setDepartmentId('')
      setAllowedRoles([])
      setAuthorizedDoctorIds([])
      setAuthorizedStaffIds([])
      await onUploaded()
    } catch (err) {
      setFormError(err instanceof Error ? err.message : 'Upload failed.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form className="auth-form invite-form document-upload-form" onSubmit={handleSubmit}>
      <label>
        File ({SUPPORTED_EXTENSIONS}, up to {MAX_UPLOAD_SIZE_MB} MB)
        <input
          type="file"
          accept={SUPPORTED_EXTENSIONS}
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          required
        />
      </label>
      <label>
        Title
        <input value={title} onChange={(e) => setTitle(e.target.value)} required />
      </label>
      <label>
        Document type
        <select value={documentType} onChange={(e) => setDocumentType(e.target.value as DocumentType)}>
          {DOCUMENT_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
      </label>
      <label>
        Sensitivity
        <select value={sensitivity} onChange={(e) => setSensitivity(e.target.value as DocumentSensitivity)}>
          {DOCUMENT_SENSITIVITIES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </label>
      <label>
        Description (optional)
        <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2} />
      </label>
      <label>
        Department (optional)
        <select value={departmentId} onChange={(e) => setDepartmentId(e.target.value)}>
          <option value="">Hospital-wide</option>
          {departments.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name}
            </option>
          ))}
        </select>
      </label>

      <fieldset className="document-access-fieldset">
        <legend>Roles allowed to retrieve this document</legend>
        {ALLOWED_ROLE_NAMES.map((role) => (
          <label key={role} className="checkbox-label">
            <input type="checkbox" checked={allowedRoles.includes(role)} onChange={() => toggleRole(role)} />
            {role}
          </label>
        ))}
      </fieldset>

      {doctors.length > 0 && (
        <fieldset className="document-access-fieldset">
          <legend>Restrict to specific doctors (optional)</legend>
          {doctors.map((doctor) => (
            <label key={doctor.id} className="checkbox-label">
              <input
                type="checkbox"
                checked={authorizedDoctorIds.includes(doctor.id)}
                onChange={() => toggleDoctor(doctor.id)}
              />
              Dr. {doctor.first_name} {doctor.last_name}
              {doctor.specialization ? ` (${doctor.specialization})` : ''}
            </label>
          ))}
        </fieldset>
      )}

      {staff.length > 0 && (
        <fieldset className="document-access-fieldset">
          <legend>Restrict to specific staff (optional)</legend>
          {staff.map((member) => (
            <label key={member.id} className="checkbox-label">
              <input
                type="checkbox"
                checked={authorizedStaffIds.includes(member.id)}
                onChange={() => toggleStaff(member.id)}
              />
              {member.first_name} {member.last_name}
              {member.designation ? ` (${member.designation})` : ''}
            </label>
          ))}
        </fieldset>
      )}

      {formError && <p className="auth-error">{formError}</p>}
      <button type="submit" disabled={submitting}>
        {submitting ? 'Uploading and processing...' : 'Upload'}
      </button>

      {result && (
        <p className={result.status === 'FAILED' ? 'auth-error' : 'invite-url-note'}>
          {result.status === 'COMPLETED' &&
            `"${result.title}" uploaded and processed successfully (${result.chunk_count} chunks).`}
          {result.status === 'FAILED' &&
            `"${result.title}" was uploaded, but processing failed: ${result.processing_error ?? 'unknown error.'}`}
        </p>
      )}
    </form>
  )
}

function DocumentDetailPanel({
  documentId,
  departments,
  onClose,
  onChanged,
}: {
  documentId: string
  departments: Department[]
  onClose: () => void
  onChanged: () => Promise<void>
}) {
  const [detail, setDetail] = useState<DocumentDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [departmentId, setDepartmentId] = useState('')
  const [allowedRoles, setAllowedRoles] = useState<AllowedRoleName[]>([])
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setLoadError(null)
    getDocument(documentId)
      .then((doc) => {
        if (cancelled) return
        setDetail(doc)
        setTitle(doc.title)
        setDescription(doc.description ?? '')
        setDepartmentId(doc.department_id ?? '')
        setAllowedRoles(doc.allowed_roles.filter((r): r is AllowedRoleName => (ALLOWED_ROLE_NAMES as readonly string[]).includes(r)))
      })
      .catch((err) => {
        if (!cancelled) setLoadError(err instanceof Error ? err.message : 'Could not load document.')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [documentId])

  function toggleRole(role: AllowedRoleName) {
    setAllowedRoles((current) => (current.includes(role) ? current.filter((r) => r !== role) : [...current, role]))
  }

  async function handleSave(event: FormEvent) {
    event.preventDefault()
    setSaveError(null)
    setSaving(true)
    try {
      const updated = await updateDocument(documentId, {
        title,
        description: description || null,
        department_id: departmentId || null,
        allowed_roles: allowedRoles,
      })
      setDetail(updated)
      await onChanged()
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : 'Could not save changes.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="document-detail-panel">
      <div className="document-detail-panel-header">
        <h2>Document details</h2>
        <button type="button" onClick={onClose}>
          Close
        </button>
      </div>
      {loading && <p>Loading...</p>}
      {loadError && <p className="auth-error">{loadError}</p>}
      {detail && (
        <>
          <dl className="profile-details">
            <dt>File name</dt>
            <dd>{detail.filename}</dd>
            <dt>MIME type</dt>
            <dd>{detail.mime_type ?? '-'}</dd>
            <dt>File size</dt>
            <dd>{detail.file_size != null ? `${(detail.file_size / 1024).toFixed(1)} KB` : '-'}</dd>
            <dt>Status</dt>
            <dd>
              <span className={statusBadgeClass(detail.status)}>{detail.status}</span>
            </dd>
            {detail.processing_error && (
              <>
                <dt>Processing error</dt>
                <dd>{detail.processing_error}</dd>
              </>
            )}
            <dt>Chunks</dt>
            <dd>{detail.chunk_count}</dd>
            <dt>Uploaded by (user id)</dt>
            <dd>{detail.uploaded_by}</dd>
            <dt>Created</dt>
            <dd>{new Date(detail.created_at).toLocaleString()}</dd>
            <dt>Updated</dt>
            <dd>{new Date(detail.updated_at).toLocaleString()}</dd>
            <dt>Authorized doctors</dt>
            <dd>{detail.authorized_doctor_ids.length || 'None (role-based access only)'}</dd>
            <dt>Authorized staff</dt>
            <dd>{detail.authorized_staff_ids.length || 'None (role-based access only)'}</dd>
          </dl>

          <form className="auth-form" onSubmit={handleSave}>
            <label>
              Title
              <input value={title} onChange={(e) => setTitle(e.target.value)} required />
            </label>
            <label>
              Description
              <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2} />
            </label>
            <label>
              Department
              <select value={departmentId} onChange={(e) => setDepartmentId(e.target.value)}>
                <option value="">Hospital-wide</option>
                {departments.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
              </select>
            </label>
            <fieldset className="document-access-fieldset">
              <legend>Roles allowed to retrieve this document</legend>
              {ALLOWED_ROLE_NAMES.map((role) => (
                <label key={role} className="checkbox-label">
                  <input type="checkbox" checked={allowedRoles.includes(role)} onChange={() => toggleRole(role)} />
                  {role}
                </label>
              ))}
            </fieldset>
            {saveError && <p className="auth-error">{saveError}</p>}
            <button type="submit" disabled={saving}>
              {saving ? 'Saving...' : 'Save changes'}
            </button>
          </form>
        </>
      )}
    </div>
  )
}
