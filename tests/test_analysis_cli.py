"""Проверки команды analyze: JSON-отчёты, ошибки и независимый запуск CLI."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from school_lab import cli
from school_lab.application import build_application
from school_lab.hashing import stable_hash, submission_hash
from school_lab.models import Assignment
from school_lab.parsing import parse_submission
from school_lab.reports import AnalysisContext


@pytest.fixture
def input_files(temp_dir: Path, raw_submission: dict) -> tuple[Path, Path]:
    """Вход: временная папка и сдача. Выход: пути JSONL сдач и JSON заданий."""
    submissions = temp_dir / "submissions.jsonl"
    assignments = temp_dir / "assignments.json"
    submissions.write_text(json.dumps(raw_submission, ensure_ascii=False) + "\n", encoding="utf-8")
    assignments.write_text(json.dumps([{
        "assignment_id": "lab-1",
        "allowed_template": "print(2 + 3)\n",
        "requirements": ["Пояснить решение"],
    }], ensure_ascii=False), encoding="utf-8")
    return submissions, assignments


def test_cli_default_analyzers_skip_duplicate(input_files, raw_submission, capsys) -> None:
    """По умолчанию работают length/template, повторная сдача не создаёт новые отчёты."""
    submissions, assignments = input_files
    line = json.dumps(raw_submission, ensure_ascii=False)
    submissions.write_text(line + "\n" + line + "\n", encoding="utf-8")

    result = cli.main(["analyze", str(submissions), "--assignments", str(assignments)])
    captured = capsys.readouterr()
    output = json.loads(captured.out)

    assert result == 0
    assert captured.err == ""
    assert output["accepted"] == ["submission-1"]
    assert output["duplicate"] == ["submission-1"]
    assert output["errors"] == []
    assert output["analysis_errors"] == []
    assert [report["analyzer_id"] for report in output["reports"]] == ["length", "template"]
    assert all(report["submission_hash"] == submission_hash(parse_submission(raw_submission))
               for report in output["reports"])


def test_cli_selects_analyzers_and_config_version(input_files, capsys) -> None:
    """Повторяемый --analyzer задаёт состав отчётов, версия настроек влияет на хеш."""
    submissions, assignments = input_files

    result = cli.main([
        "analyze", str(submissions), "--assignments", str(assignments),
        "--analyzer", "declaration", "--analyzer", "missing-explanation",
        "--config-version", "student-v2",
    ])
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    context = AnalysisContext(
        Assignment("lab-1", "print(2 + 3)\n", ("Пояснить решение",)),
        config_version="student-v2",
    )

    assert result == 0
    assert captured.err == ""
    assert [report["analyzer_id"] for report in output["reports"]] == [
        "declaration", "missing-explanation",
    ]
    assert all(report["config_hash"] == stable_hash(context.config_payload()) for report in output["reports"])


def test_cli_analysis_continues_after_input_errors(input_files, raw_submission, capsys) -> None:
    """Повреждённый JSON и конфликт не мешают анализу принятой версии и следующей сдачи."""
    submissions, assignments = input_files
    conflict = deepcopy(raw_submission)
    conflict["code"] = "print(99)\n"
    second = deepcopy(raw_submission)
    second["submission_id"] = "submission-2"
    lines = [json.dumps(raw_submission), "{broken", json.dumps(conflict), json.dumps(second)]
    submissions.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = cli.main([
        "analyze", str(submissions), "--assignments", str(assignments), "--analyzer", "length",
    ])
    captured = capsys.readouterr()
    output = json.loads(captured.out)

    assert result == 1
    assert captured.err == ""
    assert output["accepted"] == ["submission-1", "submission-2"]
    assert [(error["line_no"], error["code"]) for error in output["errors"]] == [
        (2, "invalid_json"), (3, "conflict"),
    ]
    assert [report["submission_id"] for report in output["reports"]] == ["submission-1", "submission-2"]
    assert output["reports"][0]["submission_hash"] == submission_hash(parse_submission(raw_submission))
    assert output["analysis_errors"] == []


def test_cli_isolates_analyzer_and_assignment_errors(input_files, raw_submission, monkeypatch, capsys) -> None:
    """Сбой расширения, неизвестное имя и отсутствие задания сохраняются отдельно от отчётов."""
    class BrokenAnalyzer:
        """Тестовое расширение, которое завершает анализ исключением."""
        analyzer_id = "broken"
        analyzer_version = "1.0"

        def analyze(self, submission, context):
            """Вход: сдача и контекст. Выход: RuntimeError вместо отчёта."""
            raise RuntimeError("Учебный сбой")

    app = build_application()
    app.registry.register("broken", BrokenAnalyzer)
    monkeypatch.setattr(cli, "build_application", lambda **kwargs: app)
    submissions, assignments = input_files
    second = deepcopy(raw_submission)
    second.update(submission_id="submission-2", assignment_id="missing-assignment")
    submissions.write_text(json.dumps(raw_submission) + "\n" + json.dumps(second) + "\n", encoding="utf-8")

    result = cli.main([
        "analyze", str(submissions), "--assignments", str(assignments),
        "--analyzer", "broken", "--analyzer", "length", "--analyzer", "unknown",
    ])
    captured = capsys.readouterr()
    output = json.loads(captured.out)

    assert result == 1
    assert captured.err == ""
    assert output["errors"] == []
    assert len(output["reports"]) == 1
    assert output["reports"][0]["analyzer_id"] == "length"
    assert output["reports"][0]["submission_id"] == "submission-1"
    errors = output["analysis_errors"]
    assert len(errors) == 3
    assert [(error["submission_id"], error.get("analyzer_id")) for error in errors] == [
        ("submission-1", "broken"), ("submission-1", "unknown"), ("submission-2", None),
    ]
    assert all(error["code"] == "analysis_error" and error["message"] for error in errors)
    assert len(app.repository) == 2


def test_cli_rejects_invalid_assignment_configuration(input_files, capsys) -> None:
    """Неверная схема, дубликаты ID и повторные ключи JSON дают ошибку конфигурации."""
    submissions, assignments = input_files
    valid = {"assignment_id": "lab-1", "allowed_template": "", "requirements": []}
    invalid_documents = [
        "{}", "{broken", json.dumps([{"assignment_id": "lab-1"}]),
        json.dumps([{**valid, "extra": True}]),
        json.dumps([{**valid, "requirements": "Пояснить"}]),
        json.dumps([{**valid, "requirements": [" "]}]),
        json.dumps([{**valid, "allowed_template": None}]),
        json.dumps([valid, valid]),
        '[{"assignment_id":"lab-1","assignment_id":"lab-2","allowed_template":"","requirements":[]}]',
    ]

    for document in invalid_documents:
        assignments.write_text(document, encoding="utf-8")
        result = cli.main(["analyze", str(submissions), "--assignments", str(assignments)])
        captured = capsys.readouterr()

        assert result == 2, document
        assert captured.out == ""
        error = json.loads(captured.err)
        assert error["code"] == "configuration_error"
        assert error["message"]


def test_cli_rejects_blank_config_version(input_files, capsys) -> None:
    """Пустая версия настроек отклоняется до вывода отчётов."""
    submissions, assignments = input_files

    result = cli.main([
        "analyze", str(submissions), "--assignments", str(assignments), "--config-version", " ",
    ])
    captured = capsys.readouterr()

    assert result == 2
    assert captured.out == ""
    assert json.loads(captured.err)["code"] == "configuration_error"


@pytest.mark.parametrize("failed_file", ["submissions", "assignments"])
def test_cli_unavailable_file_returns_two(input_files, failed_file, capsys) -> None:
    """Недоступный файл сдач или заданий даёт source_unavailable в stderr."""
    submissions, assignments = input_files
    if failed_file == "submissions":
        submissions = submissions.with_name("missing.jsonl")
    else:
        assignments.write_bytes(b"\xff\xfe\xff")

    result = cli.main(["analyze", str(submissions), "--assignments", str(assignments)])
    captured = capsys.readouterr()

    assert result == 2
    assert captured.out == ""
    assert json.loads(captured.err)["code"] == "source_unavailable"


def test_analyze_module_runs_in_separate_process(input_files) -> None:
    """Установленный пакет выполняет анализ и печатает JSON при запуске через -m."""
    submissions, assignments = input_files

    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "school_lab.cli", "analyze", str(submissions),
         "--assignments", str(assignments)],
        capture_output=True, text=True, encoding="utf-8",
    )

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    output = json.loads(result.stdout)
    assert output["accepted"] == ["submission-1"]
    assert output["analysis_errors"] == []
    assert [report["analyzer_id"] for report in output["reports"]] == ["length", "template"]
