"""Обычные контракты AST-нормализации, отказов и исходных координат."""
from dataclasses import asdict, replace

import pytest

from school_lab.features import ast_occurrences, prepare_submission
from school_lab.hashing import submission_hash
from school_lab.models import Assignment, Evidence
from school_lab.parsing import parse_submission
from school_lab.similarity import compare_prepared, compare_submissions, top_k, validate_pair_result
from school_lab.similarity_models import SimilarityConfig
from school_lab.source_text import physical_lines


BASE = "def solve(values):\n    total = 0\n    for value in values:\n        total += value\n    return total\n"
RENAMED = "def answer(items):\n    result = 0\n    for item in items:\n        result += item\n    return result\n"
TEMPLATE = "from typing import Iterable"


@pytest.fixture
def inputs(raw_submission):
    """Вход: пример JSON. Выход: две сдачи разных студентов и общее задание."""
    left = replace(parse_submission(raw_submission), code=BASE)
    right = replace(left, submission_id="second", student_id="student-2")
    return left, right, Assignment(left.assignment_id, "", ())


def test_ast_renaming_preserves_structure_while_token_score_decreases(inputs):
    left, right, assignment = inputs
    right = replace(right, code=RENAMED)
    assert compare_submissions(left, right, assignment, SimilarityConfig()).score < 1
    assert compare_submissions(left, right, assignment, SimilarityConfig(method="ast")).score == 1


def test_ast_copy_is_symmetric_with_original_evidence_after_comments(inputs):
    left, right, assignment = inputs
    right = replace(right, code="# heading\n\n" + BASE.replace("total = 0", "total=0"))
    config = SimilarityConfig(method="ast", max_evidence=1000)
    a, b = [prepare_submission(item, assignment, config) for item in (left, right)]
    forward, backward = compare_prepared(a, b), compare_prepared(b, a)
    assert forward.score == backward.score == 1
    assert forward.review_required and forward.evidence
    assert len(forward.evidence) == len(backward.evidence)
    for item, reverse in zip(forward.evidence, backward.evidence):
        assert item.feature == reverse.feature
        assert item.left == reverse.right and item.right == reverse.left
        assert item.right.start_line == item.left.start_line + 2
        assert item.right.end_line == item.left.end_line + 2
    validate_pair_result(forward, a, b)


@pytest.mark.parametrize("source", [
    "def solve(values):\n    return sum(values)\n", BASE.replace("+=", "-="),
])
def test_ast_distinguishes_structure_and_operators(inputs, source):
    left, right, assignment = inputs
    result = compare_submissions(left, replace(right, code=source), assignment, SimilarityConfig(method="ast"))
    assert result.status == "ok" and result.score < 1


@pytest.mark.parametrize("source", ["", "# only comment\n", "\n \n"])
def test_ast_empty_union_and_one_empty_source(inputs, source):
    left, right, assignment = inputs
    config = SimilarityConfig(method="ast")
    empty_left, empty_right = replace(left, code=source), replace(right, code="")
    empty = compare_submissions(empty_left, empty_right, assignment, config)
    assert (empty.status, empty.score, empty.review_required, empty.evidence) == (
        "insufficient_data", None, False, (),
    )
    assert compare_submissions(empty_left, right, assignment, config).score == 0


def test_ast_syntax_error_has_explicit_reason(inputs):
    left, right, assignment = inputs
    result = compare_submissions(left, replace(right, code="def bad(:\n"), assignment, SimilarityConfig(method="ast"))
    assert result.status == "invalid_source" and result.score is None
    assert result.method == "ast" and result.reason
    assert not result.evidence and not result.review_required


