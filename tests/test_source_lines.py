"""Физические строки шаблонов и отчётов без изменения исходной сдачи."""
from dataclasses import asdict, replace

import pytest

from school_lab.analyzers import (
    LengthAnalyzer, MissingExplanationAnalyzer, TemplateAnalyzer, template_ranges,
)
from school_lab.errors import AnalysisFailure
from school_lab.hashing import submission_hash
from school_lab.models import Assignment, Evidence, Finding
from school_lab.parsing import parse_submission
from school_lab.reports import AnalysisContext, make_report
from school_lab.source_text import physical_lines


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_template_preserves_form_feed_and_spaces(newline):
    """Form feed остаётся частью строки и учитывается при точном совпадении."""
    source = "def f(x):" + newline + "\f    return x" + newline
    assert template_ranges(source, "\f    return x") == ((2, 2),)
    assert template_ranges(source, "    return x") == ()


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_template_report_preserves_ranges_and_original_source(raw_submission, newline):
    """Диапазон указывает на исходный блок, хотя шаблон использует другие переносы."""
    lines = ["def f(x):", "\f    marker = 'a\u2028b\x85c'", "    return marker"]
    source = newline.join(lines) + newline
    submission = replace(parse_submission(raw_submission), code=source)
    snapshot = asdict(submission)
    digest = submission_hash(submission)
    template = "\n".join(lines[1:]) + "\n"
    context = AnalysisContext(Assignment(submission.assignment_id, template, ()))

    report = TemplateAnalyzer().analyze(submission, context)

    finding = report.findings[0]
    assert finding.evidence == (Evidence(submission.submission_id, "code", 2, 3),)
    reference = finding.evidence[0]
    assert physical_lines(submission.code)[reference.start_line - 1:reference.end_line] == lines[1:]
    assert finding.score is None and not report.review_required
    assert report.submission_hash == digest
    assert submission.code == source
    assert asdict(submission) == snapshot
    assert submission_hash(submission) == digest


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_legacy_analyzers_and_report_bounds_use_physical_lines(raw_submission, newline):
    """Символы внутри литерала не создают строки для анализаторов и валидатора."""
    source = "def f(x):" + newline + "\f    return 'a\u2028b\x85c'" + newline
    submission = replace(parse_submission(raw_submission), code=source, text="")
    context = AnalysisContext(Assignment(submission.assignment_id, "", ()))

    length = LengthAnalyzer().analyze(submission, context)
    assert length.findings[0].evidence == (
        Evidence(submission.submission_id, "code", 1, 1),
        Evidence(submission.submission_id, "code", 2, 2),
    )
    missing = MissingExplanationAnalyzer().analyze(submission, context)
    assert missing.findings[0].evidence == (Evidence(submission.submission_id, "code", 1, 2),)
    assert missing.review_required

    phantom_line = Evidence(submission.submission_id, "code", 3, 3)
    with pytest.raises(AnalysisFailure, match="границы"):
        make_report(submission, context, "custom", "1", (
            Finding("rubric_feedback", None, (phantom_line,), "Несуществующая строка"),
        ))
