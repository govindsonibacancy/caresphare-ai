import datetime
import uuid

from pydantic import BaseModel

# One explicit response schema per resource, matching what's reasonable to
# expose to a caller who already passed authorize() for that specific
# resource - no internal audit columns beyond created_at/updated_at (not
# sensitive), no cross-resource dumps, no fields from unrelated tables. See
# docs/SECURE_DATA_APIS.md, "Response-data minimization".


class PatientResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID | None
    hospital_id: uuid.UUID
    patient_number: str
    date_of_birth: datetime.date | None
    gender: str | None
    blood_group: str | None
    phone: str | None
    address: str | None
    emergency_contact_name: str | None
    emergency_contact_phone: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class DoctorResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    hospital_id: uuid.UUID
    department_id: uuid.UUID | None
    employee_number: str
    specialization: str | None
    license_number: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class DepartmentResponse(BaseModel):
    id: uuid.UUID
    hospital_id: uuid.UUID
    name: str
    code: str
    description: str | None
    is_active: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime


class AppointmentResponse(BaseModel):
    id: uuid.UUID
    hospital_id: uuid.UUID
    patient_id: uuid.UUID
    doctor_id: uuid.UUID
    department_id: uuid.UUID | None
    appointment_date: datetime.date
    appointment_time: datetime.time
    status: str
    reason: str | None
    notes: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class MedicalRecordResponse(BaseModel):
    id: uuid.UUID
    hospital_id: uuid.UUID
    patient_id: uuid.UUID
    doctor_id: uuid.UUID
    record_type: str
    title: str
    description: str | None
    clinical_notes: str | None
    recorded_at: datetime.datetime
    created_at: datetime.datetime
    updated_at: datetime.datetime


class LabReportResponse(BaseModel):
    id: uuid.UUID
    hospital_id: uuid.UUID
    patient_id: uuid.UUID
    ordered_by_doctor_id: uuid.UUID
    test_name: str
    test_code: str | None
    result: str | None
    unit: str | None
    reference_range: str | None
    status: str
    reported_at: datetime.datetime | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class PrescriptionResponse(BaseModel):
    id: uuid.UUID
    hospital_id: uuid.UUID
    patient_id: uuid.UUID
    doctor_id: uuid.UUID
    medication_name: str
    dosage: str | None
    frequency: str | None
    route: str | None
    duration: str | None
    instructions: str | None
    prescribed_at: datetime.datetime
    created_at: datetime.datetime
    updated_at: datetime.datetime
