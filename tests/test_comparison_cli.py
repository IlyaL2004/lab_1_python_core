"""Контракт token/AST-сравнения через CLI на малых независимых входах."""

import ast
from collections import Counter
from itertools import combinations
import json
import subprocess
import sys

import pytest

from school_lab.cli import main
from school_lab import comparison_cli
from school_lab.parsing import load_assignments
from school_lab.pipeline import file_sha256, iter_submissions
from school_lab.similarity import compare_submissions, eligible
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


@pytest.mark.parametrize("method", ["token", "ast"])
def test_compare_query_filters_and_stable_ties(temp_dir, raw_submission, capsys, method):
    corpus, assignments = inputs(temp_dir, raw_submission)
    args = ["compare", str(corpus), "--assignments", str(assignments),
            "--query", "q", "--top-k", "1", "--method", method]
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["documents"] == 5
    assert result["comparisons"] == 2
    assert len(result["pairs"]) == 1
    assert result["pairs"][0]["right_id"] == "b"
    assert result["pairs"][0]["score"] == 1
    assert result["config"]["method"] == result["pairs"][0]["method"] == method
    assert result["pairs"][0]["review_required"] is True
    assert result["undefined_pairs"] == result["errors"] == []


@pytest.mark.parametrize("method", ["token", "ast"])
def test_compare_all_pairs_follow_public_contract(temp_dir, raw_submission, capsys, method):
    corpus, assignments_path = inputs(temp_dir, raw_submission)
    assert main(["compare", str(corpus), "--assignments", str(assignments_path), "--method", method]) == 0
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


@pytest.mark.parametrize("method", ["token", "ast"])
@pytest.mark.parametrize("query", [None, "q"])
def test_compare_prepares_once_and_keeps_baseline_results(
    temp_dir, raw_submission, capsys, monkeypatch, method, query,
):
    """Индекс сохраняет весь отчёт, top-k и отказы; следующий запуск читает изменения."""
    corpus, assignments_path = inputs(temp_dir, raw_submission)
    rows = [json.loads(line) for line in corpus.read_text(encoding="utf-8").splitlines()]
    template = "from typing import Iterable"
    for row in rows:
        row["code"] = template + "\n" + row["code"]
    rows.extend(dict(raw_submission, submission_id=identifier, student_id=identifier, code=source)
                for identifier, source in [("invalid", "def broken(:"), ("empty", ""),
                                           ("template", template),
                                           ("unsupported", "def solve(xs):\n    return [x for x in xs]\n")])
    assignments = json.loads(assignments_path.read_text(encoding="utf-8"))
    for assignment in assignments:
        assignment["allowed_template"] = template
    assignments_path.write_text(json.dumps(assignments), encoding="utf-8")
    assignments = load_assignments(assignments_path)
    calls = Counter()
    original = comparison_cli.prepare_submission

    def counted(submission, assignment, config):
        calls[submission.submission_id] += 1
        return original(submission, assignment, config)

    monkeypatch.setattr(comparison_cli, "prepare_submission", counted)
    args = ["compare", str(corpus), "--assignments", str(assignments_path), "--method", method,
            "--top-k", "3"] + (["--query", query] if query is not None else [])
    previous = None
    for number in (1, 2):
        write_rows(corpus, rows)
        assert main(args) == 0
        result = json.loads(capsys.readouterr().out)
        assert calls == Counter({row["submission_id"]: number for row in rows})
        documents = list(iter_submissions(corpus))
        if query is None:
            pairs = [(left, right) for left, right in combinations(documents, 2) if eligible(left, right)]
        else:
            left = next(item for item in documents if item.submission_id == query)
            pairs = [(left, right) for right in documents if eligible(left, right)]
        expected = [compare_submissions(left, right, assignments[left.assignment_id],
                                        SimilarityConfig(method=method)).to_dict()
                    for left, right in pairs]
        undefined = [row for row in expected if row["score"] is None]
        selected = expected if query is None else sorted(
            (row for row in expected if row["score"] is not None),
            key=lambda row: (-row["score"], row["right_id"]),
        )[:3]
        assert result["pairs"] == selected
        assert result["undefined_pairs"] == undefined
        assert result["comparisons"] == len(expected)
        if previous is not None:
            assert result["corpus_sha256"] != previous["corpus_sha256"]
            assert result["pairs"] != previous["pairs"]
        previous = result
        rows[0]["code"] = template + "\ndef changed(value):\n    return value - 99\n"


@pytest.mark.parametrize("method", ["token", "ast"])
def test_compare_file_output_equals_stdout(temp_dir, raw_submission, capsys, method):
    corpus, assignments = inputs(temp_dir, raw_submission)
    output = temp_dir / "results" / "pairs.json"
    assert main(["compare", str(corpus), "--assignments", str(assignments),
                 "--method", method, "--output", str(output)]) == 0
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


@pytest.mark.parametrize("method", ["token", "ast"])
def test_compare_threshold_and_config_version_are_recorded(temp_dir, raw_submission, capsys, method):
    corpus, assignments = inputs(temp_dir, raw_submission)
    args = ["compare", str(corpus), "--assignments", str(assignments),
            "--query", "q", "--method", method]
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


