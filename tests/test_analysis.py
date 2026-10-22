"""Контракты анализаторов, проверка отчётов и расширение через реестр."""

from dataclasses import asdict, replace
from datetime import datetime
from types import SimpleNamespace

import pytest

from school_lab.analyzers import (
    DeclarationAnalyzer,
    LengthAnalyzer,
    MissingExplanationAnalyzer,
    TemplateAnalyzer,
)
from school_lab.errors import AnalysisFailure
from school_lab.hashing import stable_hash, submission_hash
from school_lab.models import Assignment, Evidence, Finding
from school_lab.parsing import parse_submission
from school_lab.reports import AnalysisContext, make_report, validate_report
from school_lab.repositories import InMemorySubmissionRepository
from school_lab.services import AnalysisService, AnalyzerRegistry, SubmissionService


@pytest.fixture
def context() -> AnalysisContext:
    """Вход: нет. Выход: настройки задания, совпадающего с raw_submission."""
    return AnalysisContext(Assignment("lab-1", "print(2 + 3)\n", ("Пояснить решение",)))


@pytest.mark.parametrize("analyzer_class", [
    LengthAnalyzer, TemplateAnalyzer, DeclarationAnalyzer, MissingExplanationAnalyzer,
])
def test_shared_analyzer_contract(raw_submission: dict, context, analyzer_class) -> None:
    """Все реализации сохраняют сдачу и возвращают проверяемый отчёт с хешами."""
    submission = parse_submission(raw_submission)
    before = asdict(submission)
    digest = submission_hash(submission)
    analyzer = analyzer_class()

    report = analyzer.analyze(submission, context)

    validate_report(report, submission, context)
    assert report.submission_id == submission.submission_id
    assert report.submission_hash == digest
    assert report.config_hash == stable_hash(context.config_payload())
    assert report.analyzer_id == analyzer.analyzer_id
    assert report.analyzer_version == analyzer.analyzer_version
    assert report.report_id
    assert datetime.fromisoformat(report.created_at).utcoffset() is not None
    assert isinstance(report.findings, tuple)
    assert all(finding.explanation.strip() for finding in report.findings)
    assert asdict(submission) == before
    assert submission_hash(submission) == digest


def test_length_evidence_preserves_blank_lines_and_crlf(raw_submission: dict, context) -> None:
    """Пустые строки не считаются, но позиции evidence относятся к исходному тексту."""
    raw_submission.update(text="\r\nПервая\r\n \r\nВторая\r\n", code="\nprint(1)\n\n")
    submission = parse_submission(raw_submission)

    report = LengthAnalyzer().analyze(submission, context)
    finding = report.findings[0]

    assert [(item.field, item.start_line, item.end_line) for item in finding.evidence] == [
        ("text", 2, 2), ("text", 4, 4), ("code", 2, 2),
    ]
    assert "text=2" in finding.explanation
    assert "code=1" in finding.explanation
    assert finding.score is None
    assert report.review_required is False


@pytest.mark.parametrize(("code", "template", "expected"), [
    ("before\r\nx\r\ny\r\nafter\r\n", "x\ny\n", [(2, 3)]),
    ("x\nx\nx\n", "x\nx\n", [(1, 2), (2, 3)]),
    (" x\ny\n", "x\ny\n", []),
    ("print(1)\n", " \n", []),
])
def test_template_exact_ranges_are_permitted(raw_submission: dict, code, template, expected) -> None:
    """Точные блоки, включая пересечения, не ведут к оценке или запросу санкции."""
    raw_submission["code"] = code
    submission = parse_submission(raw_submission)
    context = AnalysisContext(Assignment("lab-1", template, ()))

    report = TemplateAnalyzer().analyze(submission, context)
    finding = report.findings[0]

    assert [(item.start_line, item.end_line) for item in finding.evidence] == expected
    assert all(item.field == "code" for item in finding.evidence)
    assert finding.kind == "similarity"
    assert finding.score is None
    assert report.review_required is False


@pytest.mark.parametrize(("text", "code", "review"), [
    (" \n", "\r\nprint(1)\r\n\r\n", True),
    ("Объяснение", "print(1)", False),
    ("Только текст", "", False),
])
def test_missing_explanation_refers_to_code(raw_submission: dict, context, text, code, review) -> None:
    """Отсутствие пояснения отмечается только при наличии кода и с корректной ссылкой."""
    raw_submission.update(text=text, code=code)
    submission = parse_submission(raw_submission)

    report = MissingExplanationAnalyzer().analyze(submission, context)

    assert report.review_required is review
    assert report.findings[0].score is None
    expected = (Evidence(submission.submission_id, "code", 1, 3),) if review else ()
    assert report.findings[0].evidence == expected


