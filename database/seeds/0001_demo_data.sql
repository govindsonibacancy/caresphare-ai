-- Synthetic/demo data only. No real patient, staff, or hospital information.
--
-- Idempotency: hospital, departments, roles, permissions, role_permissions,
-- users, patients, doctors, and staff are all keyed on a natural unique
-- constraint and inserted with ON CONFLICT DO NOTHING, so re-running this
-- file against a database that already has the seed is a no-op for those
-- tables. Transactional data (appointments, medical_records, lab_reports,
-- prescriptions, doctor_patient_assignments) has no natural unique key and
-- will be duplicated on a second run - this file is meant to be run once
-- against a freshly migrated database.
--
-- auth_user_id values below are placeholder UUIDs, not real Supabase Auth
-- identities. Phase 3 (Supabase Auth) will associate these user profiles
-- with real Supabase-issued user ids.

BEGIN;

-- 1. Hospital -----------------------------------------------------------

INSERT INTO hospitals (name, code, address, city, state, country, phone, email, is_active)
VALUES (
    'CareSphere General Hospital', 'CGH', '100 Wellness Avenue',
    'Pune', 'Maharashtra', 'India', '+91-20-5550100', 'info@caresphere-demo.example', true
)
ON CONFLICT (code) DO NOTHING;

-- 2. Departments ----------------------------------------------------------

INSERT INTO departments (hospital_id, name, code, description)
SELECT h.id, v.name, v.code, v.description
FROM hospitals h
CROSS JOIN (VALUES
    ('Emergency',         'ER',     'Emergency and trauma care'),
    ('Cardiology',        'CARD',   'Heart and cardiovascular care'),
    ('Radiology',         'RAD',    'Diagnostic imaging'),
    ('Pathology',         'PATH',   'Laboratory and diagnostic testing'),
    ('General Medicine',  'GENMED', 'General adult medicine'),
    ('Nursing',           'NURS',   'Nursing services'),
    ('Administration',    'ADMIN',  'Hospital administration and front desk'),
    ('Pharmacy',          'PHARM',  'Medication dispensing')
) AS v(name, code, description)
WHERE h.code = 'CGH'
ON CONFLICT (hospital_id, code) DO NOTHING;

-- 3. Roles ------------------------------------------------------------------

INSERT INTO roles (name, description)
VALUES
    ('PATIENT',        'Hospital patient with access to their own records'),
    ('DOCTOR',         'Physician responsible for assigned patients'),
    ('NURSE',          'Nursing staff supporting assigned patients'),
    ('RECEPTIONIST',   'Front-desk scheduling and registration staff'),
    ('STAFF',          'General hospital staff (non-clinical)'),
    ('HOSPITAL_ADMIN', 'Administrator for a single hospital'),
    ('SUPER_ADMIN',    'Cross-hospital platform administrator')
ON CONFLICT (name) DO NOTHING;

-- 4. Permissions --------------------------------------------------------

INSERT INTO permissions (code, description)
VALUES
    ('view_own_profile',            'View own user profile'),
    ('view_own_appointments',       'View own appointments'),
    ('view_own_reports',            'View own lab reports'),
    ('view_own_prescriptions',      'View own prescriptions'),
    ('view_assigned_patients',      'View the list of patients assigned to the caller'),
    ('view_patient_basic_information', 'View a patient''s non-clinical identifying information'),
    ('view_patient_medical_records',   'View a patient''s medical records'),
    ('view_vital_records',          'View a patient''s recorded vitals'),
    ('create_clinical_notes',       'Author clinical notes for a patient'),
    ('update_nursing_notes',        'Author/update nursing notes for a patient'),
    ('manage_appointments',         'Reschedule/cancel/manage appointments'),
    ('create_appointments',         'Create new appointments'),
    ('view_admission_information',  'View patient admission/visit information'),
    ('manage_users',                'Create/update/deactivate hospital users'),
    ('manage_roles',                'Manage roles and role-permission mappings'),
    ('manage_departments',          'Create/update hospital departments'),
    ('manage_hospital_documents',   'Upload/manage hospital documents'),
    ('view_hospital_operations',    'View operational (non-clinical) hospital data'),
    ('view_hospital_analytics',     'View hospital-level analytics/reporting')
