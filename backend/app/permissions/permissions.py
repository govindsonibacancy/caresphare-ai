from enum import StrEnum


class Permission(StrEnum):
    """Typed constants for the permission codes seeded into `permissions` by
    database/seeds/0001_demo_data.sql.

    This is a vocabulary of *names*, not a role→permission map - which role
    has which permission is never hard-coded here or anywhere in Python; it
    is always resolved from `role_permissions` at request time (see
    app/permissions/scope.py). Adding a permission to the seed without
    adding it here still works (has_permission does a plain string
    membership check) - this enum exists so application code doesn't
    reference permission codes as untyped string literals.
    """

    VIEW_OWN_PROFILE = "view_own_profile"
    VIEW_OWN_APPOINTMENTS = "view_own_appointments"
    VIEW_OWN_REPORTS = "view_own_reports"
    VIEW_OWN_PRESCRIPTIONS = "view_own_prescriptions"

    VIEW_ASSIGNED_PATIENTS = "view_assigned_patients"
    VIEW_PATIENT_BASIC_INFORMATION = "view_patient_basic_information"
    VIEW_PATIENT_MEDICAL_RECORDS = "view_patient_medical_records"
    VIEW_VITAL_RECORDS = "view_vital_records"

    CREATE_CLINICAL_NOTES = "create_clinical_notes"
    UPDATE_NURSING_NOTES = "update_nursing_notes"

    MANAGE_APPOINTMENTS = "manage_appointments"
    CREATE_APPOINTMENTS = "create_appointments"

    VIEW_ADMISSION_INFORMATION = "view_admission_information"

    MANAGE_USERS = "manage_users"
    MANAGE_ROLES = "manage_roles"
    MANAGE_DEPARTMENTS = "manage_departments"
    MANAGE_HOSPITAL_DOCUMENTS = "manage_hospital_documents"

    VIEW_HOSPITAL_OPERATIONS = "view_hospital_operations"
    VIEW_HOSPITAL_ANALYTICS = "view_hospital_analytics"
