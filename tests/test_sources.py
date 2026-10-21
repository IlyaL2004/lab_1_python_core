"""Общий контракт источников, построчное чтение и освобождение файлов."""

from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path

import pytest

from school_lab.errors import SubmissionError
from school_lab.repositories import InMemorySubmissionRepository
from school_lab.services import SubmissionService
from school_lab.sources import (
    InMemorySource,
    JSONLSource,
    SourceRecord,
    import_source,
)
import school_lab.sources as sources


@pytest.fixture(params=["jsonl", "memory"])
def make_source(request, temp_dir: Path):
    """Создать источник одного из двух видов для одинаковых проверок."""
    def create(values: list[dict]):
        if request.param == "memory":
            return InMemorySource(values)
        path = temp_dir / "submissions.jsonl"
        lines = [json.dumps(value, ensure_ascii=False) for value in values]
        path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
        return JSONLSource(path)

    return create


@pytest.fixture
def opened_files(monkeypatch):
    """Запомнить настоящие файлы, открытые на чтение, для проверки закрытия."""
    handles = []
    original_open = Path.open

    def tracked_open(path, *args, **kwargs):
        handle = original_open(path, *args, **kwargs)
        if "r" in handle.mode:
            handles.append(handle)
        return handle

    monkeypatch.setattr(Path, "open", tracked_open)
    return handles


def test_sources_share_acceptance_rules(make_source, raw_submission: dict) -> None:
    """Оба источника одинаково обрабатывают приём, повтор, конфликт и схему."""
    conflict = deepcopy(raw_submission)
    conflict["code"] = "print(99)\n"
    invalid = deepcopy(raw_submission)
    invalid["student_id"] = ""
    last = deepcopy(raw_submission)
    last["submission_id"] = "submission-2"
    source = make_source([raw_submission, raw_submission, conflict, invalid, last])
    repository = InMemorySubmissionRepository()

    summary = import_source(source, SubmissionService(repository))

    assert summary.accepted == ["submission-1", "submission-2"]
    assert summary.duplicate == ["submission-1"]
    assert [(error["line_no"], error["code"]) for error in summary.errors] == [
        (3, "conflict"), (4, "schema_error"),
    ]
    assert len(repository) == 2
    assert repository.get("submission-1").code == raw_submission["code"]


def test_empty_source(make_source) -> None:
    """Пустой источник даёт пустой отчёт без ошибок."""
    summary = import_source(
        make_source([]), SubmissionService(InMemorySubmissionRepository())
    )

    assert summary.to_dict() == {"accepted": [], "duplicate": [], "errors": []}


def test_source_can_be_opened_again(make_source, raw_submission: dict) -> None:
    """Повторное открытие начинает чтение с первой записи."""
    source = make_source([raw_submission])
    service = SubmissionService(InMemorySubmissionRepository())

    first = import_source(source, service)
    repeated = import_source(source, service)

    assert first.accepted == ["submission-1"]
    assert repeated.to_dict() == {
        "accepted": [], "duplicate": ["submission-1"], "errors": [],
    }


def test_open_iterators_are_independent(make_source, raw_submission: dict) -> None:
    """Продвижение одного итератора не пропускает записи в другом."""
    second = deepcopy(raw_submission)
    second["submission_id"] = "submission-2"
    source = make_source([raw_submission, second])

    with source.open() as left, source.open() as right:
        for records in (left, right):
            first_record = next(records)
            second_record = next(records)
            assert first_record.raw == raw_submission
            assert first_record.line_no == 1
            assert first_record.error is None
            assert second_record.raw == second
            assert second_record.line_no == 2
            assert second_record.error is None
        assert list(left) == []
        assert list(right) == []


def test_memory_source_copies_input(raw_submission: dict) -> None:
    """Изменение исходного списка и вложенного словаря не меняет источник."""
    values = [deepcopy(raw_submission)]
    source = InMemorySource(values)
    values[0]["ai_declaration"]["verified_by_student"] = False
    values.append(deepcopy(raw_submission))

    with source.open() as records:
        result = list(records)

    assert len(result) == 1
    assert result[0].line_no == 1
    assert result[0].raw == raw_submission


def test_memory_source_copies_each_result(raw_submission: dict) -> None:
    """Изменение выданной записи не затрагивает следующие и повторные чтения."""
    source = InMemorySource([raw_submission, raw_submission])

    with source.open() as records:
        first = next(records)
        first.raw["ai_declaration"]["verified_by_student"] = False
        assert next(records).raw == raw_submission

    with source.open() as records:
        assert next(records).raw == raw_submission

    assert raw_submission["ai_declaration"]["verified_by_student"] is True


def test_jsonl_keeps_errors_and_physical_lines(temp_dir: Path, raw_submission: dict) -> None:
    """Пустая и повреждённая строки дают ошибки, следующая запись доступна."""
    path = temp_dir / "mixed.jsonl"
    path.write_text("\n{broken\n" + json.dumps(raw_submission) + "\n", encoding="utf-8")

    with JSONLSource(path).open() as records:
        result = list(records)

    assert [record.line_no for record in result] == [1, 2, 3]
    assert [record.error.code for record in result[:2]] == ["invalid_json", "invalid_json"]
    assert result[2].error is None
    assert result[2].raw == raw_submission


