import { supabase } from './supabase'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

async function authHeaders(): Promise<HeadersInit> {
  const { data } = await supabase.auth.getSession()
  const token = data.session?.access_token
  return token ? { Authorization: `Bearer ${token}` } : {}
}

/** Attaches the current Supabase access token, if any. Never logs it. */
async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = { ...(await authHeaders()), ...(init.headers ?? {}) }
  return fetch(`${API_BASE_URL}${path}`, { ...init, headers })
}

async function readErrorDetail(response: Response, fallback: string): Promise<string> {
  const body = await response.json().catch(() => null)
  return (body && typeof body.detail === 'string' && body.detail) || fallback
}

export interface HealthStatus {
  status: string
  service: string
}

export async function fetchHealth(): Promise<HealthStatus> {
  const response = await fetch(`${API_BASE_URL}/health`)
  if (!response.ok) {
    throw new Error(`Health check failed with status ${response.status}`)
  }
  return response.json()
}

export interface CurrentUser {
  id: string
  auth_user_id: string
  email: string
  first_name: string
  last_name: string
  hospital_id: string
  role: string
  department_id: string | null
  is_active: boolean
}

export async function fetchCurrentUser(): Promise<CurrentUser> {
  const response = await apiFetch('/api/auth/me')
  if (!response.ok) {
    throw new Error(await readErrorDetail(response, `Failed to load current user (${response.status})`))
  }
  return response.json()
}

export interface AuthorizationContext {
  role: string
  hospital_id: string
  department_id: string | null
  permissions: string[]
}

/**
 * UX-only data: which nav/actions to show. The backend independently
 * re-checks every permission on every protected call - this is never the
 * security boundary, just what's convenient to know client-side.
 */
export async function fetchAuthorizationContext(): Promise<AuthorizationContext> {
  const response = await apiFetch('/api/authz/me')
  if (!response.ok) {
    throw new Error(await readErrorDetail(response, `Failed to load permissions (${response.status})`))
  }
  return response.json()
}

export interface RegisterPatientInput {
  auth_user_id: string
  email: string
  first_name: string
  last_name: string
}

export async function registerPatient(input: RegisterPatientInput): Promise<CurrentUser> {
  const response = await apiFetch('/api/auth/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  if (!response.ok) {
    throw new Error(await readErrorDetail(response, `Registration failed (${response.status})`))
  }
  return response.json()
}

// --- Employee invitations (Phase 5) -------------------------------------

export type InviteableRole = 'DOCTOR' | 'NURSE' | 'RECEPTIONIST' | 'STAFF'

export interface InvitationSummary {
  id: string
  email: string
  first_name: string
  last_name: string
  role: string
  hospital_id: string
  department_id: string | null
  status: 'PENDING' | 'ACCEPTED' | 'EXPIRED' | 'REVOKED'
  invited_by_user_id: string
  expires_at: string
  accepted_at: string | null
  created_at: string
}

export interface InvitationCreatedResponse extends InvitationSummary {
  /** Shown once, to the admin who just created it - see docs/EMPLOYEE_INVITATIONS.md. */
  invitation_url: string
}

export interface Department {
  id: string
  name: string
  code: string
}

/** Populates the invitation form's department picker (caller's own hospital). */
export async function listDepartments(): Promise<Department[]> {
  const response = await apiFetch('/api/admin/departments')
  if (!response.ok) {
    throw new Error(await readErrorDetail(response, `Could not load departments (${response.status})`))
  }
  return response.json()
}

export interface CreateInvitationInput {
  email: string
  first_name: string
  last_name: string
  role: InviteableRole
  department_id?: string | null
}

export async function createInvitation(input: CreateInvitationInput): Promise<InvitationCreatedResponse> {
  const response = await apiFetch('/api/admin/invitations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  if (!response.ok) {
    throw new Error(await readErrorDetail(response, `Could not create invitation (${response.status})`))
  }
  return response.json()
}

export interface ListInvitationsFilters {
  status?: string
  role?: string
  department_id?: string
  email?: string
}

export async function listInvitations(filters: ListInvitationsFilters = {}): Promise<InvitationSummary[]> {
  const params = new URLSearchParams(Object.entries(filters).filter(([, v]) => Boolean(v)) as [string, string][])
  const query = params.toString()
  const response = await apiFetch(`/api/admin/invitations${query ? `?${query}` : ''}`)
  if (!response.ok) {
    throw new Error(await readErrorDetail(response, `Could not load invitations (${response.status})`))
  }
  return response.json()
}

