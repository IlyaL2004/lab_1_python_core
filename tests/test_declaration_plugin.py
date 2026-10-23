"""Проверки отдельно установленного пакета и его настоящего entry point."""

from dataclasses import asdict, replace
from importlib.metadata import entry_points, version

import pytest

from school_lab.hashing import stable_hash, submission_hash
from school_lab.models import AIDeclaration, AnalysisReport, Assignment
from school_lab.parsing import parse_submission
from school_lab.reports import AnalysisContext, validate_report
from school_lab.repositories import InMemorySubmissionRepository
from school_lab.services import SubmissionService


@pytest.fixture
def external_analyzer():
    """Создать анализатор из entry point установленного пакета."""
    points = entry_points(group="school_lab.analyzers", name="external-declaration")
    assert len(points) == 1, "Установите пакет plugins/declaration перед запуском тестов"
    return next(iter(points)).load()()


def test_plugin_is_discovered_from_installed_package(external_analyzer):
    """Проверить метаданные пакета, точку входа и версию API плагина."""
    points = entry_points(group="school_lab.analyzers", name="external-declaration")
    point = next(iter(points))

    assert point.value == "declaration_plugin:ExternalDeclarationAnalyzer"
    assert point.dist.metadata["Name"] == "school-declaration-plugin"
    assert version("school-declaration-plugin") == "1.0.0"
    assert type(external_analyzer.plugin_api_version) is int
    assert external_analyzer.plugin_api_version == 1
    assert external_analyzer.analyzer_id == "external-declaration"


@pytest.mark.parametrize("used", [False, True])
@pytest.mark.parametrize("verified", [False, True])
def test_plugin_analyzes_accepted_submission(raw_submission, external_analyzer, used, verified):
    """Проверить отчёт принятой сдачи и сохранность её исходных данных."""
    raw_submission["ai_declaration"] = {
        "used": used,
        "tool": "Учебный помощник" if used else None,
        "purpose": "Разбор примера" if used else None,
        "verified_by_student": verified,
    }
    repository = InMemorySubmissionRepository()
    acceptance = SubmissionService(repository).accept(raw_submission)
    submission = repository.get(acceptance.submission_id)
    assert submission is not None
    original = asdict(submission)
    context = AnalysisContext(Assignment(submission.assignment_id, "", ()))

    report = external_analyzer.analyze(submission, context)

    validate_report(report, submission, context)
    assert isinstance(report, AnalysisReport)
    assert report.analyzer_id == "external-declaration"
    assert report.analyzer_version == "1.0"
    assert report.submission_hash == acceptance.content_hash
    assert report.config_hash == stable_hash(context.config_payload())
    assert report.review_required is False
    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.kind == "rubric_feedback"
    assert finding.score is None
    assert finding.evidence == ()
    assert ("инструмент и цель заполнены" if used else "ИИ не заявлено") in finding.explanation
    assert asdict(submission) == original
    assert repository.get(submission.submission_id) is submission


@pytest.mark.parametrize("missing_field", ["tool", "purpose"])
def test_plugin_handles_incomplete_model_created_without_parser(
    raw_submission, external_analyzer, missing_field,
):
    """Запросить проверку неполной декларации, созданной в обход парсера."""
    submission = parse_submission(raw_submission)
    declaration = AIDeclaration(True, "Учебный помощник", "Разбор примера", True)
    # Парсер такую декларацию не принимает; здесь проверяется прямой вызов плагина.
    declaration = replace(declaration, **{missing_field: None})
    submission = replace(submission, ai_declaration=declaration)
    context = AnalysisContext(Assignment(submission.assignment_id, "", ()))

    report = external_analyzer.analyze(submission, context)

    validate_report(report, submission, context)
    assert report.review_required is True
    assert "инструмент или цель не заполнены" in report.findings[0].explanation


def test_plugin_report_tracks_configuration(raw_submission, external_analyzer):
    """Отразить смену настроек в хеше отчёта при неизменной сдаче."""
    submission = parse_submission(raw_submission)
    context = AnalysisContext(Assignment(submission.assignment_id, "", ()))
    updated_context = replace(context, config_version="2")

    first = external_analyzer.analyze(submission, context)
    second = external_analyzer.analyze(submission, updated_context)

    validate_report(first, submission, context)
    validate_report(second, submission, updated_context)
    assert first.submission_hash == second.submission_hash == submission_hash(submission)
    assert first.config_hash != second.config_hash
    assert first.findings == second.findings
