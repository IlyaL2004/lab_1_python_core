"""Проверки воспроизводимой нагрузки и последовательных замеров оптимизации."""

import hashlib
import json
import tracemalloc
from dataclasses import asdict, replace
from pathlib import Path
from statistics import median

import pytest

from school_lab import benchmarking, similarity
from school_lab.benchmarking import (
    build_feature_index,
    pair_results,
    run_benchmarks,
    write_workload,
)
from school_lab.models import Assignment
from school_lab.parsing import parse_submission
from school_lab.pipeline import file_sha256, iter_submissions
from school_lab.similarity import compare_submissions
from school_lab.similarity_models import SimilarityConfig


@pytest.fixture
def benchmark_inputs(temp_dir: Path, raw_submission: dict):
    """Вход: временная папка и JSON; выход: пути, три сдачи и их задание."""
    raw_submission["code"] = "def solve(value):\n    result = value + 1\n    return result\n"
    first = parse_submission(raw_submission)
    submissions = [
        first,
        replace(first, submission_id="second", student_id="student-2"),
        replace(first, submission_id="third", student_id="student-3",
                code="def solve(number):\n    answer = number + 1\n    return answer\n"),
    ]
    assignment = Assignment(first.assignment_id, "", ())
    corpus = temp_dir / "corpus.jsonl"
    corpus.write_text(
        "".join(json.dumps(asdict(item)) + "\n" for item in submissions),
        encoding="utf-8",
    )
    assignments = temp_dir / "assignments.json"
    assignments.write_text(json.dumps([asdict(assignment)]), encoding="utf-8")
    return corpus, assignments, submissions, assignment


@pytest.mark.parametrize("sizes", [(), (0,), (-1,), (True,), (1.5,), (2, 2)])
def test_invalid_sizes_fail_before_reading(temp_dir, sizes):
    """Некорректные размеры отклоняются до чтения входа и создания результатов."""
    output = temp_dir / "results"
    with pytest.raises(ValueError):
        run_benchmarks(temp_dir / "missing", temp_dir / "missing", output, sizes=sizes)
    assert not output.exists()


@pytest.mark.parametrize("repeats", [0, 1, 2, True, 3.0])
def test_at_least_three_integer_repeats(temp_dir, repeats):
    """Нужно минимум три отдельных повтора времени и памяти."""
    with pytest.raises(ValueError):
        run_benchmarks(temp_dir / "missing", temp_dir / "missing", temp_dir, repeats=repeats)


@pytest.mark.parametrize("seed", [True, 1.5, "42"])
def test_seed_is_an_integer(temp_dir, seed):
    with pytest.raises(ValueError):
        run_benchmarks(temp_dir / "missing", temp_dir / "missing", temp_dir, seed=seed)


def test_workload_is_reproducible_and_keeps_sources(benchmark_inputs, temp_dir):
    """Синтетические записи получают новые ID, а исходные образцы не меняются."""
    _, _, submissions, assignment = benchmark_inputs
    original = [asdict(item) for item in submissions]
    left, right = temp_dir / "left.jsonl", temp_dir / "right.jsonl"
    write_workload(left, submissions, 7, 42)
    write_workload(right, submissions, 7, 42)
    assert left.read_bytes() == right.read_bytes()
    loaded = list(iter_submissions(left))
    assert len(loaded) == 7
    assert len({item.submission_id for item in loaded}) == 7
    assert len({item.student_id for item in loaded}) == 7
    assert {item.assignment_id for item in loaded} == {assignment.assignment_id}
    assert {item.code for item in loaded} <= {item.code for item in submissions}
    assert [asdict(item) for item in submissions] == original


def test_empty_workload_examples_are_rejected(temp_dir):
    path = temp_dir / "empty.jsonl"
    with pytest.raises(ValueError):
        write_workload(path, [], 3, 42)
    assert not path.exists()


