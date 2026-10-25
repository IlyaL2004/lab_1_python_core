"""Чтение JSONL из открытого потока с сохранением владения у вызывающего кода."""

from io import StringIO
import json

import pytest

from school_lab.repositories import InMemorySubmissionRepository
from school_lab.services import SubmissionService
from school_lab.sources import StreamSource, import_source


def test_stream_reads_current_position_and_preserves_line_errors(raw_submission: dict) -> None:
    """Чтение начинается с текущей позиции, строки нумеруются заново, ошибки не мешают продолжению."""
    stream = StringIO("already read\n\n{broken\nnull\n" + json.dumps(raw_submission) + "\n")
    assert stream.readline() == "already read\n"

    with StreamSource(stream).open() as records:
        result = list(records)

    assert [record.line_no for record in result] == [1, 2, 3, 4]
    assert [record.error.code for record in result[:2]] == ["invalid_json", "invalid_json"]
    assert result[2].raw is None
    assert result[2].error is None
    assert result[3].raw == raw_submission
    assert result[3].error is None
    assert not stream.closed
    stream.close()


def test_stream_early_exit_closes_iterator_but_preserves_remaining_input(raw_submission: dict) -> None:
    """Выход закрывает итератор, а повторное открытие продолжает читать сам поток."""
    second = {**raw_submission, "submission_id": "submission-2"}
    stream = StringIO(json.dumps(raw_submission) + "\n" + json.dumps(second) + "\n")
    source = StreamSource(stream)

    with source.open() as records:
        for record in records:
            assert record.raw == raw_submission
            break

    with pytest.raises(StopIteration):
        next(records)
    assert not stream.closed
    with source.open() as remaining:
        result = list(remaining)
    assert len(result) == 1
    assert result[0].line_no == 1
    assert result[0].raw == second
    stream.close()


def test_stream_consumer_exception_does_not_close_stream(raw_submission: dict) -> None:
    """Исключение потребителя передаётся без замены, поток остаётся доступным владельцу."""
    line = json.dumps(raw_submission) + "\n"
    stream = StringIO(line * 2)
    error = RuntimeError("consumer failed")

    with pytest.raises(RuntimeError) as captured:
        with StreamSource(stream).open() as records:
            assert next(records).raw == raw_submission
            raise error

    assert captured.value is error
    with pytest.raises(StopIteration):
        next(records)
    assert not stream.closed
    assert stream.readline() == line
    stream.close()


def test_stream_import_requires_seek_for_repeat(raw_submission: dict) -> None:
    """Общий импорт принимает сдачу; повтор после конца пуст, после seek обнаруживает дубликат."""
    stream = StringIO(json.dumps(raw_submission) + "\n")
    source = StreamSource(stream)
    repository = InMemorySubmissionRepository()
    service = SubmissionService(repository)

    first = import_source(source, service)
    exhausted = import_source(source, service)
    stream.seek(0)
    repeated = import_source(source, service)

    assert first.to_dict() == {"accepted": ["submission-1"], "duplicate": [], "errors": []}
    assert exhausted.to_dict() == {"accepted": [], "duplicate": [], "errors": []}
    assert repeated.to_dict() == {"accepted": [], "duplicate": ["submission-1"], "errors": []}
    assert len(repository) == 1
    assert not stream.closed
    stream.close()


def test_stream_read_failure_preserves_accepted_data_and_caller_ownership(raw_submission: dict) -> None:
    """Сбой чтения не откатывает принятую строку и не закрывает переданный поток."""
    error = OSError("read failed")

    class FailingStream(StringIO):
        """Поток, который возвращает одну строку и затем сообщает об ошибке чтения."""

        def __next__(self) -> str:
            """Вход: без аргументов. Выход: первая строка или исходная ошибка чтения."""
            if self.tell() != 0:
                raise error
            return super().__next__()

    stream = FailingStream(json.dumps(raw_submission) + "\n")
    repository = InMemorySubmissionRepository()

    with pytest.raises(OSError) as captured:
        import_source(StreamSource(stream), SubmissionService(repository))

    assert captured.value is error
    assert len(repository) == 1
    assert repository.get("submission-1").code == raw_submission["code"]
    assert not stream.closed
    stream.close()
