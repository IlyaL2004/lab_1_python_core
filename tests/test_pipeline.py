"""Проверки ленивого чтения и пограничных Unicode-данных при переходе к ЛР2."""

import copy
import io
import json
from contextlib import closing

import pytest

from school_lab.cli import main
from school_lab.errors import SubmissionError
from school_lab.parsing import parse_submission
from school_lab.pipeline import iter_submissions


def write_rows(path, rows):
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_pipeline_is_lazy_and_closes_after_early_exit(temp_dir, raw_submission, monkeypatch):
    path = temp_dir / "rows.jsonl"
    iterator = iter_submissions(path)
    # Создание генератора не пытается открыть пока ещё отсутствующий файл.
    write_rows(path, [raw_submission, dict(raw_submission, submission_id="second")])
    from pathlib import Path

    actual_open = Path.open
    opened = []

    def tracked_open(self, *args, **kwargs):
        stream = actual_open(self, *args, **kwargs)
        if self == path:
            opened.append(stream)
        return stream

    monkeypatch.setattr(Path, "open", tracked_open)
    with closing(iterator):
        assert next(iterator).submission_id == raw_submission["submission_id"]
        assert not opened[0].closed
    assert opened[0].closed


def test_pipeline_keeps_duplicate_conflict_policy(temp_dir, raw_submission):
    path = temp_dir / "rows.jsonl"
    conflict = dict(raw_submission, code="print(99)")
    after = dict(raw_submission, submission_id="after")
    write_rows(path, [raw_submission, raw_submission, conflict, after])
    errors = []
    result = list(iter_submissions(path, errors))
    assert [item.submission_id for item in result] == [raw_submission["submission_id"], "after"]
    assert [(item["line_no"], item["code"]) for item in errors] == [(3, "conflict")]


def test_bad_unicode_is_one_record_error_and_import_continues(temp_dir, raw_submission, capsys):
    path = temp_dir / "rows.jsonl"
    bad = dict(raw_submission, text=chr(0xD800))
    after = dict(raw_submission, submission_id="after")
    write_rows(path, [bad, after])
    errors = []
    assert [item.submission_id for item in iter_submissions(path, errors)] == ["after"]
    assert errors[0]["line_no"] == 1
    assert errors[0]["field"] == "text"
    assert errors[0]["code"] == "schema_error"
    assert main(["import", str(path)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["accepted"] == ["after"]
    assert result["errors"][0]["field"] == "text"


@pytest.mark.parametrize("field", ["text", "code", "submission_id", "student_id", "assignment_id", "template_id", "submitted_at"])
def test_parser_rejects_surrogates_without_changing_input(raw_submission, field):
    raw_submission[field] = chr(0xDFFF)
    original = copy.deepcopy(raw_submission)
    with pytest.raises(SubmissionError) as exc:
        parse_submission(raw_submission)
    assert exc.value.field == field
    assert raw_submission == original


def test_pipeline_without_error_list_raises(temp_dir):
    path = temp_dir / "bad.jsonl"
    path.write_text("{broken\n", encoding="utf-8")
    with pytest.raises(SubmissionError):
        list(iter_submissions(path))


def test_cli_unicode_output_with_cp1251(temp_dir, raw_submission, monkeypatch):
    path = temp_dir / "unicode.jsonl"
    raw_submission["submission_id"] = "id-" + chr(0x1F600)
    write_rows(path, [raw_submission])
    output = io.BytesIO()
    stream = io.TextIOWrapper(output, encoding="cp1251", write_through=True)
    monkeypatch.setattr("sys.stdout", stream)
    assert main(["import", str(path)]) == 0
    assert json.loads(output.getvalue().decode("cp1251"))["accepted"] == [raw_submission["submission_id"]]
    stream.detach()
