"""Контракт сходства, шаблонов, отказов, координат и версий конфигурации."""
import json
from dataclasses import replace

import pytest

from school_lab.features import prepare_submission, token_occurrences
from school_lab.models import Assignment, Evidence
from school_lab.parsing import parse_submission
from school_lab.similarity import (
    compare_prepared, compare_submissions, eligible, jaccard, top_k, validate_pair_result,
)
from school_lab.similarity_models import SimilarityConfig


BASE = "def solve(values):\n    total = 0\n    for value in values:\n        total += value\n    return total\n"
RENAMED = "def answer(items):\n    result = 0\n    for item in items:\n        result += item\n    return result\n"
TEMPLATE = "from typing import Iterable"


@pytest.fixture
def inputs(raw_submission):
    """Вход: fixture JSON. Выход: две версии разных студентов и задание."""
    left = replace(parse_submission(raw_submission), code=BASE)
    right = replace(left, submission_id="second", student_id="student-2")
    return left, right, Assignment(left.assignment_id, "", ())


@pytest.mark.parametrize("changes", [
    {"method": "unknown"}, {"n": 0}, {"n": True}, {"n": 1.5},
    {"exclude_template": "false"}, {"max_evidence": -1}, {"max_evidence": True},
    {"config_version": " "},
])
def test_config_rejects_ambiguous_values(changes):
    with pytest.raises(ValueError):
        SimilarityConfig(**changes)


@pytest.mark.parametrize("a,b,expected", [
    (set(), set(), None), (set(), {1}, 0), ({1}, {1}, 1),
    ({1, 2, 3}, {2, 3, 4}, 0.5), ({1}, {2}, 0),
])
def test_jaccard_known_values(a, b, expected):
    assert jaccard(a, b) == expected


@pytest.mark.parametrize("method", ["token"])
def test_exact_copy_and_reversed_evidence(inputs, method):
    left, right, assignment = inputs
    right = replace(right, code="# дополнительная строка\n\n" + right.code)
    config = SimilarityConfig(method=method, max_evidence=1000)
    a, b = (prepare_submission(item, assignment, config) for item in (left, right))
    result, reverse = compare_prepared(a, b), compare_prepared(b, a)
    assert result.score == reverse.score == 1
    assert result.review_required
    assert result.evidence
    assert len(result.evidence) == len(reverse.evidence)
    for item, other in zip(result.evidence, reverse.evidence):
        assert item.feature == other.feature
        assert item.left == other.right and item.right == other.left
        assert item.right.start_line == item.left.start_line + 2
    validate_pair_result(result, a, b)
    assert json.loads(json.dumps(result.to_dict())) == result.to_dict()


def test_token_formatting_and_comments_do_not_change_features(inputs):
    left, right, assignment = inputs
    right = replace(right, code=BASE.replace("total = 0", "total=0 # zero").replace("total += value", "total+=value"))
    result = compare_submissions(left, right, assignment, SimilarityConfig())
    assert result.score == 1


def test_renaming_reduces_token_similarity(inputs):
    left, right, assignment = inputs
    right = replace(right, code=RENAMED)
    assert compare_submissions(left, right, assignment, SimilarityConfig()).score < 1


@pytest.mark.parametrize("source", ["", "# only comment\n", "\n \n"])
@pytest.mark.parametrize("method", ["token"])
def test_empty_union_and_single_empty(inputs, source, method):
    left, right, assignment = inputs
    config = SimilarityConfig(method=method)
    empty_left, empty_right = replace(left, code=source), replace(right, code="")
    result = compare_submissions(empty_left, empty_right, assignment, config)
    assert (result.status, result.score, result.review_required, result.evidence) == (
        "insufficient_data", None, False, (),
    )
    assert compare_submissions(empty_left, right, assignment, config).score == 0


@pytest.mark.parametrize("method", ["token"])
def test_syntax_error_is_explicit_and_not_ranked(inputs, method):
    left, right, assignment = inputs
    right = replace(right, code="def bad(:\n")
    config = SimilarityConfig(method=method)
    a, b = (prepare_submission(item, assignment, config) for item in (left, right))
    result = compare_prepared(a, b)
    assert result.status == "invalid_source" and result.score is None
    assert result.reason and result.method == method
    assert top_k(a, [b]) == []


def test_token_template_exclusion_does_not_bridge_neighbors():
    source = "left = 1\n" + TEMPLATE + "\nright = 2\n"
    kept = token_occurrences(source, excluded=((2, 2),))
    assert [(item.start_line, item.end_line) for item in kept] == [(1, 1), (3, 3)]
    full = token_occurrences(source)
    assert any(item.start_line < item.end_line for item in full)