ON CONFLICT (code) DO NOTHING;

-- 5. Role -> permission mapping -----------------------------------------
-- Deliberately not universal: no role (other than the two admin roles,
-- which are expected to be broad) gets every permission.

INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM (VALUES
    ('PATIENT', 'view_own_profile'),
    ('PATIENT', 'view_own_appointments'),
    ('PATIENT', 'view_own_reports'),
    ('PATIENT', 'view_own_prescriptions'),

    ('DOCTOR', 'view_own_profile'),
    ('DOCTOR', 'view_assigned_patients'),
    ('DOCTOR', 'view_patient_basic_information'),
    ('DOCTOR', 'view_patient_medical_records'),
    ('DOCTOR', 'view_vital_records'),
    ('DOCTOR', 'create_clinical_notes'),
    ('DOCTOR', 'create_appointments'),
    ('DOCTOR', 'view_admission_information'),

    ('NURSE', 'view_own_profile'),
    ('NURSE', 'view_assigned_patients'),
    ('NURSE', 'view_patient_basic_information'),
    ('NURSE', 'view_vital_records'),
    ('NURSE', 'update_nursing_notes'),
    ('NURSE', 'view_admission_information'),

    ('RECEPTIONIST', 'view_own_profile'),
    ('RECEPTIONIST', 'view_patient_basic_information'),
    ('RECEPTIONIST', 'manage_appointments'),
    ('RECEPTIONIST', 'create_appointments'),
    ('RECEPTIONIST', 'view_admission_information'),
    ('RECEPTIONIST', 'view_hospital_operations'),

    ('STAFF', 'view_own_profile'),
    ('STAFF', 'manage_appointments'),
    ('STAFF', 'create_appointments'),
    ('STAFF', 'view_admission_information'),
    ('STAFF', 'manage_hospital_documents'),
    ('STAFF', 'view_hospital_operations'),

    ('HOSPITAL_ADMIN', 'view_own_profile'),
    ('HOSPITAL_ADMIN', 'manage_users'),
    ('HOSPITAL_ADMIN', 'manage_departments'),
    ('HOSPITAL_ADMIN', 'manage_hospital_documents'),
    ('HOSPITAL_ADMIN', 'manage_appointments'),
    ('HOSPITAL_ADMIN', 'view_admission_information'),
    ('HOSPITAL_ADMIN', 'view_hospital_operations'),
    ('HOSPITAL_ADMIN', 'view_hospital_analytics'),

    ('SUPER_ADMIN', 'view_own_profile'),
    ('SUPER_ADMIN', 'manage_users'),
    ('SUPER_ADMIN', 'manage_roles'),
    ('SUPER_ADMIN', 'manage_departments'),
    ('SUPER_ADMIN', 'manage_hospital_documents'),
    ('SUPER_ADMIN', 'view_hospital_operations'),
    ('SUPER_ADMIN', 'view_hospital_analytics')
) AS rp(role_name, permission_code)
JOIN roles r ON r.name = rp.role_name
JOIN permissions p ON p.code = rp.permission_code
ON CONFLICT (role_id, permission_id) DO NOTHING;

-- 6. Demo users (one per role, plus a second doctor) -----------------------

