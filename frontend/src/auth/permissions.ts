/**
 * Frontend permission checks are UX only: hiding a nav link or disabling a
 * button. They are never the security boundary - every protected backend
 * call independently re-verifies the caller's permission against the
 * database (see backend/app/permissions/dependencies.py, require_permission).
 * A user who bypasses this check in devtools gains nothing: the backend
 * would still reject the request.
 */
export function hasPermission(permissions: readonly string[] | null | undefined, permission: string): boolean {
  return permissions?.includes(permission) ?? false
}