def test_partial_multiline_token_intersection_is_excluded():
    source = 'value = """first\nsecond\nthird"""\nother = 2\n'
    kept = token_occurrences(source, excluded=((2, 2),))
    assert kept and all(item.start_line == item.end_line == 4 for item in kept)


@pytest.mark.parametrize("method", ["token"])
def test_exact_template_and_original_coordinates(inputs, method):
    left, right, assignment = inputs
    assignment = replace(assignment, allowed_template=TEMPLATE)
    left = replace(left, code=TEMPLATE + "\n\n" + BASE)
    right = replace(right, code="# intro\n" + TEMPLATE + "\n" + BASE)
    config = SimilarityConfig(method=method, max_evidence=1000)
    a, b = (prepare_submission(item, assignment, config) for item in (left, right))
    result = compare_prepared(a, b)
    assert result.score == 1
    assert result.excluded_left == (Evidence(left.submission_id, "code", 1, 1),)
    assert result.excluded_right == (Evidence(right.submission_id, "code", 2, 2),)
    assert result.evidence and all(item.left.start_line >= 3 for item in result.evidence)
    assert all(item.right.start_line >= 3 for item in result.evidence)
    validate_pair_result(result, a, b)


def test_template_matching_is_exact_and_preserves_repeated_ranges(inputs):
    left, _, assignment = inputs
    assignment = replace(assignment, allowed_template=TEMPLATE)
    left = replace(left, code=TEMPLATE + "\n" + TEMPLATE + "\n" + TEMPLATE + "  \n" + BASE)
    prepared = prepare_submission(left, assignment, SimilarityConfig())
    assert [(item.start_line, item.end_line) for item in prepared.excluded_template] == [(1, 1), (2, 2)]


def test_same_student_self_and_other_assignment_are_filtered_before_ranking(inputs):
    left, right, assignment = inputs
    config = SimilarityConfig()
    query = prepare_submission(left, assignment, config)
    duplicate = prepare_submission(replace(left, submission_id="revision"), assignment, config)
    other = prepare_submission(
        replace(right, assignment_id="other"), Assignment("other", "", ()), config,
    )
    candidate_b = prepare_submission(replace(right, submission_id="b"), assignment, config)
    candidate_a = prepare_submission(replace(right, submission_id="a"), assignment, config)
    assert not eligible(left, left)
    assert not eligible(query, duplicate)
    assert not eligible(query, other)
    assert compare_prepared(query, duplicate).status == "ineligible"
    assert [item.right_id for item in top_k(query, [query, duplicate, other, candidate_b, candidate_a])] == ["a", "b"]


def test_top_k_deduplicates_and_limits_tied_candidates(inputs):
    left, right, assignment = inputs
    config = SimilarityConfig()
    query = prepare_submission(left, assignment, config)
    candidates = [prepare_submission(replace(right, submission_id=identifier), assignment, config)
                  for identifier in ("b", "a", "b")]
    assert [item.right_id for item in top_k(query, iter(candidates), k=1)] == ["a"]
    assert [item.right_id for item in top_k(query, iter(candidates), k=5)] == ["a", "b"]


@pytest.mark.parametrize("k", [0, -1, True, 1.5])
def test_top_k_rejects_invalid_limit(inputs, k):
    left, _, assignment = inputs
    query = prepare_submission(left, assignment, SimilarityConfig())
    with pytest.raises(ValueError):
        top_k(query, [], k=k)


@pytest.mark.parametrize("changes", [{"n": 4}, {"config_version": "2"}, {"exclude_template": False}])
def test_configuration_changes_reject_stale_precomputed_values(inputs, changes):
    left, right, assignment = inputs
    a = prepare_submission(left, assignment, SimilarityConfig())
    b = prepare_submission(right, assignment, SimilarityConfig(**changes))
    assert a.config_hash != b.config_hash
    with pytest.raises(ValueError):
        compare_prepared(a, b)


def test_new_template_changes_hash_and_old_result_is_rejected(inputs):
    left, right, assignment = inputs
    config = SimilarityConfig()
    before = [prepare_submission(item, assignment, config) for item in (left, right)]
    old = compare_prepared(*before)
    after = [prepare_submission(item, replace(assignment, allowed_template=BASE), config) for item in (left, right)]
    assert before[0].config_hash != after[0].config_hash
    assert compare_prepared(*after).status == "insufficient_data"
    with pytest.raises(ValueError):
        validate_pair_result(old, *after)


