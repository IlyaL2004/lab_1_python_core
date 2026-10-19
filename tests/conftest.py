"""Общие примеры данных для тестов приёма сдачи."""

import pytest


@pytest.fixture
def raw_submission() -> dict:
    """Вернуть новый словарь корректной учебной сдачи для каждого теста."""
    return {
        "schema_version": 1,
        "submission_id": "submission-1",
        "student_id": "student-1",
        "assignment_id": "lab-1",
        "submitted_at": "2026-10-19T12:30:00+03:00",
        "text": "Программа выводит сумму двух чисел.",
        "code": "print(2 + 3)\n",
        "template_id": None,
        "ai_declaration": {
            "used": False,
            "tool": None,
            "purpose": None,
            "verified_by_student": True,
        },
    }