export async function revokeInvitation(id: string): Promise<InvitationSummary> {
  const response = await apiFetch(`/api/admin/invitations/${id}/revoke`, { method: 'POST' })
  if (!response.ok) {
    throw new Error(await readErrorDetail(response, `Could not revoke invitation (${response.status})`))
  }
  return response.json()
}

export interface InvitationPreview {
  email: string
  first_name: string
  last_name: string
  role: string
  department: string | null
  status: 'PENDING' | 'ACCEPTED' | 'EXPIRED' | 'REVOKED'
  expires_at: string
}

/** Public - no auth token needed (the invitee has no account yet). */
export async function previewInvitation(token: string): Promise<InvitationPreview> {
  const response = await fetch(`${API_BASE_URL}/api/invitations/${encodeURIComponent(token)}`)
  if (!response.ok) {
    throw new Error(await readErrorDetail(response, `Invitation not found (${response.status})`))
  }
  return response.json()
}

/** Public - the invitee has just created (or signed in to) their own
 * Supabase identity client-side; this links it to the invitation. */
export async function acceptInvitation(token: string, authUserId: string): Promise<CurrentUser> {
  const response = await fetch(`${API_BASE_URL}/api/invitations/${encodeURIComponent(token)}/accept`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ auth_user_id: authUserId }),
  })
  if (!response.ok) {
    throw new Error(await readErrorDetail(response, `Could not accept invitation (${response.status})`))
  }
  return response.json()
}

// --- Structured hospital data (Phase 6) -----------------------------------
//
// Minimal typed client for the new read APIs - no dashboard UI is built on
// these yet (that's a later phase). Every list call returns a `Page<T>`;
// the backend enforces a max page_size itself, this client just mirrors
// the shape. Authorization is entirely server-side - these functions pass
// through whatever the backend decides, never filter or second-guess it.

export interface Page<T> {
  items: T[]
  page: number
  page_size: number
  total: number
}

export interface Patient {
  id: string
  user_id: string | null
  hospital_id: string
  patient_number: string
  date_of_birth: string | null
  gender: string | null
  blood_group: string | null
  phone: string | null
  address: string | null
  emergency_contact_name: string | null
  emergency_contact_phone: string | null
  created_at: string
  updated_at: string
}

export interface Doctor {
  id: string
  user_id: string
  hospital_id: string
  department_id: string | null
  employee_number: string
  specialization: string | null
  license_number: string | null
  created_at: string
  updated_at: string
}

