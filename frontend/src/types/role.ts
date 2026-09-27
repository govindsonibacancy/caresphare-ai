/**
 * Roles supported by CareSphere AI's RBAC model.
 * Must stay in sync with backend/app/permissions/roles.py.
 */
export type Role =
  | 'PATIENT'
  | 'DOCTOR'
  | 'NURSE'
  | 'RECEPTIONIST'
  | 'STAFF'
  | 'HOSPITAL_ADMIN'
  | 'SUPER_ADMIN'