INSERT INTO users (auth_user_id, hospital_id, role_id, department_id, first_name, last_name, email, phone, is_active)
SELECT v.auth_user_id::uuid, h.id, r.id, d.id, v.first_name, v.last_name, v.email, v.phone, true
FROM hospitals h
CROSS JOIN (VALUES
    ('00000000-0000-0000-0000-000000000001', 'PATIENT',        NULL,     'Asha',   'Verma',    'asha.verma@example-patient.example',     '+91-9800000001'),
    ('00000000-0000-0000-0000-000000000002', 'DOCTOR',         'CARD',   'Rohan',  'Mehta',    'rohan.mehta@caresphere-demo.example',    '+91-9800000002'),
    ('00000000-0000-0000-0000-000000000003', 'DOCTOR',         'GENMED', 'Priya',  'Nair',     'priya.nair@caresphere-demo.example',     '+91-9800000003'),
    ('00000000-0000-0000-0000-000000000004', 'NURSE',          'NURS',   'Kavya',  'Iyer',     'kavya.iyer@caresphere-demo.example',     '+91-9800000004'),
    ('00000000-0000-0000-0000-000000000005', 'RECEPTIONIST',   'ADMIN',  'Sanjay', 'Rao',      'sanjay.rao@caresphere-demo.example',     '+91-9800000005'),
    ('00000000-0000-0000-0000-000000000006', 'STAFF',          'PHARM',  'Neha',   'Joshi',    'neha.joshi@caresphere-demo.example',     '+91-9800000006'),
    ('00000000-0000-0000-0000-000000000007', 'HOSPITAL_ADMIN', 'ADMIN',  'Vikram', 'Singh',    'vikram.singh@caresphere-demo.example',   '+91-9800000007'),
    ('00000000-0000-0000-0000-000000000008', 'SUPER_ADMIN',    NULL,     'Meera',  'Kapoor',   'meera.kapoor@caresphere-demo.example',   '+91-9800000008')
) AS v(auth_user_id, role_name, dept_code, first_name, last_name, email, phone)
JOIN roles r ON r.name = v.role_name
LEFT JOIN departments d ON d.code = v.dept_code AND d.hospital_id = h.id
WHERE h.code = 'CGH'
ON CONFLICT (auth_user_id) DO NOTHING;

-- 7. Demo patients ----------------------------------------------------------
-- Patient A has a portal login (linked to the PATIENT user above); patients
-- B and C were registered at the front desk and have no portal login yet,
-- demonstrating patients.user_id is optional.

INSERT INTO patients (
    user_id, hospital_id, patient_number, date_of_birth, gender, blood_group,
    phone, address, emergency_contact_name, emergency_contact_phone
)
SELECT u.id, h.id, v.patient_number, v.date_of_birth::date, v.gender, v.blood_group,
       v.phone, v.address, v.emergency_contact_name, v.emergency_contact_phone
FROM hospitals h
CROSS JOIN (VALUES
    ('CGH-P-0001', '1990-04-12', 'FEMALE', 'O+', '+91-9811111111', '12 MG Road, Pune',    'Rahul Verma',       '+91-9811111112', 'asha.verma@example-patient.example'),
    ('CGH-P-0002', '1985-11-02', 'MALE',   'B+', '+91-9822222222', '45 Lake View, Pune',  'Sunita Deshmukh',   '+91-9822222223', NULL),
    ('CGH-P-0003', '2001-07-19', 'FEMALE', 'A-', '+91-9833333333', '7 Church Street, Pune','Arjun Deshpande',  '+91-9833333334', NULL)
) AS v(patient_number, date_of_birth, gender, blood_group, phone, address, emergency_contact_name, emergency_contact_phone, user_email)
LEFT JOIN users u ON u.email = v.user_email
WHERE h.code = 'CGH'
ON CONFLICT (hospital_id, patient_number) DO NOTHING;

-- 8. Demo doctors -------------------------------------------------------

INSERT INTO doctors (user_id, hospital_id, department_id, employee_number, specialization, license_number)
SELECT u.id, h.id, d.id, v.employee_number, v.specialization, v.license_number
FROM hospitals h
CROSS JOIN (VALUES
    ('rohan.mehta@caresphere-demo.example', 'CARD',   'CGH-D-0001', 'Cardiology',       'CARD-LIC-1001'),
    ('priya.nair@caresphere-demo.example',  'GENMED', 'CGH-D-0002', 'General Medicine', 'GENMED-LIC-1002')
) AS v(user_email, dept_code, employee_number, specialization, license_number)
JOIN users u ON u.email = v.user_email
JOIN departments d ON d.code = v.dept_code AND d.hospital_id = h.id
WHERE h.code = 'CGH'
ON CONFLICT (hospital_id, employee_number) DO NOTHING;

