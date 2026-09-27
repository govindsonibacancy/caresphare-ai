"""Upload validation: extension, a real content-based check (not just the
extension), size, and filename safety. Runs before anything is written to
disk or the database - see docs/RAG_INGESTION.md, "File validation".
"""

import dataclasses

_MAX_FILENAME_LENGTH = 255

# Deliberately small and reliably processable, per Phase 7's instructions -
# not "every format Python can theoretically parse".
SUPPORTED_EXTENSIONS: dict[str, str] = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain",
    "md": "text/markdown",
}

_PDF_MAGIC = b"%PDF-"
_ZIP_MAGIC = b"PK\x03\x04"  # .docx is a zip archive under the hood


class DocumentValidationError(Exception):
    """Base for every upload-validation failure. `str(exc)` is always safe
    to return to the client directly - no internal detail leaks into any of
    these messages."""


class UnsupportedFileType(DocumentValidationError):
    pass


class FileTooLarge(DocumentValidationError):
    pass


class EmptyFile(DocumentValidationError):
    pass


class UnsafeFilename(DocumentValidationError):
    pass


class CorruptFile(DocumentValidationError):
    """Extension looked fine, but the file's own bytes don't match what
    that type should look like - e.g. a `.pdf` that isn't actually a PDF."""


@dataclasses.dataclass(frozen=True)
class ValidatedUpload:
    extension: str
    mime_type: str
    size: int


def validate_upload(*, filename: str, content: bytes, max_size_bytes: int) -> ValidatedUpload:
    """Never uses `filename` for anything beyond reading its extension - it
    is not used to construct a storage path (see storage.py, which derives
    the on-disk name entirely from the document's own server-generated id).
    """
    if not filename or "/" in filename or "\\" in filename or "\x00" in filename:
        raise UnsafeFilename("Filename is missing or contains unsafe characters.")
    if len(filename) > _MAX_FILENAME_LENGTH:
        raise UnsafeFilename("Filename is too long.")
    if filename.startswith("."):
        raise UnsafeFilename("Filename must not start with a dot.")

    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFileType(
            f"Unsupported file type '.{extension}'. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}."
        )

    if len(content) == 0:
        raise EmptyFile("The uploaded file is empty.")
    if len(content) > max_size_bytes:
        raise FileTooLarge(f"File exceeds the {max_size_bytes // (1024 * 1024)} MB limit.")

    # A real (if lightweight) content check, not just trusting the
    # extension: PDF/DOCX have a fixed magic-byte signature; txt/md have
    # none, so the closest available check is that the content actually
    # decodes as UTF-8 text.
    if extension == "pdf" and not content.startswith(_PDF_MAGIC):
        raise CorruptFile("File does not look like a valid PDF.")
    if extension == "docx" and not content.startswith(_ZIP_MAGIC):
        raise CorruptFile("File does not look like a valid DOCX.")
    if extension in ("txt", "md"):
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CorruptFile("File is not valid UTF-8 text.") from exc

    return ValidatedUpload(extension=extension, mime_type=SUPPORTED_EXTENSIONS[extension], size=len(content))
