"""CLI оценки качества: реальные временные данные, артефакты и ошибки входа."""

import json
import subprocess
import sys

import pytest

from school_lab import experiments
from school_lab.cli import main


def write_json(path, value):
    """Записать небольшой JSON во временный каталог теста."""
    path.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")


@pytest.fixture
def evaluation_data(temp_dir, raw_submission):
    """Создать две положительные пары из разных семейств validation/test."""
    directory = temp_dir / "data" / "lab2"
    directory.mkdir(parents=True)
    source = "def solve(value):\n    answer = value + 1\n    return answer\n"
    documents, provenance, pairs, split = [], {}, [], {}
    for part in ("validation", "test"):
        root_id, child_id = f"{part}-root", f"{part}-child"
        split[part] = [root_id, child_id]
        for identifier, parent in ((root_id, None), (child_id, root_id)):
            documents.append({
                **raw_submission, "submission_id": identifier,
                "student_id": identifier, "code": source,
            })
            provenance[identifier] = {
                "family_id": f"{part}-family", "parent_id": parent,
                "transformation": "original" if parent is None else "exact-copy",
                "origin": "Отдельное искусственное семейство; потомок копирует корень.",
            }
        pairs.append({
            "left_id": root_id, "right_id": child_id, "split": part,
            "derivation_relation": True, "template_only": False,
            "explanation": "Известная точная копия внутри семейства.",
        })
    for name, rows in (("submissions.jsonl", documents), ("pairs.jsonl", pairs)):
        (directory / name).write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )
    write_json(directory / "assignments.json", [{
        "assignment_id": raw_submission["assignment_id"],
        "allowed_template": "", "requirements": [],
    }])
    write_json(directory / "provenance.json", provenance)
    write_json(directory / "split.json", split)
    return directory