@pytest.mark.parametrize("source", [
    "def f(xs):\n    return [x + 1 for x in xs]\n",
    "def f(xs):\n    return {x for x in xs}\n",
    "def f(xs):\n    return {x: x + 1 for x in xs}\n",
    "def f(xs):\n    return sum(x for x in xs)\n",
    "def f(x):\n    return lambda y: x + y\n",
    "def f(x):\n    def g(y):\n        return y\n    return g(x)\n",
    "def f(x=1):\n    return x\n",
    "def f(x: int):\n    return x\n",
    "def f(x) -> int:\n    return x\n",
    "def f(x):\n    answer: int = x\n    return answer\n",
    "@decorator\ndef f(x):\n    return x\n",
    "def f(x):\n    return f(x - 1)\n",
    "def f(x):\n    global other\n    return x\n",
    "def f(x):\n    nonlocal other\n    return x\n",
    "def f(x):\n    import os\n    return x\n",
    "class Example:\n    pass\n",
    "async def f(x):\n    return x\n",
    "def f(x):\n    match x:\n        case 0:\n            return 1\n    return x\n",
    "def f(x):\n    try:\n        return x\n    except ValueError as error:\n        return error\n",
    "def f(x):\n    return (other := x + 1)\n",
    "def f(x):\n    type Container[T] = list[T]\n    return x\n",
    "def f[T](x):\n    return x\n",
    "def f(x):\n    return x\ndef g(x):\n    return x\n",
])
def test_ast_unsupported_constructs_never_fall_back_to_token_score(inputs, source):
    left, right, assignment = inputs
    result = compare_submissions(left, replace(right, code=source), assignment, SimilarityConfig(method="ast"))
    assert result.status == "unsupported" and result.score is None
    assert result.method == "ast" and result.reason
    assert not result.review_required and not result.evidence


@pytest.mark.parametrize("first,second", [
    ("return sum(items)", "return max(items)"),
    ("return items.value", "return items.other"),
    ("return items + 1", "return items + 2"),
    ("return items", "return v0"),
])
def test_ast_preserves_external_names_attributes_and_literals(inputs, first, second):
    left, right, assignment = inputs
    left = replace(left, code="def solve(items):\n    " + first + "\n")
    right = replace(right, code="def solve(items):\n    " + second + "\n")
    result = compare_submissions(left, right, assignment, SimilarityConfig(method="ast"))
    assert result.status == "ok" and result.score < 1


def test_ast_parameter_shadowing_function_name_remains_local(inputs):
    left, right, assignment = inputs
    left = replace(left, code="def solve(solve):\n    return solve + 1\n")
    right = replace(right, code="def answer(value):\n    return value + 1\n")
    result = compare_submissions(left, right, assignment, SimilarityConfig(method="ast"))
    assert result.status == "ok" and result.score == 1


def test_ast_name_order_follows_dictionary_source_positions():
    """Вход: имена в Dict до присваивания. Выход: нумерация по тексту, не keys/values."""
    source = ("def solve():\n"
              "    result = {first: second, third: fourth}\n"
              "    first, second, third, fourth = 1, 2, 3, 4\n"
              "    return result\n")
    dictionary = next(item for item in ast_occurrences(source) if item.feature.startswith("Dict("))
    assert dictionary.feature == (
        "Dict(keys=[Name(id='v1', ctx=Load()), Name(id='v3', ctx=Load())], "
        "values=[Name(id='v2', ctx=Load()), Name(id='v4', ctx=Load())])"
    )
    assert (dictionary.start_line, dictionary.end_line) == (2, 2)


def test_ast_allowed_import_is_removed_before_scope_check(inputs):
    left, right, assignment = inputs
    assignment = replace(assignment, allowed_template=TEMPLATE)
    left = replace(left, code=TEMPLATE + "\n\n" + BASE)
    right = replace(right, code="# intro\n" + TEMPLATE + "\n" + BASE)
    config = SimilarityConfig(method="ast", max_evidence=1000)
    a, b = [prepare_submission(item, assignment, config) for item in (left, right)]
    result = compare_prepared(a, b)
    assert result.score == 1 and result.evidence
    assert result.excluded_left == (Evidence(left.submission_id, "code", 1, 1),)
    assert result.excluded_right == (Evidence(right.submission_id, "code", 2, 2),)
    assert all(item.left.start_line >= 3 and item.right.start_line >= 3 for item in result.evidence)
    validate_pair_result(result, a, b)


