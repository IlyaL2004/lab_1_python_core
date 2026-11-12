"""Токеновые признаки, исходные координаты и ленивый read → validate → features."""
import json
from contextlib import closing
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from school_lab.errors import SubmissionError
from school_lab.features import (
    InvalidSource, prepare_submission, source_tokens, token_occurrences,
)
from school_lab.hashing import submission_hash
from school_lab.models import Assignment, Evidence
from school_lab.parsing import parse_submission
from school_lab.pipeline import iter_features
from school_lab.similarity_models import SimilarityConfig


@pytest.fixture
def assignment(raw_submission):
    """Вход: пример сдачи. Выход: её задание без разрешённого шаблона."""
    return Assignment(raw_submission["assignment_id"], "", ())


def track_file_open(monkeypatch, path):
    """Вход: monkeypatch и путь. Выход: список реальных открытых файлов для проверки закрытия."""
    actual_open = Path.open
    opened = []

    def tracked_open(self, *args, **kwargs):
        stream = actual_open(self, *args, **kwargs)
        if self == path:
            opened.append(stream)
        return stream

    monkeypatch.setattr(Path, "open", tracked_open)
    return opened


@pytest.mark.parametrize("method", ["ast", "unknown", None, True])
def test_config_accepts_only_token_method(method):
    """Выбор ещё не реализованного метода отклоняется до подготовки сдач."""
    with pytest.raises(ValueError, match="method"):
        SimilarityConfig(method=method)


@pytest.mark.parametrize("n", [0, -1, True, 1.5])
def test_invalid_window_size_is_rejected(n):
    """Размер окна должен быть положительным целым и в конфигурации, и в прямом API."""
    with pytest.raises(ValueError):
        SimilarityConfig(n=n)
    with pytest.raises(ValueError):
        token_occurrences("x = 1\n", n=n)


def test_tokens_keep_names_and_literals_without_comments_or_service_tokens():
    """Значимые токены сохраняют текст и строку оригинала после пустоты и комментариев."""
    tokens = source_tokens("\n# heading\nanswer = 'text'  # explanation\n")
    assert [item.value for item in tokens] == [
        ("NAME", "answer"), ("OP", "="), ("STRING", "'text'"),
    ]
    assert [(item.start_line, item.end_line) for item in tokens] == [(3, 3)] * 3


def test_trigrams_have_expected_values_and_original_ranges():
    """Окна по три токена могут пересекать перевод строки без изменения координат."""
    occurrences = token_occurrences("x = 1\ny = x\n")
    assert [json.loads(item.feature) for item in occurrences] == [
        [["NAME", "x"], ["OP", "="], ["NUMBER", "1"]],
        [["OP", "="], ["NUMBER", "1"], ["NAME", "y"]],
        [["NUMBER", "1"], ["NAME", "y"], ["OP", "="]],
        [["NAME", "y"], ["OP", "="], ["NAME", "x"]],
    ]
    assert [(item.start_line, item.end_line) for item in occurrences] == [(1, 1), (1, 2), (1, 2), (2, 2)]


def test_formatting_does_not_rename_identifiers_or_change_literals():
    """Пробелы и комментарии не меняют признаки; имя и значение остаются различимы."""
    variants = ["answer = 12\n", "# heading\nanswer=12 # note\n", "other = 12\n", "answer = 13\n"]
    features = [{item.feature for item in token_occurrences(source)} for source in variants]
    assert features[0] == features[1]
    assert features[0] != features[2] and features[0] != features[3]


@pytest.mark.parametrize("source", ["", "# comment only\n", "\n \n", "pass\n"])
def test_empty_or_short_source_has_no_trigrams(raw_submission, assignment, source):
    """Отсутствие полного окна даёт пустые признаки без ошибки исходника."""
    prepared = prepare_submission(replace(parse_submission(raw_submission), code=source), assignment, SimilarityConfig())
    assert prepared.status == "ok" and prepared.reason is None
    assert not prepared.features and not prepared.occurrences


