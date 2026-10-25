"""Сохранение поведения функции при измерении времени и измерения в CLI."""

from copy import deepcopy
from dataclasses import FrozenInstanceError
from inspect import signature
from itertools import count
import json
from pathlib import Path

import pytest

from school_lab import cli, timing
from school_lab.application import build_application
from school_lab.timing import Measurement, timed


def test_timed_preserves_signature_and_metadata() -> None:
    """Декоратор сохраняет имя, документацию, аннотации и сигнатуру функции."""
    def calculate(value: int, /, *, increment: int = 1) -> int:
        """Вход: число и приращение. Выход: их сумма."""
        return value + increment

    wrapped = timed([])(calculate)

    assert wrapped.__wrapped__ is calculate
    assert wrapped.__name__ == calculate.__name__
    assert wrapped.__qualname__ == calculate.__qualname__
    assert wrapped.__doc__ == calculate.__doc__
    assert wrapped.__annotations__ == calculate.__annotations__
    assert signature(wrapped) == signature(calculate)


def test_timed_preserves_arguments_and_result(monkeypatch) -> None:
    """Аргументы передаются один раз, возвращается тот же объект, время записано в мс."""
    moments = iter([12.0, 12.125])
    monkeypatch.setattr(timing, "perf_counter", lambda: next(moments))
    measurements = []
    calls = []
    result = {"value": 7}

    def calculate(value: int, /, *, increment: int = 1) -> dict:
        """Вход: число и приращение. Выход: заранее подготовленный результат."""
        calls.append((value, increment))
        return result

    wrapped = timed(measurements)(calculate)

    assert wrapped(3, increment=4) is result
    assert calls == [(3, 4)]
    assert measurements == [Measurement(calculate.__qualname__, 125.0)]
    assert isinstance(measurements[0].duration_ms, float)


@pytest.mark.parametrize("error", [ValueError("failure"), SystemExit(3)])
def test_timed_records_failure_and_preserves_exception(monkeypatch, error) -> None:
    """Даже при исключении время записывается, наружу выходит исходный объект ошибки."""
    moments = iter([20.0, 20.25])
    monkeypatch.setattr(timing, "perf_counter", lambda: next(moments))
    measurements = []

    @timed(measurements)
    def fail() -> None:
        """Вход: нет. Выход: переданное в тест исключение."""
        raise error

    with pytest.raises(type(error)) as captured:
        fail()

    assert captured.value is error
    assert measurements == [Measurement(fail.__qualname__, 250.0)]


def test_timed_appends_each_call_and_keeps_lists_independent(monkeypatch) -> None:
    """Повторные вызовы дополняют переданный список, другой список не изменяется."""
    moments = iter([1.0, 1.1, 2.0, 2.2, 3.0, 3.3])
    monkeypatch.setattr(timing, "perf_counter", lambda: next(moments))
    first = [Measurement("previous", 5.0)]
    second = []

    def echo(value: int) -> int:
        """Вход: число. Выход: то же число."""
        return value

    left = timed(first)(echo)
    right = timed(second)(echo)

    assert left(10) == 10
    assert left(20) == 20
    assert second == []
    assert right(30) == 30
    assert [item.duration_ms for item in first] == pytest.approx([5.0, 100.0, 200.0])
    assert [item.duration_ms for item in second] == pytest.approx([300.0])
    assert all(item.function == echo.__qualname__ for item in first[1:] + second)


def test_measurement_is_immutable() -> None:
    """Готовое измерение нельзя случайно изменить после сохранения."""
    measurement = Measurement("calculate", 2.5)

    with pytest.raises(FrozenInstanceError):
        measurement.duration_ms = 10.0

    assert measurement == Measurement("calculate", 2.5)


@pytest.fixture
def timing_files(temp_dir: Path, raw_submission: dict) -> tuple[Path, Path]:
    """Вход: папка и сдача. Выход: файлы сдач и задания для проверки измерений."""
    submissions = temp_dir / "submissions.jsonl"
    assignments = temp_dir / "assignments.json"
    submissions.write_text(json.dumps(raw_submission) + "\n", encoding="utf-8")
    assignments.write_text(json.dumps([{
        "assignment_id": "lab-1", "allowed_template": "", "requirements": [],
    }]), encoding="utf-8")
    return submissions, assignments