export interface HospitalDepartment {
  id: string
  hospital_id: string
  name: string
  code: string
  description: string | null
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface Appointment {
  id: string
  hospital_id: string
  patient_id: string
  doctor_id: string
  department_id: string | null
  appointment_date: string
  appointment_time: string
  status: 'SCHEDULED' | 'CONFIRMED' | 'COMPLETED' | 'CANCELLED' | 'NO_SHOW'
  reason: string | null
  notes: string | null
  created_at: string
  updated_at: string
}

export interface MedicalRecord {
  id: string
  hospital_id: string
  patient_id: string
  doctor_id: string
  record_type: string
  title: string
  description: string | null
  clinical_notes: string | null
  recorded_at: string
  created_at: string
  updated_at: string
}

export interface LabReport {
  id: string
  hospital_id: string
  patient_id: string
  ordered_by_doctor_id: string
  test_name: string
  test_code: string | null
  result: string | null
  unit: string | null
  reference_range: string | null
  status: 'ORDERED' | 'IN_PROGRESS' | 'COMPLETED' | 'CANCELLED'
  reported_at: string | null
  created_at: string
  updated_at: string
}

export interface Prescription {
  id: string
  hospital_id: string
  patient_id: string
  doctor_id: string
  medication_name: string
  dosage: string | null
  frequency: string | null
  route: string | null
  duration: string | null
  instructions: string | null
  prescribed_at: string
  created_at: string
  updated_at: string
}

async function fetchPage<T>(
  path: string,
  params: Record<string, string | number | boolean | undefined> = {},
): Promise<Page<T>> {
  const query = new URLSearchParams(
    Object.entries(params)
      .filter((entry): entry is [string, string | number | boolean] => entry[1] !== undefined)
      .map(([key, value]) => [key, String(value)]),
  ).toString()
  const response = await apiFetch(`${path}${query ? `?${query}` : ''}`)
  if (!response.ok) {
    throw new Error(await readErrorDetail(response, `Request failed (${response.status})`))
  }
  return response.json()
}

async function fetchOne<T>(path: string): Promise<T> {
  const response = await apiFetch(path)
  if (!response.ok) {
    throw new Error(await readErrorDetail(response, `Request failed (${response.status})`))
  }
  return response.json()
}

export function listPatients(params: { page?: number; page_size?: number } = {}): Promise<Page<Patient>> {
  return fetchPage('/api/patients', params)
}
export function getPatient(id: string): Promise<Patient> {
  return fetchOne(`/api/patients/${id}`)
}

export function listDoctors(
  params: { page?: number; page_size?: number; department_id?: string } = {},
): Promise<Page<Doctor>> {
  return fetchPage('/api/doctors', params)
}
export function getDoctor(id: string): Promise<Doctor> {
  return fetchOne(`/api/doctors/${id}`)
}

export function listHospitalDepartments(
  params: { page?: number; page_size?: number } = {},
): Promise<Page<HospitalDepartment>> {
  return fetchPage('/api/departments', params)
}
export function getHospitalDepartment(id: string): Promise<HospitalDepartment> {
  return fetchOne(`/api/departments/${id}`)
}

export function listAppointments(
  params: {
    page?: number
    page_size?: number
    patient_id?: string
    doctor_id?: string
    department_id?: string
    status?: Appointment['status']
    date_from?: string
    date_to?: string
    sort_by?: 'appointment_date' | 'created_at'
    descending?: boolean
  } = {},
): Promise<Page<Appointment>> {
  return fetchPage('/api/appointments', params)
}
export function getAppointment(id: string): Promise<Appointment> {
  return fetchOne(`/api/appointments/${id}`)
}

export function listMedicalRecords(
  params: { page?: number; page_size?: number; patient_id?: string; doctor_id?: string; record_type?: string } = {},
): Promise<Page<MedicalRecord>> {
  return fetchPage('/api/medical-records', params)
}
export function getMedicalRecord(id: string): Promise<MedicalRecord> {
  return fetchOne(`/api/medical-records/${id}`)
}

export function listLabReports(
  params: { page?: number; page_size?: number; patient_id?: string; status?: LabReport['status'] } = {},
): Promise<Page<LabReport>> {
  return fetchPage('/api/lab-reports', params)
}
export function getLabReport(id: string): Promise<LabReport> {
  return fetchOne(`/api/lab-reports/${id}`)
}

export function listPrescriptions(
  params: { page?: number; page_size?: number; patient_id?: string; doctor_id?: string } = {},
): Promise<Page<Prescription>> {
  return fetchPage('/api/prescriptions', params)
}
export function getPrescription(id: string): Promise<Prescription> {
  return fetchOne(`/api/prescriptions/${id}`)
}

// --- RAG document ingestion (Phase 7) --------------------------------------
//
// Admin-only document management (upload/list/detail/update/archive). This
// is the ingestion pipeline's management surface, not the RAG retrieval
// this project will add in a later phase - see docs/RAG_INGESTION.md. All
// authorization is enforced server-side (require_permission); the frontend
// only hides UI a user has no permission for.

export const DOCUMENT_TYPES = [
  'HOSPITAL_POLICY',
  'CLINICAL_GUIDELINE',
  'NURSING_PROCEDURE',
  'MEDICATION_GUIDELINE',
  'EMERGENCY_PROCEDURE',
  'PATIENT_EDUCATION',
  'HR_POLICY',
  'SOP',
  'GENERAL_INFORMATION',
] as const
export type DocumentType = (typeof DOCUMENT_TYPES)[number]

export const DOCUMENT_SENSITIVITIES = ['PUBLIC', 'INTERNAL', 'CLINICAL', 'CONFIDENTIAL'] as const
export type DocumentSensitivity = (typeof DOCUMENT_SENSITIVITIES)[number]

export const ALLOWED_ROLE_NAMES = ['DOCTOR', 'NURSE', 'RECEPTIONIST', 'STAFF', 'HOSPITAL_ADMIN', 'SUPER_ADMIN'] as const
export type AllowedRoleName = (typeof ALLOWED_ROLE_NAMES)[number]

export type DocumentProcessingStatus = 'PENDING' | 'PROCESSING' | 'COMPLETED' | 'FAILED'

export interface DocumentSummary {
  id: string
  title: string
  document_type: string
  department_id: string | null
  sensitivity: string
  status: DocumentProcessingStatus
  is_active: boolean
  chunk_count: number
  uploaded_by: string
  created_at: string
  updated_at: string
}

export interface DocumentDetail extends DocumentSummary {
  description: string | null
  filename: string
  mime_type: string | null
  file_size: number | null
  processing_error: string | null
  allowed_roles: string[]
  authorized_doctor_ids: string[]
  authorized_staff_ids: string[]
}

export interface DoctorOption {
  id: string
  first_name: string
  last_name: string
  specialization: string | null
}

export interface StaffOption {
  id: string
  first_name: string
  last_name: string
  designation: string | null
}

export function listAdminDoctors(): Promise<DoctorOption[]> {
  return fetchOne('/api/admin/doctors')
}

export function listAdminStaff(): Promise<StaffOption[]> {
  return fetchOne('/api/admin/staff')
}

export type ListDocumentsFilters = {
  page?: number
  page_size?: number
  status?: DocumentProcessingStatus
  document_type?: DocumentType
  department_id?: string
  is_active?: boolean
}

export function listDocuments(filters: ListDocumentsFilters = {}): Promise<Page<DocumentSummary>> {
  return fetchPage('/api/admin/documents', filters)
}

export function getDocument(id: string): Promise<DocumentDetail> {
  return fetchOne(`/api/admin/documents/${id}`)
}

export interface UploadDocumentInput {
  file: File
  title: string
  document_type: DocumentType
  sensitivity: DocumentSensitivity
  description?: string
  department_id?: string
  allowed_roles?: AllowedRoleName[]
  authorized_doctor_ids?: string[]
  authorized_staff_ids?: string[]
}

/** Multipart upload - the backend derives `hospital_id`/`uploaded_by` from
 * the authenticated session, never from anything submitted here (see
 * docs/RAG_INGESTION.md, "Authorization"). Ingestion (extract -> chunk ->
 * embed) runs synchronously; the response already reflects COMPLETED or
 * FAILED, never PENDING/PROCESSING. */
export async function uploadDocument(input: UploadDocumentInput): Promise<DocumentDetail> {
  const form = new FormData()
  form.set('file', input.file)
  form.set('title', input.title)
  form.set('document_type', input.document_type)
  form.set('sensitivity', input.sensitivity)
  if (input.description) form.set('description', input.description)
  if (input.department_id) form.set('department_id', input.department_id)
  for (const role of input.allowed_roles ?? []) form.append('allowed_roles', role)
  for (const id of input.authorized_doctor_ids ?? []) form.append('authorized_doctor_ids', id)
  for (const id of input.authorized_staff_ids ?? []) form.append('authorized_staff_ids', id)

  const response = await apiFetch('/api/admin/documents', { method: 'POST', body: form })
  if (!response.ok) {
    throw new Error(await readErrorDetail(response, `Upload failed (${response.status})`))
  }
  return response.json()
}

export interface UpdateDocumentInput {
  title?: string
  description?: string | null
  department_id?: string | null
  allowed_roles?: AllowedRoleName[]
  authorized_doctor_ids?: string[]
  authorized_staff_ids?: string[]
}

export async function updateDocument(id: string, input: UpdateDocumentInput): Promise<DocumentDetail> {
  const response = await apiFetch(`/api/admin/documents/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  if (!response.ok) {
    throw new Error(await readErrorDetail(response, `Could not update document (${response.status})`))
  }
  return response.json()
}

export async function archiveDocument(id: string): Promise<DocumentDetail> {
  const response = await apiFetch(`/api/admin/documents/${id}/archive`, { method: 'POST' })
  if (!response.ok) {
    throw new Error(await readErrorDetail(response, `Could not archive document (${response.status})`))
  }
  return response.json()
}

// --- AI chat (Phase 10-15 backend, Phase 16 frontend) ----------------------
//
// A thin, typed wrapper around the existing POST /api/rag/answer contract
// (backend/app/schemas/rag.py) - no new endpoint, no field this schema
// doesn't already declare. `extra="forbid"` on the backend request model
// means there is nothing here for the client to submit beyond `query`/
// `top_k`/`conversation_id` - no hospital/role/permission/model/SQL
// override is possible, by construction, not by frontend discipline alone.

export const SOURCE_TYPES = [
  'document',
  'appointment',
  'medical_record',
  'lab_report',
  'prescription',
  'doctor',
  'department',
  'administrative_summary',
] as const
/** Mirrors backend/app/sources/models.py's SourceType exactly. */
export type SourceType = (typeof SOURCE_TYPES)[number]

export interface SourceReference {
  number: number
  id: string
  type: SourceType
  label: string
  document_id: string | null
  document_title: string | null
  document_type: string | null
  chunk_id: string | null
  page: number | null
  section: string | null
}

export interface AiAnswerRequest {
  query: string
  top_k?: number
  /** Omit on the first turn - the backend creates a conversation and
   * returns its id. Never generate this client-side. */
  conversation_id?: string | null
}

export interface AiAnswerResponse {
  answer: string
  sources: SourceReference[]
  model: string | null
  conversation_id: string | null
  request_id: string | null
}

export type AiErrorKind = 'validation' | 'auth' | 'not_found' | 'server' | 'network'

/** Normalized, safe-to-display chat error - see docs/FRONTEND_AI_CHAT.md,
 * "Error handling". `message` is always a safe, user-facing sentence,
 * never a raw backend detail string that could contain internal
 * information; `requestId` is included only for an optional support
 * reference, never used for anything else. */
export class AiRequestError extends Error {
  readonly kind: AiErrorKind
  readonly status: number | null
  readonly requestId: string | null

  constructor(message: string, kind: AiErrorKind, status: number | null, requestId: string | null) {
    super(message)
    this.name = 'AiRequestError'
    this.kind = kind
    this.status = status
    this.requestId = requestId
  }
}

async function toAiRequestError(response: Response): Promise<AiRequestError> {
  const requestId = response.headers.get('X-Request-ID')
  if (response.status === 404) {
    return new AiRequestError('This conversation is no longer available. Start a new chat to continue.', 'not_found', 404, requestId)
  }
  if (response.status === 401) {
    return new AiRequestError('Please sign in again to continue.', 'auth', 401, requestId)
  }
  if (response.status === 422) {
    return new AiRequestError('Please enter a valid question.', 'validation', 422, requestId)
  }
  if (response.status >= 500) {
    return new AiRequestError("The assistant couldn't complete that request. Please try again.", 'server', response.status, requestId)
  }
  return new AiRequestError('Something went wrong. Please try again.', 'server', response.status, requestId)
}

export async function askAi(input: AiAnswerRequest): Promise<AiAnswerResponse> {
  let response: Response
  try {
    response = await apiFetch('/api/rag/answer', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        query: input.query,
        top_k: input.top_k ?? 5,
        conversation_id: input.conversation_id ?? null,
      }),
    })
  } catch {
    throw new AiRequestError('You appear to be offline. Please check your connection and try again.', 'network', null, null)
  }
  if (!response.ok) {
    throw await toAiRequestError(response)
  }
  return response.json()
}

// --- Conversation history (Phase 13/17 backend, Phase 17 frontend) --------
//
// A thin wrapper around GET /api/conversations and GET /api/conversations/{id}
// (backend/app/schemas/conversations.py) - both self-scoped by the
// authenticated caller server-side; there is no field here that could ask
// for another user's conversations, and none would be honored if sent.

export interface ConversationSummary {
  id: string
  title: string | null
  created_at: string
  updated_at: string
  last_activity_at: string
}

export interface ConversationMessage {
  id: string
  role: 'USER' | 'ASSISTANT'
  content: string
  sources: SourceReference[]
  created_at: string
}

export interface ConversationDetail extends ConversationSummary {
  messages: ConversationMessage[]
}

export async function listConversations(): Promise<Page<ConversationSummary>> {
  return fetchPage('/api/conversations', { page: 1, page_size: 50 })
}

/** A 404 (nonexistent or not owned - identical either way, see
 * docs/CONVERSATIONAL_AUTH_ROUTING.md, "Ownership") throws the same
 * `AiRequestError` shape `askAi` does, so callers can reuse one error
 * handling path. */
export async function getConversation(conversationId: string): Promise<ConversationDetail> {
  let response: Response
  try {
    response = await apiFetch(`/api/conversations/${encodeURIComponent(conversationId)}`)
  } catch {
    throw new AiRequestError('You appear to be offline. Please check your connection and try again.', 'network', null, null)
  }
  if (!response.ok) {
    throw await toAiRequestError(response)
  }
  return response.json()
}