@pytest.mark.parametrize(("used", "verified", "review"), [
    (True, False, True), (True, True, False), (False, False, False),
])
def test_declaration_only_flags_unverified_assistance(raw_submission: dict, context, used, verified, review) -> None:
    """Декларация отражает заявление студента и факт проверки результата."""
    raw_submission["ai_declaration"].update(
        used=used, verified_by_student=verified,
        tool="Учебный помощник" if used else None,
        purpose="Разобрать пример" if used else None,
    )

    report = DeclarationAnalyzer().analyze(parse_submission(raw_submission), context)

    assert report.review_required is review
    assert report.findings[0].score is None
    assert report.findings[0].evidence == ()
    if used:
        assert "Учебный помощник" in report.findings[0].explanation
        assert "Разобрать пример" in report.findings[0].explanation


def test_config_hash_tracks_all_context_settings(raw_submission: dict, context) -> None:
    """Шаблон, требования, ID задания, режим и версия участвуют в хеше настроек."""
    original = context.config_payload()
    changed_contexts = [
        replace(context, assignment=replace(context.assignment, allowed_template="другой шаблон")),
        replace(context, assignment=replace(context.assignment, requirements=("Другое требование",))),
        replace(context, assignment=replace(context.assignment, assignment_id="lab-2")),
        replace(context, mode="stub"),
        replace(context, config_version="2"),
    ]

    assert all(stable_hash(item.config_payload()) != stable_hash(original) for item in changed_contexts)
    exported = context.config_payload()
    exported["assignment"]["allowed_template"] = "изменение копии"
    assert context.config_payload() == original
    submission = parse_submission(raw_submission)
    first = LengthAnalyzer().analyze(submission, context)
    second = LengthAnalyzer().analyze(submission, changed_contexts[-1])
    assert first.submission_hash == second.submission_hash
    assert first.config_hash != second.config_hash


@pytest.mark.parametrize(("field", "value"), [
    ("submission_id", "another"), ("submission_hash", "0" * 64),
    ("config_hash", "0" * 64), ("report_id", " "),
    ("analyzer_id", ""), ("analyzer_version", None),
    ("review_required", 1), ("created_at", "2026-10-23T12:00:00"),
    ("findings", []),
])
def test_report_rejects_invalid_metadata(raw_submission: dict, context, field, value) -> None:
    """Чужие версии данных и неверные обязательные поля отчёта отклоняются."""
    submission = parse_submission(raw_submission)
    report = LengthAnalyzer().analyze(submission, context)

    with pytest.raises(AnalysisFailure):
        validate_report(replace(report, **{field: value}), submission, context)


def test_report_requires_models_and_valid_finding(raw_submission: dict, context) -> None:
    """Словари вместо моделей и неверные поля наблюдения не проходят контракт."""
    submission = parse_submission(raw_submission)
    report = LengthAnalyzer().analyze(submission, context)
    finding = report.findings[0]
    invalid_reports = [
        asdict(report),
        replace(report, findings=({},)),
        replace(report, findings=(replace(finding, kind="verdict"),)),
        replace(report, findings=(replace(finding, explanation=" "),)),
        replace(report, findings=(replace(finding, evidence=[]),)),
        replace(report, findings=(replace(finding, evidence=({},)),)),
    ]
    for invalid in invalid_reports:
        with pytest.raises(AnalysisFailure):
            validate_report(invalid, submission, context)


@pytest.mark.parametrize("score", [True, "0.5", -0.1, 1.1, float("nan"), float("inf")])
def test_report_rejects_invalid_score(raw_submission: dict, context, score) -> None:
    """Оценка допускает только конечное число от 0 до 1 или None."""
    submission = parse_submission(raw_submission)
    finding = Finding("rubric_feedback", score, (), "Проверка оценки")

    with pytest.raises(AnalysisFailure):
        make_report(submission, context, "custom", "1.0", (finding,))


def test_report_accepts_score_boundaries(raw_submission: dict, context) -> None:
    """Нулевая, единичная, промежуточная и отсутствующая оценки допустимы."""
    submission = parse_submission(raw_submission)
    findings = tuple(Finding("rubric_feedback", score, (), "Результат") for score in (0, 1, 0.5, None))

    report = make_report(submission, context, "custom", "1.0", findings)

    assert report.findings == findings


@pytest.mark.parametrize(("field", "value"), [
    ("submission_id", "another"), ("field", "student_id"),
    ("start_line", 0), ("start_line", True),
    ("end_line", 99), ("end_line", 0),
])
def test_report_rejects_invalid_evidence(raw_submission: dict, context, field, value) -> None:
    """Evidence ссылается на существующие строки text/code именно этой сдачи."""
    submission = parse_submission(raw_submission)
    evidence = Evidence(submission.submission_id, "code", 1, 1)
    invalid = replace(evidence, **{field: value})
    finding = Finding("rubric_feedback", None, (invalid,), "Проверка ссылки")

    with pytest.raises(AnalysisFailure):
        make_report(submission, context, "custom", "1.0", (finding,))