@pytest.mark.parametrize("method", ["token", "ast"])
def test_compare_keeps_partial_input_errors_and_continues(temp_dir, raw_submission, capsys, method):
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
    assert main(["compare", str(corpus), "--assignments", str(assignments),
                 "--query", "q", "--method", method]) == 1
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
@pytest.mark.parametrize("method", ["token", "ast"])
def test_compare_undefined_results_are_reported_without_ranking(
    temp_dir, raw_submission, capsys, code, status, query, method,
):
    corpus, assignments = inputs(temp_dir, raw_submission)
    write_rows(corpus, [
        dict(raw_submission, submission_id="q", student_id="a", code=code),
        dict(raw_submission, submission_id="z", student_id="b", code=code),
    ])
    args = ["compare", str(corpus), "--assignments", str(assignments), "--method", method]
    assert main(args + (["--query", "q"] if query else [])) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["comparisons"] == len(result["undefined_pairs"]) == 1
    report = result["undefined_pairs"][0]
    assert report["status"] == status
    assert report["method"] == method
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


def test_compare_ast_renaming_preserves_original_evidence_and_token_default(
    temp_dir, raw_submission, capsys,
):
    corpus, assignments = inputs(temp_dir, raw_submission)
    template = "from typing import Iterable"
    left_code = template + "\n\ndef solve(value):\n    answer = value + 1\n    return answer\n"
    right_code = (
        "# переименование\n\n" + template
        + "\n\ndef renamed(number):\n    total = number + 1\n    return total\n"
    )
    write_rows(corpus, [
        dict(raw_submission, submission_id="q", student_id="a", code=left_code),
        dict(raw_submission, submission_id="renamed", student_id="b", code=right_code),
    ])
    assignments.write_text(json.dumps([
        {"assignment_id": raw_submission["assignment_id"], "allowed_template": template, "requirements": []},
    ]), encoding="utf-8")
    args = ["compare", str(corpus), "--assignments", str(assignments), "--query", "q", "--top-k", "1"]
    assert main(args) == 0
    token_result = json.loads(capsys.readouterr().out)
    assert token_result["config"]["method"] == token_result["pairs"][0]["method"] == "token"
    assert token_result["pairs"][0]["score"] < 1
    assert main([*args, "--method", "ast"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["comparisons"] == 1 and result["undefined_pairs"] == []
    report = result["pairs"][0]
    assert report["right_id"] == "renamed"
    assert report["method"] == "ast" and report["score"] == 1
    assert report["evidence"]
    assert report["excluded_left"][0]["start_line"] == 1
    assert report["excluded_right"][0]["start_line"] == 3
    for side, identifier, source in [("left", "q", left_code), ("right", "renamed", right_code)]:
        original_nodes = {
            (type(node).__name__, node.lineno, node.end_lineno)
            for node in ast.walk(ast.parse(source)) if hasattr(node, "lineno")
        }
        for item in report["evidence"]:
            reference = item[side]
            node_type = item["feature"].split("(", 1)[0]
            assert reference["submission_id"] == identifier
            assert (node_type, reference["start_line"], reference["end_line"]) in original_nodes
    assignment_evidence = next(item for item in report["evidence"] if item["feature"].startswith("Assign("))
    assert assignment_evidence["left"]["start_line"] == assignment_evidence["left"]["end_line"] == 4
    assert assignment_evidence["right"]["start_line"] == assignment_evidence["right"]["end_line"] == 6


def test_compare_ast_query_reports_unsupported_and_invalid_sources_without_fallback(
    temp_dir, raw_submission, capsys,
):
    corpus, assignments = inputs(temp_dir, raw_submission)
    source = "def solve(values):\n    return values\n"
    write_rows(corpus, [
        dict(raw_submission, submission_id="q", student_id="a", code=source),
        dict(raw_submission, submission_id="copy", student_id="b", code=source),
        dict(raw_submission, submission_id="unsupported", student_id="c",
             code="def solve(values):\n    return [value for value in values]\n"),
        dict(raw_submission, submission_id="invalid", student_id="d", code="def broken(:"),
    ])
    args = ["compare", str(corpus), "--assignments", str(assignments), "--query", "q"]
    assert main([*args, "--method", "ast"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["comparisons"] == 3
    assert [row["right_id"] for row in result["pairs"]] == ["copy"]
    assert {row["right_id"]: row["status"] for row in result["undefined_pairs"]} == {
        "unsupported": "unsupported", "invalid": "invalid_source",
    }
    for row in result["undefined_pairs"]:
        assert row["method"] == "ast" and row["score"] is None
        assert row["review_required"] is False and row["reason"]
    assert main(args) == 0
    token_result = json.loads(capsys.readouterr().out)
    assert {row["right_id"] for row in token_result["pairs"]} == {"copy", "unsupported"}
    assert [row["right_id"] for row in token_result["undefined_pairs"]] == ["invalid"]


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
    ["compare", "unused", "--assignments", "unused", "--method", "unknown"],
])
def test_future_methods_and_commands_are_not_available(args, capsys):
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "invalid choice" in captured.err


@pytest.mark.parametrize("method", ["token", "ast"])
def test_compare_subprocess(temp_dir, raw_submission, method):
    corpus, assignments = inputs(temp_dir, raw_submission)
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "school_lab.cli", "compare", str(corpus),
         "--assignments", str(assignments), "--query", "q", "--method", method],
        capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == b""
    result_json = json.loads(result.stdout)
    assert result_json["config"]["method"] == method
    assert [row["right_id"] for row in result_json["pairs"]] == ["b", "z"]
    assert all(row["method"] == method for row in result_json["pairs"])