@pytest.mark.parametrize("failure", ["unknown_assignment", "malformed_json"])
def test_index_does_not_silently_drop_invalid_input(benchmark_inputs, failure):
    """Ошибка приёма прерывает замер, чтобы он не учитывал неполную нагрузку."""
    corpus, _, _, assignment = benchmark_inputs
    assignments = {assignment.assignment_id: assignment}
    if failure == "unknown_assignment":
        assignments = {}
    else:
        with corpus.open("a", encoding="utf-8") as stream:
            stream.write("{broken\n")
    with pytest.raises(ValueError):
        build_feature_index(corpus, assignments, SimilarityConfig())


@pytest.mark.parametrize("method", ["token", "ast"])
def test_index_stores_features_and_coordinates_without_source(benchmark_inputs, method):
    corpus, _, submissions, assignment = benchmark_inputs
    index = build_feature_index(
        corpus, {assignment.assignment_id: assignment}, SimilarityConfig(method=method),
    )
    assert set(index) == {item.submission_id for item in submissions}
    for entry in index.values():
        assert entry.features and entry.occurrences
        assert entry.status == "ok"
        assert not hasattr(entry, "code") and not hasattr(entry, "text")
        assert entry.assignment_id == assignment.assignment_id


@pytest.mark.parametrize("method", ["token", "ast"])
def test_pair_baseline_matches_public_reports(benchmark_inputs, monkeypatch, method):
    """Каждая пара проходит обычное сравнение с теми же score и evidence."""
    _, _, submissions, assignment = benchmark_inputs
    config = SimilarityConfig(method=method)
    expected = [
        compare_submissions(submissions[i], submissions[j], assignment, config).to_dict()
        for i, j in ((0, 1), (0, 2), (1, 2))
    ]
    calls = []

    def record_pair(left, right, *args, **kwargs):
        calls.append((left.submission_id, right.submission_id))
        return compare_submissions(left, right, *args, **kwargs)

    monkeypatch.setattr(benchmarking, "compare_submissions", record_pair)
    result = pair_results(submissions, {assignment.assignment_id: assignment}, config)
    assert result == expected
    assert len(calls) == len(result) == 3
    assert result[0]["score"] == 1 and result[0]["evidence"]


@pytest.mark.parametrize("cached", [False, True])
def test_pair_baseline_keeps_versions_and_filters_author_and_assignment(benchmark_inputs, cached):
    """Обе версии сравниваются с другим студентом; между собой — нет."""
    _, _, submissions, assignment = benchmark_inputs
    first, second = submissions[:2]
    records = [
        first, replace(first, submission_id="new-version"), second,
        replace(submissions[2], assignment_id="different"),
    ]
    assignments = {
        assignment.assignment_id: assignment,
        "different": Assignment("different", "", ()),
    }
    result = pair_results(records, assignments, SimilarityConfig(), cached=cached)
    assert [(row["left_id"], row["right_id"]) for row in result] == [
        (first.submission_id, second.submission_id), ("new-version", second.submission_id),
    ]


@pytest.mark.parametrize("method", ["token", "ast"])
def test_preparation_is_once_per_submission_and_repeated_each_run(benchmark_inputs, monkeypatch, method):
    """Измерение включает новую подготовку всех сдач, но не повторяет её для каждой пары."""
    _, _, submissions, assignment = benchmark_inputs
    assignments = {assignment.assignment_id: assignment}
    config = SimilarityConfig(method=method)
    calls = []
    prepare = benchmarking.prepare_submission

    def recorded(item, *args, **kwargs):
        calls.append(item.submission_id)
        return prepare(item, *args, **kwargs)

    monkeypatch.setattr(benchmarking, "prepare_submission", recorded)
    monkeypatch.setattr(similarity, "prepare_submission", recorded)
    expected = pair_results(submissions, assignments, config)
    assert len(calls) == 6
    for _ in range(2):
        calls.clear()
        actual = pair_results(submissions, assignments, config, cached=True)
        assert calls == [item.submission_id for item in submissions]
        assert actual == expected