-- 9. Demo staff (nurse, receptionist, pharmacy staff) ------------------

INSERT INTO staff (user_id, hospital_id, department_id, employee_number, designation)
SELECT u.id, h.id, d.id, v.employee_number, v.designation
FROM hospitals h
CROSS JOIN (VALUES
    ('kavya.iyer@caresphere-demo.example',  'NURS',  'CGH-S-0001', 'NURSE'),
    ('sanjay.rao@caresphere-demo.example',  'ADMIN', 'CGH-S-0002', 'RECEPTIONIST'),
    ('neha.joshi@caresphere-demo.example',  'PHARM', 'CGH-S-0003', 'PHARMACY_STAFF')
) AS v(user_email, dept_code, employee_number, designation)
JOIN users u ON u.email = v.user_email
JOIN departments d ON d.code = v.dept_code AND d.hospital_id = h.id
WHERE h.code = 'CGH'
ON CONFLICT (hospital_id, employee_number) DO NOTHING;

-- 10. Doctor <-> patient assignments -------------------------------------
-- Doctor A (Rohan Mehta) is assigned to Patient A and Patient B.
-- Doctor B (Priya Nair) is assigned only to Patient C.
-- This asymmetry is what lets Phase 4 authorization tests demonstrate both
-- an allowed access (Doctor A -> Patient A) and a denied one
-- (Doctor A -> Patient C).

INSERT INTO doctor_patient_assignments (doctor_id, patient_id, is_active)
SELECT doc.id, pat.id, true
FROM (VALUES
    ('CGH-D-0001', 'CGH-P-0001'),
    ('CGH-D-0001', 'CGH-P-0002'),
    ('CGH-D-0002', 'CGH-P-0003')
) AS v(employee_number, patient_number)
JOIN doctors doc ON doc.employee_number = v.employee_number
JOIN patients pat ON pat.patient_number = v.patient_number
WHERE NOT EXISTS (
    SELECT 1 FROM doctor_patient_assignments existing
    WHERE existing.doctor_id = doc.id AND existing.patient_id = pat.id AND existing.is_active
);

-- 11. Demo appointments --------------------------------------------------

INSERT INTO appointments (hospital_id, patient_id, doctor_id, department_id, appointment_date, appointment_time, status, reason, notes)
SELECT h.id, pat.id, doc.id, dept.id, v.appointment_date::date, v.appointment_time::time, v.status, v.reason, v.notes
FROM hospitals h
CROSS JOIN (VALUES
    ('CGH-P-0001', 'CGH-D-0001', 'CARD',   '2026-09-22', '09:30', 'CONFIRMED', 'Routine cardiology follow-up', NULL),
    ('CGH-P-0002', 'CGH-D-0001', 'CARD',   '2026-09-18', '11:00', 'COMPLETED', 'Chest pain evaluation', 'Advised lipid panel'),
    ('CGH-P-0003', 'CGH-D-0002', 'GENMED', '2026-09-25', '14:15', 'SCHEDULED', 'Annual general check-up', NULL),
    ('CGH-P-0001', 'CGH-D-0001', 'CARD',   '2026-08-30', '10:00', 'NO_SHOW',   'Follow-up on medication', NULL)
) AS v(patient_number, employee_number, dept_code, appointment_date, appointment_time, status, reason, notes)
JOIN patients pat ON pat.patient_number = v.patient_number
JOIN doctors doc ON doc.employee_number = v.employee_number
JOIN departments dept ON dept.code = v.dept_code AND dept.hospital_id = h.id
WHERE h.code = 'CGH';

-- 12. Demo medical records -----------------------------------------------