@pytest.mark.parametrize("use_defaults", [False, True])
def test_evaluate_writes_artifacts_matching_stdout(
    evaluation_data, temp_dir, capsys, monkeypatch, use_defaults,
):
    """И явные пути, и стандартные пути работают внутри временного cwd."""
    monkeypatch.chdir(temp_dir)
    output = temp_dir / "results" / "lab2" if use_defaults else temp_dir / "explicit-results"
    args = ["evaluate"] if use_defaults else [
        "evaluate", "--data", str(evaluation_data), "--output", str(output), "--seed", "917",
    ]
    assert main(args) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    result = json.loads(captured.out)
    assert result == json.loads((output / "run.json").read_text(encoding="utf-8"))
    assert result["seed"] == (42 if use_defaults else 917)
    assert result["documents"] == 4 and result["labelled_pairs"] == 2
    assert result["families"] == 2
    assert set(result["configuration"]) == {"token", "ast"}
    assert {path.name for path in output.iterdir()} == {
        "run.json", "metrics.json", "pair_results.jsonl", "error_analysis.json",
    }
    method_metrics = json.loads((output / "metrics.json").read_text(encoding="utf-8"))
    assert result["metrics"] == method_metrics
    for method in ("token", "ast"):
        assert method_metrics[method]["threshold_selected_on"] == "validation"
        assert method_metrics[method]["threshold"] == 1
        assert method_metrics[method]["test"]["tp"] == 1
        assert method_metrics[method]["test"]["abstentions"] == 0
    pairs = [json.loads(line) for line in (output / "pair_results.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(pairs) == 4
    assert {(row["method"], row["split"]) for row in pairs} == {
        ("token", "validation"), ("token", "test"), ("ast", "validation"), ("ast", "test"),
    }
    assert all(row["result"]["score"] == 1 and row["kind"] == "TP" for row in pairs)
    errors = json.loads((output / "error_analysis.json").read_text(encoding="utf-8"))
    assert errors == {"test_errors": [], "diagnostics": []}


def test_evaluate_method_abstention_is_reported_as_data(evaluation_data, temp_dir, capsys):
    """Неподдерживаемый AST остаётся в метриках и не превращается в ошибку CLI."""
    corpus = evaluation_data / "submissions.jsonl"
    rows = [json.loads(line) for line in corpus.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        if row["submission_id"].startswith("test-"):
            row["code"] = "def solve(values):\n    return [value for value in values]\n"
    corpus.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    output = temp_dir / "results"
    assert main(["evaluate", "--data", str(evaluation_data), "--output", str(output)]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    result = json.loads(captured.out)
    assert result["metrics"]["token"]["test"]["tp"] == 1
    assert result["metrics"]["ast"]["test"]["abstentions"] == 1
    assert result["metrics"]["ast"]["test"]["positive_abstentions"] == 1
    errors = json.loads((output / "error_analysis.json").read_text(encoding="utf-8"))["test_errors"]
    assert len(errors) == 1
    assert errors[0]["method"] == "ast" and errors[0]["kind"] == "ABSTENTION"
    assert errors[0]["result"]["status"] == "unsupported"
    assert errors[0]["result"]["score"] is None


@pytest.mark.parametrize("failure", ["null-record", "parent-cycle"])
def test_evaluate_rejects_provenance_before_comparison(
    evaluation_data, temp_dir, capsys, monkeypatch, failure,
):
    """Реальный повреждённый manifest отклоняется до первого извлечения признаков."""
    path = evaluation_data / "provenance.json"
    provenance = json.loads(path.read_text(encoding="utf-8"))
    if failure == "null-record":
        provenance["validation-root"] = None
    else:
        provenance["validation-root"]["parent_id"] = "validation-child"
    write_json(path, provenance)

    def unexpected_comparison(*args, **kwargs):
        pytest.fail("Сравнение не должно начинаться при неверном происхождении")

    monkeypatch.setattr(experiments, "compare_submissions", unexpected_comparison)
    output = temp_dir / "results"
    assert main(["evaluate", "--data", str(evaluation_data), "--output", str(output)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    error = json.loads(captured.err)
    assert error["code"] == "configuration_error"
    assert ("provenance" if failure == "null-record" else "Цикл") in error["message"]
    assert not output.exists()


@pytest.mark.parametrize("settings", [[], {"methods": ["token"]}, {"n": 0}, {"exclude_template": "yes"}])
def test_evaluate_rejects_invalid_configuration(evaluation_data, temp_dir, capsys, settings):
    write_json(evaluation_data / "config.json", settings)
    output = temp_dir / "results"
    assert main(["evaluate", "--data", str(evaluation_data), "--output", str(output)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["code"] == "configuration_error"
    assert not output.exists()


@pytest.mark.parametrize("filename", ["submissions.jsonl", "provenance.json", "config.json"])
def test_evaluate_malformed_json_is_configuration_error(evaluation_data, temp_dir, capsys, filename):
    (evaluation_data / filename).write_text("{broken\n", encoding="utf-8")
    output = temp_dir / "results"
    assert main(["evaluate", "--data", str(evaluation_data), "--output", str(output)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["code"] == "configuration_error"
    assert not output.exists()


@pytest.mark.parametrize("failure", ["missing_data", "missing_pairs", "utf8_corpus", "utf8_provenance", "output_file"])
def test_evaluate_io_errors_use_stderr(evaluation_data, temp_dir, capsys, failure):
    data = evaluation_data
    output = temp_dir / "results"
    if failure == "missing_data":
        data = temp_dir / "absent"
    elif failure == "missing_pairs":
        (data / "pairs.jsonl").unlink()
    elif failure == "utf8_corpus":
        (data / "submissions.jsonl").write_bytes(b"\xff\n")
    elif failure == "utf8_provenance":
        (data / "provenance.json").write_bytes(b"\xff\n")
    else:
        output.write_text("existing file", encoding="utf-8")
    assert main(["evaluate", "--data", str(data), "--output", str(output)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["code"] == "source_unavailable"
    if failure == "output_file":
        assert output.read_text(encoding="utf-8") == "existing file"
    else:
        assert not output.exists()


def test_evaluate_subprocess(evaluation_data, temp_dir):
    output = temp_dir / "subprocess-results"
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "school_lab.cli", "evaluate",
         "--data", str(evaluation_data), "--output", str(output), "--seed", "73"],
        cwd=temp_dir, capture_output=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == b""
    result = json.loads(completed.stdout)
    assert result["seed"] == 73
    assert result["documents"] == 4 and result["labelled_pairs"] == 2
    assert result == json.loads((output / "run.json").read_text(encoding="utf-8"))
