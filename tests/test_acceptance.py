"""Проверки хеширования, хранения и повторного приёма сдач."""

from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import subprocess
import sys

import pytest

from school_lab.errors import SubmissionError
from school_lab.hashing import canonical_json, stable_hash, submission_hash
from school_lab.parsing import parse_submission
from school_lab.repositories import InMemorySubmissionRepository
from school_lab.services import SubmissionService


def test_hash_ignores_key_order(raw_submission: dict) -> None:
    """Порядок ключей, в том числе вложенных, не меняет SHA-256."""
    reordered = dict(reversed(list(raw_submission.items())))
    reordered["ai_declaration"] = dict(
        reversed(list(raw_submission["ai_declaration"].items()))
    )

    assert canonical_json(raw_submission) == canonical_json(reordered)
    assert stable_hash(raw_submission) == stable_hash(reordered)
    assert submission_hash(parse_submission(raw_submission)) == stable_hash(reordered)


def test_hash_preserves_content(raw_submission: dict) -> None:
    """Канонизация сохраняет пробелы, переводы строк и исходные значения."""
    raw_submission["text"] = "  Объяснение\r\n"
    raw_submission["code"] = "\nprint(2 + 3)  \n"
    original = deepcopy(raw_submission)

    canonical = canonical_json(raw_submission)
    digest = stable_hash(raw_submission)

    assert json.loads(canonical) == original
    assert raw_submission == original
    assert digest == hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@pytest.mark.parametrize(
    "field, value",
    [
        ("code", "print(2 + 3) \n"),
        ("student_id", "student-2"),
        ("submitted_at", "2026-10-20T12:30:00+03:00"),
        ("template_id", "template-1"),
        ("ai_declaration", {
            "used": True,
            "tool": "Помощник",
            "purpose": "Проверка примера",
            "verified_by_student": True,
        }),
    ],
)
def test_hash_changes_with_submission(raw_submission: dict, field: str, value: object) -> None:
    """Хеш учитывает содержимое, метаданные и вложенную AI-декларацию."""
    initial_hash = submission_hash(parse_submission(raw_submission))
    raw_submission[field] = value

    assert submission_hash(parse_submission(raw_submission)) != initial_hash


def test_hash_is_stable_between_processes(raw_submission: dict) -> None:
    """Отдельный процесс получает тот же отпечаток сдачи."""
    command = (
        "import json, sys; "
        "from school_lab.hashing import stable_hash; "
        "print(stable_hash(json.load(sys.stdin)))"
    )
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", command],
        input=json.dumps(raw_submission, ensure_ascii=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )

    assert result.stdout.strip() == stable_hash(raw_submission)
    assert result.stderr == ""


def test_accept_then_duplicate(raw_submission: dict) -> None:
    """Повтор той же сдачи сохраняет прежний объект и размер хранилища."""
    repository = InMemorySubmissionRepository()
    service = SubmissionService(repository)

    accepted = service.accept(raw_submission)
    stored = repository.get(raw_submission["submission_id"])
    duplicate = service.accept(deepcopy(raw_submission))

    assert accepted.status == "accepted"
    assert accepted.submission_id == raw_submission["submission_id"]
    assert accepted.content_hash == submission_hash(stored)
    assert asdict(stored) == raw_submission
    assert duplicate.status == "duplicate"
    assert duplicate.submission_id == accepted.submission_id
    assert duplicate.content_hash == accepted.content_hash
    assert len(repository) == 1
    assert repository.get(accepted.submission_id) is stored


def test_conflict_preserves_original(raw_submission: dict) -> None:
    """Другой код под занятым ID вызывает конфликт без перезаписи."""
    repository = InMemorySubmissionRepository()
    service = SubmissionService(repository)
    service.accept(raw_submission)
    stored = repository.get(raw_submission["submission_id"])
    raw_submission["code"] = "print(8 + 3)\n"

    with pytest.raises(SubmissionError) as caught:
        service.accept(raw_submission)

    assert caught.value.code == "conflict"
    assert caught.value.field == "submission_id"
    assert len(repository) == 1
    assert repository.get(raw_submission["submission_id"]) is stored
    assert stored.code == "print(2 + 3)\n"


def test_new_id_creates_new_version(raw_submission: dict) -> None:
    """Изменённая работа с новым ID хранится рядом с исходной версией."""
    repository = InMemorySubmissionRepository()
    service = SubmissionService(repository)
    first = service.accept(raw_submission)
    raw_submission.update(submission_id="submission-2", code="print(8 + 3)\n")

    second = service.accept(raw_submission)

    assert second.status == "accepted"
    assert len(repository) == 2
    assert repository.get(first.submission_id).code == "print(2 + 3)\n"
    assert repository.get(second.submission_id).code == "print(8 + 3)\n"


def test_invalid_submission_does_not_change_repository(raw_submission: dict) -> None:
    """Ошибка схемы не добавляет запись и не затрагивает принятые работы."""
    repository = InMemorySubmissionRepository()
    service = SubmissionService(repository)
    accepted = service.accept(raw_submission)
    stored = repository.get(accepted.submission_id)
    raw_submission.update(submission_id="invalid", student_id="")

    with pytest.raises(SubmissionError) as caught:
        service.accept(raw_submission)

    assert caught.value.code == "schema_error"
    assert len(repository) == 1
    assert repository.get("invalid") is None
    assert repository.get(accepted.submission_id) is stored


def test_repository_rejects_overwrite(raw_submission: dict) -> None:
    """Прямое добавление занятого ID тоже не перезаписывает сдачу."""
    repository = InMemorySubmissionRepository()
    original = parse_submission(raw_submission)
    repository.add(original)
    raw_submission["text"] = "Другое объяснение"

    with pytest.raises(SubmissionError) as caught:
        repository.add(parse_submission(raw_submission))

    assert caught.value.code == "conflict"
    assert repository.get(original.submission_id) is original
    assert len(repository) == 1


def test_repositories_do_not_share_state(raw_submission: dict) -> None:
    """Два экземпляра репозитория имеют независимые словари."""
    first = InMemorySubmissionRepository()
    second = InMemorySubmissionRepository()
    SubmissionService(first).accept(raw_submission)

    assert len(first) == 1
    assert len(second) == 0
    assert second.get(raw_submission["submission_id"]) is None