INSERT INTO medical_records (hospital_id, patient_id, doctor_id, record_type, title, description, clinical_notes, recorded_at)
SELECT h.id, pat.id, doc.id, v.record_type, v.title, v.description, v.clinical_notes, v.recorded_at::timestamptz
FROM hospitals h
CROSS JOIN (VALUES
    ('CGH-P-0001', 'CGH-D-0001', 'CONSULTATION', 'Cardiology consultation',
     'Initial cardiology consultation', 'Patient reports mild exertional chest discomfort. ECG within normal limits.', '2026-09-01 10:00:00+05:30'),
    ('CGH-P-0002', 'CGH-D-0001', 'DIAGNOSIS', 'Suspected hyperlipidemia',
     'Diagnosis following chest pain workup', 'Lipid panel ordered; dietary counseling provided.', '2026-09-18 11:30:00+05:30'),
    ('CGH-P-0003', 'CGH-D-0002', 'FOLLOW_UP', 'General medicine follow-up',
     'Routine follow-up visit', 'No acute concerns reported. Continue current care plan.', '2026-09-10 09:00:00+05:30')
) AS v(patient_number, employee_number, record_type, title, description, clinical_notes, recorded_at)
JOIN patients pat ON pat.patient_number = v.patient_number
JOIN doctors doc ON doc.employee_number = v.employee_number
WHERE h.code = 'CGH';

-- 13. Demo lab reports ----------------------------------------------------

INSERT INTO lab_reports (hospital_id, patient_id, ordered_by_doctor_id, test_name, test_code, result, unit, reference_range, status, reported_at)
SELECT h.id, pat.id, doc.id, v.test_name, v.test_code, v.result, v.unit, v.reference_range, v.status, v.reported_at::timestamptz
FROM hospitals h
CROSS JOIN (VALUES
    ('CGH-P-0001', 'CGH-D-0001', 'Complete Blood Count', 'CBC',   'Within normal limits', NULL,    'See report', 'COMPLETED',   '2026-09-02 08:00:00+05:30'),
    ('CGH-P-0002', 'CGH-D-0001', 'Lipid Profile',        'LIPID', NULL,                   NULL,    NULL,         'IN_PROGRESS', NULL),
    ('CGH-P-0003', 'CGH-D-0002', 'Fasting Blood Glucose', 'FBG',  '92',                   'mg/dL', '70-99',      'COMPLETED',   '2026-09-10 08:30:00+05:30')
) AS v(patient_number, employee_number, test_name, test_code, result, unit, reference_range, status, reported_at)
JOIN patients pat ON pat.patient_number = v.patient_number
JOIN doctors doc ON doc.employee_number = v.employee_number
WHERE h.code = 'CGH';

-- 14. Demo prescriptions --------------------------------------------------

INSERT INTO prescriptions (hospital_id, patient_id, doctor_id, medication_name, dosage, frequency, route, duration, instructions, prescribed_at)
SELECT h.id, pat.id, doc.id, v.medication_name, v.dosage, v.frequency, v.route, v.duration, v.instructions, v.prescribed_at::timestamptz
FROM hospitals h
CROSS JOIN (VALUES
    ('CGH-P-0001', 'CGH-D-0001', 'Atorvastatin', '10mg',  'Once daily',   'Oral', '30 days', 'Take at night', '2026-09-01 10:15:00+05:30'),
    ('CGH-P-0002', 'CGH-D-0001', 'Metformin',    '500mg', 'Twice daily',  'Oral', '90 days', 'Take with meals', '2026-09-18 11:45:00+05:30'),
    ('CGH-P-0003', 'CGH-D-0002', 'Amoxicillin',  '500mg', 'Three times daily', 'Oral', '7 days', 'Complete full course', '2026-09-10 09:15:00+05:30')
) AS v(patient_number, employee_number, medication_name, dosage, frequency, route, duration, instructions, prescribed_at)
JOIN patients pat ON pat.patient_number = v.patient_number
JOIN doctors doc ON doc.employee_number = v.employee_number
WHERE h.code = 'CGH';

COMMIT;