def test_report_tracks_original_source_version(inputs):
    left, right, assignment = inputs
    config = SimilarityConfig()
    before = [prepare_submission(item, assignment, config) for item in (left, right)]
    old = compare_prepared(*before)
    revised = prepare_submission(replace(right, code=right.code + "# новая версия\n"), assignment, config)
    new = compare_prepared(before[0], revised)
    assert old.score == new.score == 1
    assert old.right_hash != new.right_hash
    with pytest.raises(ValueError):
        validate_pair_result(old, before[0], revised)


@pytest.mark.parametrize("threshold", [True, float("nan"), float("inf"), -0.01, 1.1, "0.5", 10 ** 1000])
def test_invalid_threshold_is_rejected(inputs, threshold):
    left, right, assignment = inputs
    with pytest.raises(ValueError):
        compare_submissions(left, right, assignment, SimilarityConfig(), threshold=threshold)


def test_threshold_above_one_means_no_positive_predictions(inputs):
    left, right, assignment = inputs
    result = compare_submissions(left, right, assignment, SimilarityConfig(), threshold=1.000001)
    assert result.score == 1 and not result.review_required


@pytest.mark.parametrize("field,value", [
    ("score", True), ("score", float("nan")), ("score", 0.3), ("score", 10 ** 1000),
    ("left_hash", "wrong"), ("config_hash", "wrong"), ("review_required", False),
    ("schema_version", True), ("status", "unknown"),
])
def test_validator_rejects_corrupted_contract(inputs, field, value):
    left, right, assignment = inputs
    prepared = [prepare_submission(item, assignment, SimilarityConfig()) for item in (left, right)]
    result = replace(compare_prepared(*prepared), **{field: value})
    with pytest.raises(ValueError):
        validate_pair_result(result, *prepared)


def test_validator_checks_feature_at_referenced_lines_not_only_bounds(inputs):
    left, right, assignment = inputs
    config = SimilarityConfig(max_evidence=1000)
    a, b = [prepare_submission(item, assignment, config) for item in (left, right)]
    result = compare_prepared(a, b)
    item = next(item for item in result.evidence if item.left.start_line == item.left.end_line == 2)
    wrong = replace(item, left=replace(item.left, start_line=4, end_line=4))
    changed = replace(result, evidence=(wrong,))
    with pytest.raises(ValueError, match="Признак не находится"):
        validate_pair_result(changed, a, b)


def test_validator_rejects_unhashable_feature_with_value_error(inputs):
    left, right, assignment = inputs
    a, b = [prepare_submission(item, assignment, SimilarityConfig()) for item in (left, right)]
    result = compare_prepared(a, b)
    wrong = replace(result.evidence[0], feature=[])
    with pytest.raises(ValueError):
        validate_pair_result(replace(result, evidence=(wrong,)), a, b)


@pytest.mark.parametrize("max_evidence", [0, 1])
def test_evidence_limit_preserves_score(inputs, max_evidence):
    left, right, assignment = inputs
    result = compare_submissions(left, right, assignment, SimilarityConfig(max_evidence=max_evidence))
    assert result.score == 1
    assert len(result.evidence) == max_evidence


@pytest.mark.parametrize("changes", [
    {"submission_id": "unknown"}, {"field": "text"}, {"start_line": 0},
    {"end_line": 1000}, {"start_line": True}, {"end_line": 1.5},
])
def test_validator_rejects_forged_evidence_references(inputs, changes):
    left, right, assignment = inputs
    a, b = [prepare_submission(item, assignment, SimilarityConfig()) for item in (left, right)]
    result = compare_prepared(a, b)
    item = result.evidence[0]
    forged = replace(item, left=replace(item.left, **changes))
    with pytest.raises(ValueError):
        validate_pair_result(replace(result, evidence=(forged,)), a, b)


@pytest.mark.parametrize("failure", ["duplicate", "missing", "nonmatching"])
def test_validator_rejects_invalid_evidence_collection(inputs, failure):
    left, right, assignment = inputs
    a, b = [prepare_submission(item, assignment, SimilarityConfig()) for item in (left, right)]
    result = compare_prepared(a, b)
    item = result.evidence[0]
    evidence = {"duplicate": (item, item), "missing": (),
                "nonmatching": (replace(item, feature="not a common feature"),)}[failure]
    with pytest.raises(ValueError):
        validate_pair_result(replace(result, evidence=evidence), a, b)
