from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


class Page(BaseModel, Generic[T]):
    """Every list endpoint in app/api/{patients,doctors,departments,
    appointments,medical_records,lab_reports,prescriptions}.py returns this
    shape - never a bare array, so a client can never mistake a full table
    scan for "everything" (see docs/SECURE_DATA_APIS.md, "Pagination").
    """

    items: list[T]
    page: int
    page_size: int
    total: int