def test_json_null_is_a_schema_error(temp_dir: Path, raw_submission: dict) -> None:
    """JSON null передаётся сервису и не считается повреждённым JSON."""
    path = temp_dir / "null.jsonl"
    path.write_text("null\n" + json.dumps(raw_submission) + "\n", encoding="utf-8")
    source = JSONLSource(path)

    with source.open() as records:
        first = next(records)
        assert first.raw is None
        assert first.error is None

    summary = import_source(source, SubmissionService(InMemorySubmissionRepository()))

    assert summary.accepted == ["submission-1"]
    assert len(summary.errors) == 1
    assert summary.errors[0]["line_no"] == 1
    assert summary.errors[0]["code"] == "schema_error"


@pytest.mark.parametrize("mode", ["complete", "early", "exception", "no-iteration"])
def test_jsonl_closes_file(temp_dir: Path, raw_submission: dict, opened_files, mode: str) -> None:
    """Файл закрывается после полного чтения, раннего выхода и исключения."""
    path = temp_dir / "close.jsonl"
    path.write_text((json.dumps(raw_submission) + "\n") * 2, encoding="utf-8")
    source = JSONLSource(path)
    assert not opened_files

    if mode == "exception":
        with pytest.raises(RuntimeError, match="consumer failed"):
            with source.open() as records:
                next(records)
                raise RuntimeError("consumer failed")
    else:
        with source.open() as records:
            if mode == "complete":
                list(records)
            elif mode == "early":
                for record in records:
                    assert record.line_no == 1
                    break
                assert opened_files
                assert all(not handle.closed for handle in opened_files)

    assert opened_files
    assert all(handle.closed for handle in opened_files)


def test_jsonl_closes_file_after_unicode_error(temp_dir: Path, opened_files) -> None:
    """Неверная кодировка передаётся вызывающему коду, файл закрывается."""
    path = temp_dir / "encoding.jsonl"
    path.write_bytes(b"\xff\xfe\xff")

    with pytest.raises(UnicodeError):
        with JSONLSource(path).open() as records:
            list(records)

    assert opened_files
    assert all(handle.closed for handle in opened_files)


def test_jsonl_reads_and_decodes_on_demand(temp_dir: Path, raw_submission: dict, monkeypatch) -> None:
    """Следующая строка не читается и не разбирается до запроса итератора."""
    path = temp_dir / "lazy.jsonl"
    first_line = json.dumps(raw_submission) + "\n"
    path.write_text(first_line + "{broken\n", encoding="utf-8")
    original_open = Path.open
    original_decode = sources.decode_json
    read_lines = []
    decoded_lines = []

    @contextmanager
    def tracked_open(path, *args, **kwargs):
        with original_open(path, *args, **kwargs) as handle:
            def lines():
                for line in handle:
                    read_lines.append(line)
                    yield line
            yield lines()

    def tracked_decode(line):
        decoded_lines.append(line)
        return original_decode(line)

    monkeypatch.setattr(Path, "open", tracked_open)
    monkeypatch.setattr(sources, "decode_json", tracked_decode)

    with JSONLSource(path).open() as records:
        assert read_lines == decoded_lines == []
        assert next(records).raw == raw_submission
        assert read_lines == decoded_lines == [first_line]
        assert next(records).error.code == "invalid_json"
        assert read_lines == decoded_lines == [first_line, "{broken\n"]


def test_custom_source_does_not_need_inheritance(raw_submission: dict) -> None:
    """Для нового источника достаточно метода open с общим форматом записей."""
    closed = []
    error = SubmissionError("invalid_json", "json", "Повреждённая запись")

    class CustomSource:
        @contextmanager
        def open(self):
            try:
                yield iter([
                    SourceRecord(10, error=error),
                    SourceRecord(20, raw=raw_submission),
                    SourceRecord(30, raw=raw_submission),
                ])
            finally:
                closed.append(True)

    summary = import_source(CustomSource(), SubmissionService(InMemorySubmissionRepository()))

    assert summary.to_dict() == {
        "accepted": ["submission-1"],
        "duplicate": ["submission-1"],
        "errors": [{"line_no": 10, **error.to_dict()}],
    }
    assert closed == [True]


def test_import_closes_source_after_unexpected_error(raw_submission: dict, monkeypatch) -> None:
    """Неожиданное исключение сервиса не скрывается и завершает контекст источника."""
    closed = []

    class CustomSource:
        @contextmanager
        def open(self):
            try:
                yield iter([SourceRecord(1, raw=raw_submission)])
            finally:
                closed.append(True)

    def fail(raw):
        raise RuntimeError("service failed")

    service = SubmissionService(InMemorySubmissionRepository())
    monkeypatch.setattr(service, "accept", fail)

    with pytest.raises(RuntimeError, match="service failed"):
        import_source(CustomSource(), service)

    assert closed == [True]


def test_read_failure_keeps_accepted_records(raw_submission: dict) -> None:
    """Ошибка чтения закрывает источник, ранее принятые данные остаются в репозитории."""
    closed = []

    class FailingSource:
        @contextmanager
        def open(self):
            def records():
                yield SourceRecord(1, raw=raw_submission)
                raise OSError("read failed")

            try:
                yield records()
            finally:
                closed.append(True)

    repository = InMemorySubmissionRepository()

    with pytest.raises(OSError, match="read failed"):
        import_source(FailingSource(), SubmissionService(repository))

    assert closed == [True]
    assert len(repository) == 1
    assert repository.get("submission-1").code == raw_submission["code"]
