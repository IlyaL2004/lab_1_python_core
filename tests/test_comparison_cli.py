"""Контракт token-сравнения через CLI на малых независимых входах."""

import json
import subprocess
import sys

import pytest

from school_lab.cli import main
from school_lab.parsing import load_assignments
from school_lab.pipeline import file_sha256, iter_submissions
from school_lab.similarity import compare_submissions
from school_lab.similarity_models import SimilarityConfig


def write_rows(path, rows):
    """Записать отдельный JSONL-корпус для проверки CLI."""
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def inputs(directory, raw_submission):
    """Создать запрос, двух кандидатов, версию автора и другое задание."""
    source = "def solve(value):\n    answer = value + 1\n    return answer\n"
    first = dict(raw_submission, submission_id="q", student_id="a", code=source)
    rows = [
        first,
        dict(first, submission_id="z", student_id="b"),
        dict(first, submission_id="b", student_id="c"),
        dict(first, submission_id="same-author"),
        dict(first, submission_id="other-task", assignment_id="other", student_id="d"),
    ]
    corpus = directory / "corpus.jsonl"
    write_rows(corpus, rows)
    assignments = directory / "assignments.json"
    assignments.write_text(json.dumps([
        {"assignment_id": raw_submission["assignment_id"], "allowed_template": "", "requirements": []},
        {"assignment_id": "other", "allowed_template": "", "requirements": []},
    ]), encoding="utf-8")
    return corpus, assignments


def test_compare_query_filters_and_stable_ties(temp_dir, raw_submission, capsys):
    corpus, assignments = inputs(temp_dir, raw_submission)
    args = ["compare", str(corpus), "--assignments", str(assignments),
            "--query", "q", "--top-k", "1", "--method", "token"]
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["documents"] == 5
    assert result["comparisons"] == 2
    assert len(result["pairs"]) == 1
    assert result["pairs"][0]["right_id"] == "b"
    assert result["pairs"][0]["score"] == 1
    assert result["pairs"][0]["review_required"] is True
    assert result["undefined_pairs"] == result["errors"] == []