@pytest.mark.parametrize("method", ["token", "ast"])
@pytest.mark.parametrize("exclude_template", [False, True])
def test_cached_reports_keep_template_errors_and_source_coordinates(
    benchmark_inputs, method, exclude_template,
):
    """Оптимизация сохраняет весь отчёт, включая отказы, шаблон и ссылки на исходник."""
    _, _, submissions, assignment = benchmark_inputs
    template = "print('header')\n"
    assignment = replace(assignment, allowed_template=template)
    base = submissions[0]
    codes = [
        template + base.code,
        template + "def solve(число):\n    ответ = число + 1\n    return ответ\n",
        template,
        "def broken(:\n    return 1\n",
        "# only comment\n",
        base.code,
        submissions[2].code,
    ]
    records = [
        replace(base, submission_id=f"case-{number}", student_id=f"author-{number}", code=code)
        for number, code in enumerate(codes)
    ]
    original = [asdict(item) for item in records]
    config = SimilarityConfig(method=method, exclude_template=exclude_template)
    assignments = {assignment.assignment_id: assignment}
    expected = pair_results(records, assignments, config)
    actual = pair_results(records, assignments, config, cached=True)
    assert actual == expected
    assert any(row["score"] is None for row in actual)
    assert any(row["evidence"] for row in actual)
    if exclude_template:
        assert any(row["excluded_left"] for row in actual)
    assert [asdict(item) for item in records] == original


def test_time_and_memory_runs_are_separate():
    """Время снимается без tracemalloc, память — отдельными тремя вызовами."""
    tracing = []

    def operation():
        tracing.append(tracemalloc.is_tracing())
        return bytearray(1024)

    result = benchmarking._measure(operation, 3)
    assert tracing == [False, False, False, True, True, True]
    assert not tracemalloc.is_tracing()
    assert len(result["wall_seconds"]) == len(result["peak_bytes"]) == 3
    assert all(value >= 0 for value in result["wall_seconds"])
    assert all(value >= 1024 for value in result["peak_bytes"])
    assert result["wall_seconds_median"] == median(result["wall_seconds"])
    assert result["peak_bytes_median"] == median(result["peak_bytes"])


def test_failed_memory_run_stops_tracing():
    """Исключение измеряемой операции не оставляет tracemalloc включённым."""
    def operation():
        if tracemalloc.is_tracing():
            raise RuntimeError("failed memory measurement")
        return []

    with pytest.raises(RuntimeError, match="failed memory"):
        benchmarking._measure(operation, 3)
    assert not tracemalloc.is_tracing()


def test_external_tracing_is_rejected_without_stopping_it():
    tracemalloc.start()
    try:
        with pytest.raises(ValueError, match="tracemalloc"):
            benchmarking._measure(lambda: None, 3)
        assert tracemalloc.is_tracing()
    finally:
        tracemalloc.stop()