def test_registry_preserves_first_registration_and_checks_factory() -> None:
    """Реестр отклоняет дубликаты, неизвестные имена и неверные фабрики."""
    registry = AnalyzerRegistry()
    registry.register("length", LengthAnalyzer)
    with pytest.raises(AnalysisFailure):
        registry.register("length", TemplateAnalyzer)
    assert isinstance(registry.create("length"), LengthAnalyzer)
    for name, factory in [(" ", LengthAnalyzer), ("bad", 123)]:
        with pytest.raises(AnalysisFailure):
            registry.register(name, factory)
    with pytest.raises(AnalysisFailure):
        registry.create("unknown")


@pytest.mark.parametrize("problem", ["id", "version", "method", "exception"])
def test_registry_rejects_broken_analyzer_contract(problem: str) -> None:
    """Фабрика должна успешно создать анализатор с согласованными ID и методом."""
    def factory():
        """Вход: нет. Выход: намеренно неверный анализатор либо RuntimeError."""
        if problem == "exception":
            raise RuntimeError("Сбой создания")
        return SimpleNamespace(
            analyzer_id="other" if problem == "id" else "bad",
            analyzer_version=" " if problem == "version" else "1.0",
            analyze=None if problem == "method" else lambda submission, context: None,
        )

    registry = AnalyzerRegistry()
    registry.register("bad", factory)

    with pytest.raises(AnalysisFailure):
        registry.create("bad")


def test_external_analyzer_works_without_service_changes(raw_submission: dict, context) -> None:
    """Новый класс подключается одной регистрацией без наследования и правок сервиса."""
    class CustomAnalyzer:
        """Учебное расширение: вход через analyze, выход — стандартный отчёт."""
        analyzer_id = "custom"
        analyzer_version = "2.0"

        def analyze(self, submission, context):
            """Вход: сдача и контекст. Выход: отчёт с собственным наблюдением."""
            finding = Finding("rubric_feedback", None, (), "Проверка расширения")
            return make_report(submission, context, self.analyzer_id, self.analyzer_version, (finding,))

    repository = InMemorySubmissionRepository()
    SubmissionService(repository).accept(raw_submission)
    saved = repository.get("submission-1")
    registry = AnalyzerRegistry()
    service = AnalysisService(repository, registry)
    registry.register("custom", CustomAnalyzer)

    report = service.run("submission-1", "custom", context)

    assert report.analyzer_id == "custom"
    assert report.analyzer_version == "2.0"
    assert report.findings[0].explanation == "Проверка расширения"
    assert repository.get("submission-1") is saved
    assert report.submission_hash == submission_hash(saved)


def test_service_requires_accepted_submission_and_matching_assignment(raw_submission: dict, context) -> None:
    """Анализ нельзя запустить до приёма или с контекстом другого задания."""
    repository = InMemorySubmissionRepository()
    registry = AnalyzerRegistry()
    registry.register("length", LengthAnalyzer)
    service = AnalysisService(repository, registry)
    with pytest.raises(AnalysisFailure):
        service.run("submission-1", "length", context)
    SubmissionService(repository).accept(raw_submission)
    wrong_context = replace(context, assignment=replace(context.assignment, assignment_id="another"))
    with pytest.raises(AnalysisFailure):
        service.run("submission-1", "length", wrong_context)
    assert service.run("submission-1", "length", context).submission_id == "submission-1"


@pytest.mark.parametrize("problem", ["id", "version", "result", "exception"])
def test_service_rejects_wrong_report_or_failed_analyzer(raw_submission: dict, context, problem) -> None:
    """Чужой отчёт, словарь и сбой алгоритма становятся AnalysisFailure."""
    class BrokenAnalyzer:
        """Анализатор с намеренной ошибкой для проверки границы сервиса."""
        analyzer_id = "broken"
        analyzer_version = "1.0"

        def analyze(self, submission, context):
            """Вход: сдача и контекст. Выход: неверный результат либо RuntimeError."""
            if problem == "exception":
                raise RuntimeError("Ошибка алгоритма")
            if problem == "result":
                return {}
            return make_report(
                submission, context, "other" if problem == "id" else self.analyzer_id,
                "2.0" if problem == "version" else self.analyzer_version, (),
            )

    repository = InMemorySubmissionRepository()
    SubmissionService(repository).accept(raw_submission)
    registry = AnalyzerRegistry()
    registry.register("broken", BrokenAnalyzer)

    with pytest.raises(AnalysisFailure):
        AnalysisService(repository, registry).run("submission-1", "broken", context)
    assert len(repository) == 1
