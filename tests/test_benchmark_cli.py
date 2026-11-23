"""CLI нагрузочных замеров на малых JSONL без оценки качества синтетики."""

import json
import subprocess
import sys

import pytest

from school_lab.cli import main


@pytest.fixture
def benchmark_data(temp_dir, raw_submission):
    """Вход: временная папка и JSON; выход: каталог с тремя сдачами и заданием."""
    directory = temp_dir / "data" / "lab2"
    directory.mkdir(parents=True)
    source = "def solve(value):\n    answer = value + 1\n    return answer\n"
    documents = [
        dict(raw_submission, submission_id=f"work-{number}",
             student_id=f"author-{number}", code=source)
        for number in range(3)
    ]
    (directory / "submissions.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in documents), encoding="utf-8",
    )
    (directory / "assignments.json").write_text(json.dumps([{
        "assignment_id": raw_submission["assignment_id"],
        "allowed_template": "", "requirements": [],
    }]), encoding="utf-8")
    return directory


@pytest.mark.parametrize("use_defaults", [False, True])
def test_benchmark_stdout_matches_artifacts(benchmark_data, temp_dir, capsys, monkeypatch, use_defaults):
    """Стандартные и явные пути работают; в тестах нагрузка ограничена 3 строками."""
    monkeypatch.chdir(temp_dir)
    output = temp_dir / "results" / "lab2" if use_defaults else temp_dir / "explicit-results"
    args = ["benchmark", "--sizes", "3"]
    if not use_defaults:
        args.extend(["--data", str(benchmark_data), "--output", str(output), "--seed", "917"])
    assert main(args) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    result = json.loads(captured.out)
    assert result == json.loads((output / "benchmark.json").read_text(encoding="utf-8"))
    assert result["seed"] == (42 if use_defaults else 917)
    assert result["repeats"] == 3
    assert {(row["records"], row["method"]) for row in result["scaling"]} == {
        (3, "token"), (3, "ast"),
    }
    assert result["baseline"]["eligible_pairs"] == 3
    assert set(result["artifacts"]) == {
        "benchmark.json", "baseline.json", "optimization.json",
        "profile_before.txt", "profile_after.txt",
    }
    assert result["optimization"]["eligible_pairs"] == 3
    assert all(row["full_results_equal"] for row in result["optimization"]["methods"])


@pytest.mark.parametrize("extra", [
    ["--sizes", "0"], ["--sizes", "-1"], ["--sizes", "3", "3"], ["--repeats", "2"],
])
def test_benchmark_invalid_settings_fail_before_output(benchmark_data, temp_dir, capsys, extra):
    output = temp_dir / "results"
    args = ["benchmark", "--data", str(benchmark_data), "--output", str(output), "--sizes", "3"]
    assert main([*args, *extra]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["code"] == "configuration_error"
    assert not output.exists()


@pytest.mark.parametrize("failure", ["empty", "malformed", "no_pairs", "unknown_assignment"])
def test_benchmark_rejects_unusable_corpus(benchmark_data, temp_dir, capsys, failure):
    corpus = benchmark_data / "submissions.jsonl"
    rows = [json.loads(line) for line in corpus.read_text(encoding="utf-8").splitlines()]
    if failure == "empty":
        corpus.write_text("", encoding="utf-8")
    elif failure == "malformed":
        with corpus.open("a", encoding="utf-8") as stream:
            stream.write("{broken\n")
    else:
        for row in rows:
            row["student_id" if failure == "no_pairs" else "assignment_id"] = "same"
        corpus.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    output = temp_dir / "results"
    assert main(["benchmark", "--data", str(benchmark_data), "--output", str(output), "--sizes", "3"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["code"] == "configuration_error"
    assert not output.exists()


@pytest.mark.parametrize("failure", ["missing_data", "missing_assignments", "utf8", "output_file"])
def test_benchmark_io_errors_use_stderr(benchmark_data, temp_dir, capsys, failure):
    data = benchmark_data
    output = temp_dir / "results"
    if failure == "missing_data":
        data = temp_dir / "absent"
    elif failure == "missing_assignments":
        (data / "assignments.json").unlink()
    elif failure == "utf8":
        (data / "submissions.jsonl").write_bytes(b"\xff\n")
    else:
        output.write_text("existing file", encoding="utf-8")
    assert main(["benchmark", "--data", str(data), "--output", str(output), "--sizes", "3"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["code"] == "source_unavailable"
    if failure == "output_file":
        assert output.read_text(encoding="utf-8") == "existing file"
    else:
        assert not output.exists()


def test_benchmark_subprocess(benchmark_data, temp_dir):
    """Реальный модульный запуск сохраняет CLI-аргументы и результаты на диск."""
    output = temp_dir / "subprocess-results"
    completed = subprocess.run(
        [sys.executable, "-B", "-X", "utf8", "-m", "school_lab.cli", "benchmark",
         "--data", str(benchmark_data), "--output", str(output), "--sizes", "3", "6",
         "--repeats", "3", "--seed", "73"],
        cwd=temp_dir, capture_output=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == b""
    result = json.loads(completed.stdout)
    assert result["seed"] == 73 and result["repeats"] == 3
    assert {row["records"] for row in result["scaling"]} == {3, 6}
    assert result["baseline"]["records"] == result["baseline"]["eligible_pairs"] == 3
    assert result["optimization"] == json.loads((output / "optimization.json").read_text(encoding="utf-8"))
    assert all(row["full_results_equal"] for row in result["optimization"]["methods"])
    assert result == json.loads((output / "benchmark.json").read_text(encoding="utf-8"))
