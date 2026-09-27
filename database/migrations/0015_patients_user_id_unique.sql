-- Phase 4 (RBAC) needs to resolve "the patient record for this application
-- user" unambiguously when building a PATIENT's authorization scope. The
-- Phase 2 schema left patients.user_id un-constrained beyond the foreign
-- key, so nothing prevented two patient rows from pointing at the same
-- user. A partial unique index (NULLs excluded, since a front-desk-
-- registered patient may have no portal login at all) closes that gap
-- without touching the Phase 2 migration that created the column.

CREATE UNIQUE INDEX patients_user_id_key ON patients (user_id) WHERE user_id IS NOT NULL;
