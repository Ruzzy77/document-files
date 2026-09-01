"""Stable errors for the document extraction runtime."""

from __future__ import annotations


class DocumentExtractionError(Exception):
    """Base error raised by the document extraction boundary."""

    code = "document_extraction_error"

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


class BudgetExceededError(DocumentExtractionError):
    code = "budget_exceeded"


class ExtractionError(DocumentExtractionError):
    code = "extraction_error"