def test_small_benchmark_saves_actual_measurements_and_baseline(benchmark_inputs, temp_dir):
    """Малый прогон проверяет артефакты без требований к скорости компьютера."""
    corpus, assignments, submissions, assignment = benchmark_inputs
    output = temp_dir / "results"
    result = run_benchmarks(corpus, assignments, output, sizes=(3, 6), repeats=3, seed=917)
    assert json.loads((output / "benchmark.json").read_text(encoding="utf-8")) == result
    assert result["seed"] == 917 and result["repeats"] == 3
    assert result["corpus_sha256"] == file_sha256(corpus)
    assert result["assignments_sha256"] == file_sha256(assignments)
    assert result["environment"]["python"] and result["environment"]["platform"]
    assert result["measurement"]["pairwise_at_large_sizes"] is False
    assert result["measurement"]["separate_time_and_memory_runs"] is True
    assert result["measurement"]["concurrency"] is False
    assert result["workload"]["synthetic"] is True
    assert result["workload"]["generation_measured"] is False
    for workload in result["workload"]["files"]:
        expected = temp_dir / "expected.jsonl"
        write_workload(expected, submissions, workload["records"], 917)
        assert workload["sha256"] == file_sha256(expected)
    assert {(row["records"], row["method"]) for row in result["scaling"]} == {
        (3, "token"), (3, "ast"), (6, "token"), (6, "ast"),
    }
    measurements = []
    for row in result["scaling"]:
        assert row["indexed_records"] == row["records"]
        assert sum(row["status_counts"].values()) == row["records"]
        assert row["status_counts"]["ok"] == row["records"]
        measurements.append(row)
    baseline = result["baseline"]
    assert baseline == json.loads((output / "baseline.json").read_text(encoding="utf-8"))
    assert baseline["records"] == 3 and baseline["eligible_pairs"] == 3
    assert baseline["sample_ids"] == [item.submission_id for item in submissions]
    assert baseline["threshold"] == 0.5
    assert {row["method"] for row in baseline["methods"]} == {"token", "ast"}
    for row in baseline["methods"]:
        reports = pair_results(
            submissions, {assignment.assignment_id: assignment},
            SimilarityConfig(**row["config"]),
        )
        encoded = json.dumps(reports, sort_keys=True, ensure_ascii=True).encode("ascii")
        assert row["pairs"] == 3
        assert row["result_sha256"] == hashlib.sha256(encoded).hexdigest()
        assert "after" not in row and "wall_speedup" not in row
        measurements.append(row["measurements"])
    optimization = result["optimization"]
    assert optimization == json.loads((output / "optimization.json").read_text(encoding="utf-8"))
    for key in ("sample_ids", "records", "eligible_pairs"):
        assert optimization[key] == baseline[key]
    assert optimization["before"] == baseline["strategy"]
    assert {row["method"] for row in optimization["methods"]} == {"token", "ast"}
    for row, original in zip(optimization["methods"], baseline["methods"]):
        assert row["method"] == original["method"]
        assert row["config"] == original["config"]
        assert row["method_version"] == original["method_version"]
        assert row["pairs"] == original["pairs"] == 3
        assert row["full_results_equal"] is True
        assert row["result_sha256"] == original["result_sha256"]
        assert row["before"] == original["measurements"]
        assert row["wall_speedup"] == pytest.approx(
            row["before"]["wall_seconds_median"] / row["after"]["wall_seconds_median"],
        )
        measurements.append(row["after"])
    for row in measurements:
        assert len(row["wall_seconds"]) == len(row["peak_bytes"]) == 3
        assert row["wall_seconds_median"] == median(row["wall_seconds"])
        assert row["peak_bytes_median"] == median(row["peak_bytes"])
        assert all(value >= 0 for value in row["wall_seconds"])
        assert all(value > 0 for value in row["peak_bytes"])
    assert set(result["artifacts"]) == {
        "benchmark.json", "baseline.json", "optimization.json",
        "profile_before.txt", "profile_after.txt",
    }
    assert {path.name for path in output.iterdir()} == set(result["artifacts"])
    profile = (output / "profile_before.txt").read_text(encoding="utf-8")
    assert "METHOD token" in profile and "METHOD ast" in profile
    assert "function calls" in profile and "compare_submissions" in profile
    profile = (output / "profile_after.txt").read_text(encoding="utf-8")
    assert "METHOD token" in profile and "METHOD ast" in profile
    assert "function calls" in profile and "compare_prepared" in profile


def test_benchmark_rejects_changed_evidence_even_when_scores_match(benchmark_inputs, temp_dir, monkeypatch):
    """Проверка оптимизации сравнивает полный результат, а не только числовую оценку."""
    corpus, assignments, _, _ = benchmark_inputs
    original = benchmarking.pair_results

    def changed(*args, cached=False, **kwargs):
        reports = original(*args, cached=cached, **kwargs)
        if cached:
            reports[0]["evidence"] = []
        return reports

    monkeypatch.setattr(benchmarking, "pair_results", changed)
    output = temp_dir / "results"
    with pytest.raises(AssertionError, match="полный результат"):
        run_benchmarks(corpus, assignments, output, sizes=(3,))
    assert not (output / "optimization.json").exists()
    assert not (output / "benchmark.json").exists()
