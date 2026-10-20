"""Проверки построчного импорта JSONL и командной строки."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from school_lab.cli import main
from school_lab.repositories import InMemorySubmissionRepository
from school_lab.services import SubmissionService
from school_lab.sources import ImportSummary, import_file


def test_import_continues_after_errors(temp_dir: Path, raw_submission: dict) -> None:
    """Ошибки JSON, конфликта и схемы не мешают приёму последней строки."""
    conflict = deepcopy(raw_submission)
    conflict["code"] = "print(9)\n"
    invalid = deepcopy(raw_submission)
    invalid.update(submission_id="invalid", student_id="")
    last = deepcopy(raw_submission)
    last["submission_id"] = "submission-2"
    lines = [
        json.dumps(raw_submission, ensure_ascii=False),
        '{"broken":',
        json.dumps(raw_submission, ensure_ascii=False),
        json.dumps(conflict, ensure_ascii=False),
        json.dumps(invalid, ensure_ascii=False),
        json.dumps(last, ensure_ascii=False),
    ]
    path = temp_dir / "mixed.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    repository = InMemorySubmissionRepository()

    summary = import_file(path, SubmissionService(repository))

    assert summary.accepted == ["submission-1", "submission-2"]
    assert summary.duplicate == ["submission-1"]
    assert [error["line_no"] for error in summary.errors] == [2, 4, 5]
    assert [error["code"] for error in summary.errors] == [
        "invalid_json", "conflict", "schema_error",
    ]
    assert [error["field"] for error in summary.errors] == [
        "json", "submission_id", "student_id",
    ]
    assert all(error["message"] for error in summary.errors)
    assert len(repository) == 2
    assert repository.get("submission-1").code == raw_submission["code"]
    assert repository.get("submission-2").text == last["text"]
    assert json.loads(json.dumps(summary.to_dict())) == {
        "accepted": summary.accepted,
        "duplicate": summary.duplicate,
        "errors": summary.errors,
    }


def test_blank_line_keeps_physical_line_number(temp_dir: Path, raw_submission: dict) -> None:
    """Пустая строка считается ошибкой JSON и участвует в нумерации."""
    path = temp_dir / "blank.jsonl"
    path.write_text("\n" + json.dumps(raw_submission) + "\n", encoding="utf-8")

    summary = import_file(path, SubmissionService(InMemorySubmissionRepository()))

    assert summary.accepted == ["submission-1"]
    assert len(summary.errors) == 1
    assert summary.errors[0]["line_no"] == 1
    assert summary.errors[0]["code"] == "invalid_json"


def test_repeated_import_uses_same_repository(temp_dir: Path, raw_submission: dict) -> None:
    """Повтор файла в том же сервисе даёт только дубликаты."""
    path = temp_dir / "valid.jsonl"
    path.write_text(json.dumps(raw_submission) + "\n", encoding="utf-8")
    repository = InMemorySubmissionRepository()
    service = SubmissionService(repository)

    first = import_file(path, service)
    repeated = import_file(path, service)

    assert first.to_dict() == {
        "accepted": ["submission-1"], "duplicate": [], "errors": [],
    }
    assert repeated.to_dict() == {
        "accepted": [], "duplicate": ["submission-1"], "errors": [],
    }
    assert len(repository) == 1


def test_summaries_do_not_share_lists() -> None:
    """Изменения одного итога импорта не меняют другой итог."""
    first = ImportSummary()
    second = ImportSummary()
    first.accepted.append("submission-1")
    first.duplicate.append("submission-1")
    first.errors.append({"line_no": 2, "code": "invalid_json"})

    assert second.to_dict() == {"accepted": [], "duplicate": [], "errors": []}


@pytest.mark.parametrize("missing", [True, False], ids=["missing", "invalid-utf8"])
def test_source_failure_propagates(temp_dir: Path, missing: bool) -> None:
    """Отсутствующий файл и неверная кодировка передаются вызывающему коду."""
    path = temp_dir / "unavailable.jsonl"
    expected_error = FileNotFoundError
    if not missing:
        path.write_bytes(b"\xff\xfe\xff")
        expected_error = UnicodeError

    with pytest.raises(expected_error):
        import_file(path, SubmissionService(InMemorySubmissionRepository()))


def test_cli_success_and_duplicate(temp_dir: Path, raw_submission: dict, capsys) -> None:
    """Успешный CLI возвращает 0 и один JSON-итог без сообщений stderr."""
    path = temp_dir / "valid.jsonl"
    line = json.dumps(raw_submission, ensure_ascii=False)
    path.write_text(line + "\n" + line + "\n", encoding="utf-8")

    exit_code = main(["import", str(path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "accepted": ["submission-1"], "duplicate": ["submission-1"], "errors": [],
    }


def test_cli_record_error_returns_one(temp_dir: Path, raw_submission: dict, capsys) -> None:
    """При ошибке строки CLI возвращает 1, сохраняя общий итог в stdout."""
    path = temp_dir / "mixed.jsonl"
    path.write_text("{broken\n" + json.dumps(raw_submission) + "\n", encoding="utf-8")

    exit_code = main(["import", str(path)])
    captured = capsys.readouterr()
    summary = json.loads(captured.out)

    assert exit_code == 1
    assert captured.err == ""
    assert summary["accepted"] == ["submission-1"]
    assert summary["duplicate"] == []
    assert len(summary["errors"]) == 1
    assert summary["errors"][0]["line_no"] == 1
    assert summary["errors"][0]["code"] == "invalid_json"


@pytest.mark.parametrize("missing", [True, False], ids=["missing", "invalid-utf8"])
def test_cli_source_error_returns_two(temp_dir: Path, missing: bool, capsys) -> None:
    """Ошибка файла даёт код 2 и структурированное сообщение в stderr."""
    path = temp_dir / "unavailable.jsonl"
    if not missing:
        path.write_bytes(b"\xff\xfe\xff")

    exit_code = main(["import", str(path)])
    captured = capsys.readouterr()
    error = json.loads(captured.err)

    assert exit_code == 2
    assert captured.out == ""
    assert error["code"] == "source_unavailable"
    assert error["message"]


def test_cli_module_runs_in_new_process(temp_dir: Path, raw_submission: dict) -> None:
    """Два запуска модуля создают независимые хранилища и принимают запись."""
    path = temp_dir / "valid.jsonl"
    path.write_text(json.dumps(raw_submission, ensure_ascii=False) + "\n", encoding="utf-8")

    for _ in range(2):
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "school_lab.cli", "import", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        assert result.returncode == 0, result.stderr
        assert result.stderr == ""
        assert json.loads(result.stdout) == {
            "accepted": ["submission-1"], "duplicate": [], "errors": [],
        }