def test_template_exclusion_does_not_join_unrelated_neighbors():
    """Удаление шаблонных окон не создаёт нового окна между строками до и после него."""
    source = "left = 1\nfrom typing import Iterable\nright = 2\n"
    kept = token_occurrences(source, excluded=((2, 2),))
    assert [(item.start_line, item.end_line) for item in kept] == [(1, 1), (3, 3)]
    assert [json.loads(item.feature) for item in kept] == [
        [["NAME", "left"], ["OP", "="], ["NUMBER", "1"]],
        [["NAME", "right"], ["OP", "="], ["NUMBER", "2"]],
    ]


def test_exclusion_inside_multiline_literal_removes_intersecting_windows():
    """Частичное пересечение многострочного токена исключает всё содержащее его окно."""
    source = 'value = """first\nsecond\nthird"""\nother = 2\n'
    kept = token_occurrences(source, excluded=((2, 2),))
    assert len(kept) == 1
    assert (kept[0].start_line, kept[0].end_line) == (4, 4)


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("prefix", ["", "\f"])
def test_preparation_preserves_physical_coordinates_and_original_source(raw_submission, assignment, newline, prefix):
    """Шаблон исключается на исходной строке при CR/LF и Unicode внутри литерала."""
    source = newline.join(["def solve(x):", prefix + "    marker = 'a\u2028b\x85c'", "    return x"]) + newline
    original = replace(parse_submission(raw_submission), code=source)
    snapshot, digest = asdict(original), submission_hash(original)
    assignment = replace(assignment, allowed_template="    return x")
    config = SimilarityConfig(exclude_template=False)
    complete = prepare_submission(original, assignment, config)
    excluded = prepare_submission(original, assignment, replace(config, exclude_template=True))
    returns = [item for item in source_tokens(source) if item.value == ("NAME", "return")]
    assert [(item.start_line, item.end_line) for item in returns] == [(3, 3)]
    assert excluded.status == "ok" and excluded.features
    assert excluded.excluded_template == (Evidence(original.submission_id, "code", 3, 3),)
    assert all(item.end_line < 3 for item in excluded.occurrences)
    assert any('"return"' in feature for feature in complete.features)
    assert not any('"return"' in feature for feature in excluded.features)
    assert complete.config_hash != excluded.config_hash
    assert original.code == complete.code == excluded.code == source
    assert asdict(original) == snapshot and submission_hash(original) == digest


@pytest.mark.parametrize("source", ["def bad(:\n", "value = 'unfinished\n", "value = \x00\n"])
def test_invalid_python_has_explicit_status_without_partial_features(raw_submission, assignment, source):
    """Ошибки синтаксиса не превращаются в частичные признаки допустимой сдачи."""
    with pytest.raises(InvalidSource):
        source_tokens(source)
    prepared = prepare_submission(replace(parse_submission(raw_submission), code=source), assignment, SimilarityConfig())
    assert prepared.status == "invalid_source" and prepared.reason
    assert not prepared.features and not prepared.occurrences


def test_token_method_accepts_comprehension_without_ast_normalization(raw_submission, assignment):
    """Ограничения будущего структурного метода не применяются к токенам Python."""
    source = "result = [item + 1 for item in values]\n"
    prepared = prepare_submission(replace(parse_submission(raw_submission), code=source), assignment, SimilarityConfig())
    assert prepared.status == "ok" and prepared.features


def test_source_is_never_executed(raw_submission, assignment, temp_dir):
    """Даже вызов на верхнем уровне исходника не создаёт файл при извлечении токенов."""
    marker = temp_dir / "must-not-exist"
    source = f"open({str(marker)!r}, 'w').write('unexpected')\n"
    prepared = prepare_submission(replace(parse_submission(raw_submission), code=source), assignment, SimilarityConfig())
    assert prepared.status == "ok" and prepared.features
    assert not marker.exists()


def test_preparation_rejects_context_for_other_assignment(raw_submission):
    """Правила другого задания нельзя применить к принятой сдаче."""
    with pytest.raises(ValueError, match="другому заданию"):
        prepare_submission(parse_submission(raw_submission), Assignment("other", "", ()), SimilarityConfig())


