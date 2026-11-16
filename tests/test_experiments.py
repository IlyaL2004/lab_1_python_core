"""Воспроизводимость оценки и граница между выбором порога и test."""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import shutil

import pytest

from school_lab import experiments
from school_lab.evaluation import choose_threshold
from school_lab.features import configuration_payload
from school_lab.hashing import stable_hash
from school_lab.parsing import load_assignments
from school_lab.similarity_models import SimilarityConfig


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "lab2"
INPUT_NAMES = ("submissions.jsonl", "assignments.json", "provenance.json", "split.json", "pairs.jsonl")


def input_paths(directory=DATA):
    """Вход: каталог корпуса. Выход: пять путей в порядке run_experiment."""
    return [directory / name for name in INPUT_NAMES]


def read_json(path):
    """Вход: путь артефакта. Выход: сохранённое JSON-значение."""
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def copied_data(temp_dir):
    """Вход: временная папка. Выход: отдельная копия фиксированного корпуса."""
    directory = temp_dir / "data"
    directory.mkdir()
    for path in DATA.iterdir():
        shutil.copyfile(path, directory / path.name)
    return directory


def test_experiment_artifacts_metrics_and_hashes_are_reproducible(temp_dir):
    first, second = temp_dir / "first", temp_dir / "second"
    run = experiments.run_experiment(*input_paths(), first)
    artifacts = {path.name: path.read_bytes() for path in first.iterdir()}
    assert set(artifacts) == {"run.json", "metrics.json", "pair_results.jsonl", "error_analysis.json"}
    assert read_json(first / "run.json") == run
    assert read_json(first / "metrics.json") == run["metrics"]
    assert run["documents"] == 56 and run["labelled_pairs"] == 132
    rows = [json.loads(line) for line in (first / "pair_results.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 264
    for method in ("token", "ast"):
        validation = [(row["derivation_relation"], row["result"]["score"]) for row in rows
                      if row["method"] == method and row["split"] == "validation"]
        threshold, _ = choose_threshold(validation)
        assert run["metrics"][method]["threshold"] == threshold
        assert run["metrics"][method]["threshold_selected_on"] == "validation"
        assert all(row["threshold"] == threshold for row in rows if row["method"] == method)
        for group in ("validation", "test", "test_template_only"):
            selected = [row for row in rows if row["method"] == method
                        and row["split"] == ("test" if group == "test_template_only" else group)
                        and (group != "test_template_only" or row["template_only"])]
            defined = [row for row in selected if row["result"]["score"] is not None]
            counts = Counter((row["derivation_relation"], row["result"]["score"] >= threshold)
                             for row in defined)
            actual = run["metrics"][method][group]
            assert tuple(actual[key] for key in ("tp", "fp", "fn", "tn")) == (
                counts[True, True], counts[False, True], counts[True, False], counts[False, False],
            )
            assert actual["total_pairs"] == len(selected)
            assert actual["defined_pairs"] == len(defined)
            assert actual["abstentions"] == len(selected) - len(defined)
            assert actual["positive_abstentions"] == sum(row["derivation_relation"] for row in selected
                                                        if row["result"]["score"] is None)

    errors = read_json(first / "error_analysis.json")
    expected_errors = [row for row in rows if row["split"] == "test"
                       and row["kind"] in {"FP", "FN", "ABSTENTION"}]
    assert [{key: value for key, value in row.items() if key != "analysis"}
            for row in errors["test_errors"]] == expected_errors
    assert run["test_error_count"] == len(expected_errors)
    assert all(row["analysis"] for row in errors["test_errors"])
    diagnostic_kinds = {(row["name"], row["kind"]) for row in errors["diagnostics"]}
    assert {("rename-fn", "FN"), ("template-fp", "FP"), ("template-fp", "TN"),
            ("independent-standard-fp", "FP"), ("empty-abstention", "ABSTENTION")} <= diagnostic_kinds
    assert all(row["scope"] == "diagnostic-not-test" for row in errors["diagnostics"])
    assert run["diagnostic_comparisons"] == len(errors["diagnostics"]) == 10
    assert set(run["split_families"]["validation"]).isdisjoint(run["split_families"]["test"])
    for item in run["inputs"].values():
        assert item["sha256"] == sha256((DATA / item["file"]).read_bytes()).hexdigest()
    assert set(run["source_files_sha256"]) == {path.name for path in (ROOT / "src" / "school_lab").glob("*.py")}
    for name, digest in run["source_files_sha256"].items():
        assert digest == sha256((ROOT / "src" / "school_lab" / name).read_bytes()).hexdigest()
    source_manifest = json.dumps(run["source_files_sha256"], ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    assert run["source_sha256"] == sha256(source_manifest.encode("ascii")).hexdigest()
    configuration = json.dumps(run["configuration"], ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    assert run["configuration_sha256"] == sha256(configuration.encode("ascii")).hexdigest()
    assert experiments.run_experiment(*input_paths(), second) == run
    assert {path.name: path.read_bytes() for path in second.iterdir()} == artifacts


def test_threshold_selection_finishes_before_any_test_comparison(temp_dir, monkeypatch):
    split = read_json(DATA / "split.json")
    membership = {identifier: part for part, identifiers in split.items() for identifier in identifiers}
    pairs = [json.loads(line) for line in (DATA / "pairs.jsonl").read_text(encoding="utf-8").splitlines()]
    labels = {(row["left_id"], row["right_id"]): row["derivation_relation"] for row in pairs}
    original_compare, original_choose = experiments.compare_submissions, experiments.choose_threshold
    selected, observed_validation, seen_parts = {}, {"token": [], "ast": []}, set()
    active_method = None

    def watched_compare(left, right, assignment, config, *, threshold=0.5):
        nonlocal active_method
        is_corpus_pair = left.submission_id in membership and right.submission_id in membership
        if is_corpus_pair:
            active_method = config.method
            part = membership[left.submission_id]
            assert membership[right.submission_id] == part
            seen_parts.add((config.method, part))
            if part == "test":
                assert config.method in selected, "test сравнивается до выбора порога"
                assert threshold == selected[config.method]
        result = original_compare(left, right, assignment, config, threshold=threshold)
        if is_corpus_pair and config.method not in selected:
            assert part == "validation"
            observed_validation[config.method].append((labels[left.submission_id, right.submission_id], result.score))
        return result

    def watched_choose(rows):
        assert active_method in observed_validation and active_method not in selected
        assert rows == observed_validation[active_method]
        threshold, metrics = original_choose(rows)
        selected[active_method] = threshold
        return threshold, metrics

    monkeypatch.setattr(experiments, "compare_submissions", watched_compare)
    monkeypatch.setattr(experiments, "choose_threshold", watched_choose)
    run = experiments.run_experiment(*input_paths(), temp_dir / "out")
    assert seen_parts == {(method, part) for method in ("token", "ast") for part in ("validation", "test")}
    assert selected == {method: run["metrics"][method]["threshold"] for method in ("token", "ast")}


@pytest.mark.parametrize("failure", ["family-leak", "duplicate-id", "split-root", "null-provenance", "parent-cycle"])
def test_invalid_partition_is_rejected_before_comparisons(copied_data, temp_dir, monkeypatch, failure):
    split, provenance = read_json(copied_data / "split.json"), read_json(copied_data / "provenance.json")
    if failure == "family-leak":
        split["test"].append(split["validation"].pop(0))
    elif failure == "duplicate-id":
        split["test"].append(split["validation"][0])
    elif failure == "split-root":
        split = []
    elif failure == "null-provenance":
        provenance["squares-family-0-v0"] = None
    else:
        provenance["squares-family-0-v0"]["parent_id"] = "squares-family-0-v1"
    (copied_data / "split.json").write_text(json.dumps(split), encoding="utf-8")
    (copied_data / "provenance.json").write_text(json.dumps(provenance), encoding="utf-8")

    def forbidden(*args, **kwargs):
        pytest.fail("Некорректное разбиение должно отклоняться до сравнений")

    monkeypatch.setattr(experiments, "compare_submissions", forbidden)
    output = temp_dir / "out"
    with pytest.raises(ValueError):
        experiments.run_experiment(*input_paths(copied_data), output)
    assert not output.exists()


@pytest.mark.parametrize("settings", [
    None, [], {"methods": ["ast", "token"]}, {"n": True},
    {"exclude_template": "false"}, {"max_evidence": -1}, {"config_version": ""},
])
def test_invalid_configuration_prevents_comparison(copied_data, temp_dir, monkeypatch, settings):
    (copied_data / "config.json").write_text(json.dumps(settings), encoding="utf-8")

    def forbidden(*args, **kwargs):
        pytest.fail("Некорректные настройки должны отклоняться до сравнений")

    monkeypatch.setattr(experiments, "compare_submissions", forbidden)
    output = temp_dir / "out"
    with pytest.raises(ValueError):
        experiments.run_experiment(*input_paths(copied_data), output)
    assert not output.exists()


@pytest.mark.parametrize("seed", [None, True, 42.0, "42"])
def test_seed_requires_an_integer_without_creating_output(temp_dir, seed):
    output = temp_dir / "out"
    with pytest.raises(ValueError, match="Seed"):
        experiments.run_experiment(*input_paths(), output, seed=seed)
    assert not output.exists()


def test_minimal_provenance_and_unpaired_assignment_are_supported(temp_dir, raw_submission):
    base = "def solve(value):\n    return value + 1\n"
    renamed = "def answer(item):\n    return item + 1\n"
    task = raw_submission["assignment_id"]
    documents = [{**raw_submission, "submission_id": identifier, "student_id": identifier,
                  "assignment_id": assignment, "code": source}
                 for identifier, assignment, source in (("a", task, base), ("b", task, base),
                                                       ("c", task, base), ("d", task, renamed),
                                                       ("unpaired", "test-only-task", "x = 1\n"))]
    provenance = {identifier: {"family_id": family, "parent_id": parent}
                  for identifier, family, parent in (("a", "first", None), ("b", "first", "a"),
                                                    ("c", "second", None), ("d", "second", "c"),
                                                    ("unpaired", "third", None))}
    pairs = [{"left_id": left, "right_id": right, "split": part,
              "derivation_relation": True, "template_only": False, "explanation": "Known descendant"}
             for left, right, part in (("a", "b", "validation"), ("c", "d", "test"))]
    directory = temp_dir / "data"
    directory.mkdir()
    for name, values in (("submissions.jsonl", documents), ("pairs.jsonl", pairs)):
        (directory / name).write_text("\n".join(json.dumps(row) for row in values) + "\n", encoding="utf-8")
    for name, value in (("provenance.json", provenance),
                        ("split.json", {"validation": ["a", "b"], "test": ["c", "d", "unpaired"]}),
                        ("assignments.json", [{"assignment_id": identifier, "allowed_template": "", "requirements": []}
                                              for identifier in (task, "test-only-task")])):
        (directory / name).write_text(json.dumps(value), encoding="utf-8")
    output = temp_dir / "out"
    run = experiments.run_experiment(*input_paths(directory), output, seed=19)
    assert run["seed"] == 19 and run["documents"] == 5 and run["labelled_pairs"] == 2
    assert run["metrics"]["token"]["test"]["fn"] == 1
    errors = read_json(output / "error_analysis.json")
    assert errors["diagnostics"] == []
    assert any(row["kind"] == "FN" and "не указано" in row["analysis"] for row in errors["test_errors"])
    assignments = load_assignments(directory / "assignments.json")
    for method in ("token", "ast"):
        assert set(run["config_hashes_by_assignment"][method]) == {task, "test-only-task"}
        expected = stable_hash(configuration_payload(assignments["test-only-task"], SimilarityConfig(method=method)))
        assert run["config_hashes_by_assignment"][method]["test-only-task"] == expected