def test_ast_template_inside_function_preserves_return_coordinates(inputs):
    left, right, assignment = inputs
    assignment = replace(assignment, allowed_template="    template_value = 17")
    source = "def solve(value):\n    before = value + 1\n    template_value = 17\n    return before\n"
    left, right = replace(left, code=source), replace(right, code=source)
    result = compare_submissions(left, right, assignment, SimilarityConfig(method="ast", max_evidence=1000))
    assert result.score == 1 and result.evidence
    assert all(not item.left.start_line <= 3 <= item.left.end_line for item in result.evidence)
    assert all(not item.right.start_line <= 3 <= item.right.end_line for item in result.evidence)
    returns = [item for item in result.evidence if item.feature.startswith("Return(")]
    assert len(returns) == 1
    assert returns[0].left.start_line == returns[0].right.start_line == 4


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("prefix", ["", "\f"])
def test_ast_original_coordinates_and_hash_survive_normalization(raw_submission, newline, prefix):
    lines = ["def f(x):", prefix + "    marker = 'a\u2028b\x85c'", "    return x"]
    source = newline.join(lines) + newline
    original = replace(parse_submission(raw_submission), code=source)
    snapshot, digest = asdict(original), submission_hash(original)
    assignment = Assignment(original.assignment_id, "    return x", ())
    config = SimilarityConfig(method="ast", max_evidence=1000, exclude_template=False)
    left = prepare_submission(original, assignment, config)
    right = prepare_submission(replace(original, submission_id="other", student_id="other"), assignment, config)
    result = compare_prepared(left, right)
    assert result.score == 1 and physical_lines(left.code) == lines
    returns = [item for item in result.evidence if item.feature.startswith("Return(")]
    assert len(returns) == 1
    assert (returns[0].left.start_line, returns[0].left.end_line) == (3, 3)

    excluded = prepare_submission(original, assignment, replace(config, exclude_template=True))
    assert excluded.status == "ok" and excluded.features
    assert excluded.excluded_template == (Evidence(original.submission_id, "code", 3, 3),)
    assert all(item.end_line < 3 for item in excluded.occurrences)
    assert not any(item.startswith("Return(") for item in excluded.features)
    assert asdict(original) == snapshot and original.code == left.code == excluded.code == source
    assert submission_hash(original) == left.submission_hash == excluded.submission_hash == digest


def test_ast_line_endings_preserve_features_but_change_original_hash(raw_submission):
    base = replace(parse_submission(raw_submission), code="def f(x):\n    return x\n")
    assignment = Assignment(base.assignment_id, "", ())
    config = SimilarityConfig(method="ast")
    prepared = [prepare_submission(replace(base, code=base.code.replace("\n", newline)), assignment, config)
                for newline in ("\n", "\r\n", "\r")]
    assert prepared[0].features == prepared[1].features == prepared[2].features
    assert len({item.submission_hash for item in prepared}) == 3


def test_ast_top_k_skips_unsupported_and_invalid_sources(inputs):
    left, right, assignment = inputs
    config = SimilarityConfig(method="ast")
    query = prepare_submission(left, assignment, config)
    candidates = [prepare_submission(replace(right, submission_id=identifier, code=source), assignment, config)
                  for identifier, source in (("unsupported", "def f(xs):\n    return [x for x in xs]\n"),
                                             ("invalid", "def bad(:\n"), ("valid", BASE))]
    assert [item.status for item in candidates] == ["unsupported", "invalid_source", "ok"]
    assert [item.right_id for item in top_k(query, candidates)] == ["valid"]


@pytest.mark.parametrize("change", ["method", "version", "template"])
def test_ast_context_changes_reject_stale_features_and_report(inputs, change):
    left, right, assignment = inputs
    config = SimilarityConfig(method="ast")
    before = [prepare_submission(item, assignment, config) for item in (left, right)]
    old_report = compare_prepared(*before)
    if change == "method":
        config = SimilarityConfig(method="token")
    elif change == "version":
        config = replace(config, config_version="2")
    else:
        assignment = replace(assignment, allowed_template=BASE)
    after = [prepare_submission(item, assignment, config) for item in (left, right)]
    assert before[0].config_hash != after[0].config_hash
    with pytest.raises(ValueError):
        compare_prepared(before[0], after[1])
    with pytest.raises(ValueError):
        validate_pair_result(old_report, *after)


@pytest.mark.parametrize("location,expected_status", [("body", "ok"), ("default", "unsupported")])
def test_ast_never_executes_source(inputs, temp_dir, location, expected_status):
    left, right, assignment = inputs
    marker = temp_dir / "must-not-exist"
    expression = f"open({str(marker)!r}, 'w').write('unsafe')"
    source = (f"def solve():\n    {expression}\n    return 1\n" if location == "body" else
              f"def solve(value={expression}):\n    return value\n")
    result = compare_submissions(replace(left, code=source), replace(right, code=source), assignment,
                                 SimilarityConfig(method="ast"))
    assert result.status == expected_status and not marker.exists()
    assert result.score == (1 if expected_status == "ok" else None)
