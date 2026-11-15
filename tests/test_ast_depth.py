"""Глубокий синтаксически корректный AST даёт явный отказ без падения конвейера."""
import ast
from dataclasses import replace

import pytest

from school_lab.features import UnsupportedAST, ast_occurrences, prepare_submission
from school_lab.models import Assignment
from school_lab.parsing import parse_submission
from school_lab.similarity import compare_prepared
from school_lab.similarity_models import SimilarityConfig


DEEP_SOURCE = "def solve(value):\n    return " + " + ".join(["value"] * 500) + "\n"


def test_deep_ast_returns_explicit_unsupported_error():
    """Успешный ast.parse не означает, что последующая обработка уложится в глубину."""
    assert isinstance(ast.parse(DEEP_SOURCE), ast.Module)
    with pytest.raises(UnsupportedAST, match="глубина обработки AST"):
        ast_occurrences(DEEP_SOURCE)


def test_deep_ast_preserves_preparation_and_pair_contract(raw_submission):
    """Отказ глубокого дерева не выдаёт частичных признаков или численной оценки."""
    original = parse_submission(raw_submission)
    assignment = Assignment(original.assignment_id, "", ())
    config = SimilarityConfig(method="ast")
    deep = prepare_submission(replace(original, code=DEEP_SOURCE), assignment, config)
    normal = prepare_submission(
        replace(original, submission_id="second", student_id="student-2",
                code="def solve(value):\n    return value\n"),
        assignment, config,
    )
    assert deep.status == "unsupported"
    assert deep.reason and "глубина обработки AST" in deep.reason
    assert not deep.features and not deep.occurrences
    result = compare_prepared(deep, normal)
    assert result.status == "unsupported" and result.score is None
    assert not result.review_required and not result.evidence