def test_feature_pipeline_is_lazy_and_closes_after_early_exit(temp_dir, raw_submission, assignment, monkeypatch):
    """Создание генератора не открывает файл, а первый next не обрабатывает следующие записи."""
    path = temp_dir / "rows.jsonl"
    errors = []
    iterator = iter_features(path, {assignment.assignment_id: assignment}, SimilarityConfig(), errors)
    second = {**raw_submission, "submission_id": "second"}
    path.write_text(json.dumps(raw_submission) + "\n" + json.dumps(second) + "\n{broken\n", encoding="utf-8")
    opened = track_file_open(monkeypatch, path)
    prepared_ids = []

    def tracked_prepare(submission, context, config):
        prepared_ids.append(submission.submission_id)
        return prepare_submission(submission, context, config)

    monkeypatch.setattr("school_lab.pipeline.prepare_submission", tracked_prepare)
    assert not opened and not prepared_ids
    with closing(iterator):
        first = next(iterator)
        assert first.submission_id == raw_submission["submission_id"] and first.features
        assert prepared_ids == [raw_submission["submission_id"]] and errors == []
        assert len(opened) == 1 and not opened[0].closed
    assert opened[0].closed
    assert prepared_ids == [raw_submission["submission_id"]]


def test_feature_pipeline_distinguishes_record_errors_from_invalid_python(temp_dir, raw_submission, assignment):
    """Ошибки записи накапливаются, а некорректный Python остаётся явным результатом метода."""
    path = temp_dir / "errors.jsonl"
    invalid_schema = {key: value for key, value in raw_submission.items() if key != "student_id"}
    conflict = {**raw_submission, "code": "print(99)"}
    invalid_source = {**raw_submission, "submission_id": "bad-code", "code": "def bad(:\n"}
    after = {**raw_submission, "submission_id": "after"}
    rows = [invalid_schema, raw_submission, raw_submission, conflict, invalid_source, after]
    path.write_text("{broken\n" + "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    errors = []
    prepared = list(iter_features(path, {assignment.assignment_id: assignment}, SimilarityConfig(), errors))
    assert [(item.submission_id, item.status) for item in prepared] == [
        (raw_submission["submission_id"], "ok"), ("bad-code", "invalid_source"), ("after", "ok"),
    ]
    assert [(item["line_no"], item["code"]) for item in errors] == [
        (1, "invalid_json"), (2, "schema_error"), (5, "conflict"),
    ]


@pytest.mark.parametrize("collect_errors", [False, True])
def test_unknown_assignment_stops_pipeline_and_closes_file(temp_dir, raw_submission, monkeypatch, collect_errors):
    """Неизвестное задание — ошибка контекста даже при накоплении ошибок отдельных записей."""
    path = temp_dir / "unknown.jsonl"
    path.write_text(json.dumps(raw_submission) + "\n", encoding="utf-8")
    opened = track_file_open(monkeypatch, path)
    errors = [] if collect_errors else None
    with pytest.raises(SubmissionError) as caught:
        list(iter_features(path, {}, SimilarityConfig(), errors))
    assert caught.value.field == "assignment_id"
    assert len(opened) == 1 and opened[0].closed
    assert errors is None or errors == []


def test_record_error_without_collection_closes_feature_pipeline(temp_dir, assignment, monkeypatch):
    """Без списка ошибок первый неверный JSON завершает поток и освобождает файл."""
    path = temp_dir / "bad.jsonl"
    path.write_text("{broken\n", encoding="utf-8")
    opened = track_file_open(monkeypatch, path)
    with pytest.raises(SubmissionError):
        list(iter_features(path, {assignment.assignment_id: assignment}, SimilarityConfig()))
    assert len(opened) == 1 and opened[0].closed


def test_source_file_error_is_not_collected_as_record_error(temp_dir, assignment):
    """Отсутствующий файл передаёт OSError вызывающему коду при первом чтении."""
    errors = []
    iterator = iter_features(temp_dir / "missing.jsonl", {assignment.assignment_id: assignment}, SimilarityConfig(), errors)
    with pytest.raises(FileNotFoundError):
        next(iterator)
    assert errors == []