def test_cli_measures_each_analysis_without_accumulating_between_runs(
    timing_files, raw_submission, monkeypatch, capsys,
) -> None:
    """Дубликат не анализируется снова; следующий запуск начинает новый список времени."""
    moments = count(start=1.0, step=0.001)
    monkeypatch.setattr(timing, "perf_counter", lambda: next(moments))
    submissions, assignments = timing_files
    submissions.write_text((json.dumps(raw_submission) + "\n") * 2, encoding="utf-8")
    reports = []

    for _ in range(2):
        result = cli.main(["analyze", str(submissions), "--assignments", str(assignments)])
        captured = capsys.readouterr()
        output = json.loads(captured.out)

        assert result == 0
        assert captured.err == ""
        assert output["accepted"] == ["submission-1"]
        assert output["duplicate"] == ["submission-1"]
        assert output["analysis_errors"] == []
        assert len(output["reports"]) == len(output["measurements"]) == 2
        assert [item["function"] for item in output["measurements"]] == ["AnalysisService.run"] * 2
        assert [item["duration_ms"] for item in output["measurements"]] == pytest.approx([1.0, 1.0])
        reports.append([
            (item["submission_hash"], item["config_hash"], item["findings"])
            for item in output["reports"]
        ])

    assert reports[0] == reports[1]


def test_cli_measures_failed_analysis_but_not_missing_assignment(
    timing_files, raw_submission, monkeypatch, capsys,
) -> None:
    """Измеряются успешные и неуспешные попытки анализа; ошибки импорта и задания — нет."""
    class BrokenAnalyzer:
        """Анализатор с предсказуемой ошибкой для проверки продолжения обработки."""
        analyzer_id = "broken"
        analyzer_version = "1.0"

        def analyze(self, submission, context):
            """Вход: сдача и контекст. Выход: RuntimeError вместо отчёта."""
            raise RuntimeError("analysis failed")

    app = build_application()
    app.registry.register("broken", BrokenAnalyzer)
    monkeypatch.setattr(cli, "build_application", lambda **kwargs: app)
    moments = count(start=1.0, step=0.001)
    monkeypatch.setattr(timing, "perf_counter", lambda: next(moments))
    submissions, assignments = timing_files
    missing_assignment = deepcopy(raw_submission)
    missing_assignment.update(submission_id="submission-2", assignment_id="missing")
    lines = [json.dumps(raw_submission), "{broken", json.dumps(missing_assignment)]
    submissions.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = cli.main([
        "analyze", str(submissions), "--assignments", str(assignments),
        "--analyzer", "broken", "--analyzer", "length", "--analyzer", "unknown",
    ])
    captured = capsys.readouterr()
    output = json.loads(captured.out)

    assert result == 1
    assert captured.err == ""
    assert [(item["line_no"], item["code"]) for item in output["errors"]] == [(2, "invalid_json")]
    assert [report["analyzer_id"] for report in output["reports"]] == ["length"]
    assert [(item["submission_id"], item.get("analyzer_id")) for item in output["analysis_errors"]] == [
        ("submission-1", "broken"), ("submission-1", "unknown"), ("submission-2", None),
    ]
    assert [item["function"] for item in output["measurements"]] == ["AnalysisService.run"] * 3
    assert [item["duration_ms"] for item in output["measurements"]] == pytest.approx([1.0] * 3)


def test_cli_empty_input_has_no_measurements(timing_files, capsys) -> None:
    """При отсутствии сдач результат содержит пустой список измерений."""
    submissions, assignments = timing_files
    submissions.write_text("", encoding="utf-8")

    result = cli.main(["analyze", str(submissions), "--assignments", str(assignments)])
    captured = capsys.readouterr()
    output = json.loads(captured.out)

    assert result == 0
    assert captured.err == ""
    assert output["reports"] == output["analysis_errors"] == output["measurements"] == []


def test_cli_import_does_not_measure_analysis(timing_files, capsys) -> None:
    """Команда import сохраняет прежний формат отчёта без измерений времени."""
    submissions, _ = timing_files

    result = cli.main(["import", str(submissions)])
    captured = capsys.readouterr()

    assert result == 0
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "accepted": ["submission-1"], "duplicate": [], "errors": [],
    }