def test_compare_all_pairs_follow_public_contract(temp_dir, raw_submission, capsys):
    corpus, assignments_path = inputs(temp_dir, raw_submission)
    assert main(["compare", str(corpus), "--assignments", str(assignments_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["schema_version"] == 1
    assert result["query"] is None
    assert result["corpus_sha256"] == file_sha256(corpus)
    assert result["comparisons"] == len(result["pairs"]) == 5
    assert [(row["left_id"], row["right_id"]) for row in result["pairs"]] == [
        ("q", "z"), ("q", "b"), ("z", "b"), ("z", "same-author"), ("b", "same-author"),
    ]
    documents = {item.submission_id: item for item in iter_submissions(corpus)}
    assignments = load_assignments(assignments_path)
    config = SimilarityConfig(**result["config"])
    for row in result["pairs"]:
        left, right = documents[row["left_id"]], documents[row["right_id"]]
        expected = compare_submissions(
            left, right, assignments[left.assignment_id], config,
            threshold=result["threshold"],
        )
        assert row == expected.to_dict()


def test_compare_file_output_equals_stdout(temp_dir, raw_submission, capsys):
    corpus, assignments = inputs(temp_dir, raw_submission)
    output = temp_dir / "results" / "pairs.json"
    assert main(["compare", str(corpus), "--assignments", str(assignments), "--output", str(output)]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(output.read_text(encoding="utf-8")) == json.loads(captured.out)


@pytest.mark.parametrize("extra", [
    ["--query", "missing"], ["--top-k", "0"], ["--top-k", "-1"],
    ["--threshold", "nan"], ["--threshold", "inf"], ["--threshold", "-0.1"],
    ["--threshold", "1.5"], ["--config-version", ""], ["--config-version", " "],
])
def test_compare_bad_configuration(temp_dir, raw_submission, capsys, extra):
    corpus, assignments = inputs(temp_dir, raw_submission)
    assert main(["compare", str(corpus), "--assignments", str(assignments), *extra]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["code"] == "configuration_error"


def test_compare_threshold_and_config_version_are_recorded(temp_dir, raw_submission, capsys):
    corpus, assignments = inputs(temp_dir, raw_submission)
    args = ["compare", str(corpus), "--assignments", str(assignments), "--query", "q"]
    assert main(args) == 0
    original = json.loads(capsys.readouterr().out)
    assert main([*args, "--threshold", "1.000001", "--config-version", "2"]) == 0
    changed = json.loads(capsys.readouterr().out)
    assert changed["config"]["config_version"] == "2"
    assert changed["threshold"] == 1.000001
    for before, after in zip(original["pairs"], changed["pairs"]):
        assert before["score"] == after["score"] == 1
        assert before["config_hash"] != after["config_hash"]
        assert after["threshold"] == 1.000001
        assert after["review_required"] is False


def test_compare_keeps_partial_input_errors_and_continues(temp_dir, raw_submission, capsys):
    corpus, assignments = inputs(temp_dir, raw_submission)
    first = json.loads(corpus.read_text(encoding="utf-8").splitlines()[0])
    with corpus.open("a", encoding="utf-8") as stream:
        stream.write("{broken\n\n")
        for row in [
            dict(first, submission_id="bad", student_id=""), first,
            dict(first, code="print(99)"),
            dict(first, submission_id="after", student_id="last"),
        ]:
            stream.write(json.dumps(row) + "\n")
    assert main(["compare", str(corpus), "--assignments", str(assignments), "--query", "q"]) == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    result = json.loads(captured.out)
    assert result["documents"] == 6
    assert result["comparisons"] == 3
    assert [row["right_id"] for row in result["pairs"]] == ["after", "b", "z"]
    assert [error["line_no"] for error in result["errors"]] == [6, 7, 8, 10]
    assert result["errors"][-1]["code"] == "conflict"


@pytest.mark.parametrize("content, exit_code, error_count", [("", 0, 0), ("{broken\n\n", 1, 2)])
def test_compare_empty_or_invalid_jsonl(temp_dir, raw_submission, capsys, content, exit_code, error_count):
    corpus, assignments = inputs(temp_dir, raw_submission)
    corpus.write_text(content, encoding="utf-8")
    assert main(["compare", str(corpus), "--assignments", str(assignments)]) == exit_code
    result = json.loads(capsys.readouterr().out)
    assert result["documents"] == result["comparisons"] == 0
    assert result["pairs"] == result["undefined_pairs"] == []
    assert len(result["errors"]) == error_count


@pytest.mark.parametrize("code, status", [("", "insufficient_data"), ("def broken(:", "invalid_source")])
@pytest.mark.parametrize("query", [False, True])
def test_compare_undefined_results_are_reported_without_ranking(
    temp_dir, raw_submission, capsys, code, status, query,
):
    corpus, assignments = inputs(temp_dir, raw_submission)
    write_rows(corpus, [
        dict(raw_submission, submission_id="q", student_id="a", code=code),
        dict(raw_submission, submission_id="z", student_id="b", code=code),
    ])
    args = ["compare", str(corpus), "--assignments", str(assignments)]
    assert main(args + (["--query", "q"] if query else [])) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["comparisons"] == len(result["undefined_pairs"]) == 1
    report = result["undefined_pairs"][0]
    assert report["status"] == status
    assert report["score"] is None and report["review_required"] is False
    assert report["reason"]
    assert result["pairs"] == ([] if query else [report])


def test_compare_keep_template_changes_score_and_exclusions(temp_dir, raw_submission, capsys):
    corpus, assignments = inputs(temp_dir, raw_submission)
    template = "from typing import Iterable"
    write_rows(corpus, [
        dict(raw_submission, submission_id="q", student_id="a", code=template + "\nalpha = 1\n"),
        dict(raw_submission, submission_id="z", student_id="b", code=template + "\nbeta = 2\n"),
    ])
    assignments.write_text(json.dumps([
        {"assignment_id": raw_submission["assignment_id"], "allowed_template": template, "requirements": []},
    ]), encoding="utf-8")
    args = ["compare", str(corpus), "--assignments", str(assignments), "--threshold", "0.2"]
    assert main(args) == 0
    excluded = json.loads(capsys.readouterr().out)
    assert main([*args, "--keep-template"]) == 0
    included = json.loads(capsys.readouterr().out)
    before, after = excluded["pairs"][0], included["pairs"][0]
    assert before["score"] == 0 < after["score"]
    assert before["review_required"] is False and after["review_required"] is True
    assert before["excluded_left"][0]["start_line"] == 1
    assert before["excluded_right"][0]["end_line"] == 1
    assert after["excluded_left"] == after["excluded_right"] == []
    assert before["config_hash"] != after["config_hash"]
    assert included["config"]["exclude_template"] is False


def test_compare_unknown_assignment_fails_even_when_filtered(temp_dir, raw_submission, capsys):
    corpus, assignments = inputs(temp_dir, raw_submission)
    assignments.write_text(json.dumps([
        {"assignment_id": raw_submission["assignment_id"], "allowed_template": "", "requirements": []},
    ]), encoding="utf-8")
    assert main(["compare", str(corpus), "--assignments", str(assignments), "--query", "q"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["code"] == "configuration_error"


@pytest.mark.parametrize("problem", ["missing_corpus", "bad_encoding", "missing_assignments", "output_directory"])
def test_compare_io_errors_use_stderr(temp_dir, raw_submission, capsys, problem):
    corpus, assignments = inputs(temp_dir, raw_submission)
    extra = []
    if problem == "missing_corpus":
        corpus = temp_dir / "missing.jsonl"
    elif problem == "bad_encoding":
        corpus.write_bytes(b"\xff\n")
    elif problem == "missing_assignments":
        assignments = temp_dir / "missing.json"
    else:
        extra = ["--output", str(temp_dir)]
    assert main(["compare", str(corpus), "--assignments", str(assignments), *extra]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["code"] == "source_unavailable"


@pytest.mark.parametrize("content", ["{broken", "{}", '[{"assignment_id":"missing-fields"}]'])
def test_compare_invalid_assignments_are_configuration_error(temp_dir, raw_submission, capsys, content):
    corpus, assignments = inputs(temp_dir, raw_submission)
    assignments.write_text(content, encoding="utf-8")
    assert main(["compare", str(corpus), "--assignments", str(assignments)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["code"] == "configuration_error"


@pytest.mark.parametrize("args", [
    ["compare", "unused", "--assignments", "unused", "--method", "ast"],
    ["evaluate"], ["benchmark"],
])
def test_future_methods_and_commands_are_not_available(args, capsys):
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "invalid choice" in captured.err


def test_compare_subprocess(temp_dir, raw_submission):
    corpus, assignments = inputs(temp_dir, raw_submission)
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "school_lab.cli", "compare", str(corpus),
         "--assignments", str(assignments), "--query", "q"],
        capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == b""
    assert [row["right_id"] for row in json.loads(result.stdout)["pairs"]] == ["b", "z"]
