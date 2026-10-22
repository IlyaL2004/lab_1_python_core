"""Контекст анализа, протокол анализатора и проверка его результата."""
import math
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Protocol, Literal
from uuid import uuid4
from .models import Assignment, Submission, Evidence, Finding, AnalysisReport
from .hashing import stable_hash, submission_hash
from .errors import AnalysisFailure


@dataclass(frozen=True, slots=True)
class AnalysisContext:
    """Задание и настройки одного запуска анализа.

    assignment содержит разрешённый шаблон и требования. mode и config_version
    входят в config_hash, поэтому отчёт связан с конкретными настройками.

    Вход: assignment (Assignment) — задание; mode (str) — "baseline" или "stub";
    config_version (str) — версия настроек.
    Выход: экземпляр AnalysisContext с параметрами запуска.
    """
    assignment: Assignment
    # Конфигурация представлена явной неизменяемой моделью вместо общего dict.
    mode: Literal["baseline", "stub"] = "baseline"
    config_version: str = "1"

    def config_payload(self) -> dict[str, object]:
        """Вернуть новый JSON-совместимый словарь настроек для вычисления хеша.

        Вход: без аргументов; используются поля текущего контекста.
        Выход: dict[str, object] — задание, режим и версия настроек.
        """
        return {
            "assignment": asdict(self.assignment),
            "mode": self.mode,
            "config_version": self.config_version,
        }


class Analyzer(Protocol):
    """Общий интерфейс: имя, версия и синхронный анализ принятой сдачи.

    Вход analyze: Submission и AnalysisContext.
    Выход analyze: AnalysisReport; сам Protocol не выполняет анализ.
    """

    @property
    def analyzer_id(self) -> str:
        """Вернуть уникальное имя, совпадающее с именем в реестре.

        Вход: без аргументов; свойство текущего анализатора.
        Выход: str — имя анализатора.
        """
        ...

    @property
    def analyzer_version(self) -> str:
        """Вернуть версию алгоритма, которая будет записана в отчёт.

        Вход: без аргументов; свойство текущего анализатора.
        Выход: str — версия алгоритма.
        """
        ...

    def analyze(self, submission: Submission, context: AnalysisContext) -> AnalysisReport:
        """Вернуть наблюдения по submission с настройками context, не меняя сдачу.

        Вход: submission (Submission) — сдача; context (AnalysisContext) — настройки.
        Выход: AnalysisReport — отчёт с наблюдениями анализатора.
        """
        ...


def validate_report(report: AnalysisReport, value: Submission, ctx: AnalysisContext) -> None:
    """Проверить связь отчёта со сдачей value и настройками ctx.

    Проверяет также время, наблюдения, score и номера строк evidence.
    Вход: report (AnalysisReport) — отчёт; value (Submission) — исходная сдача;
    ctx (AnalysisContext) — настройки, с которыми выполнен анализ.
    Выход: None, если отчёт прошёл проверку. При нарушении контракта вызывает
    AnalysisFailure; отчёт и исходную сдачу не изменяет.
    """
    if not isinstance(report, AnalysisReport):
        raise AnalysisFailure("Ожидается AnalysisReport")
    if report.submission_id != value.submission_id or report.submission_hash != submission_hash(value):
        raise AnalysisFailure("Отчёт ссылается на другую версию сдачи")
    if report.config_hash != stable_hash(ctx.config_payload()):
        raise AnalysisFailure("Отчёт использует другую конфигурацию")
    for identifier in (report.report_id, report.analyzer_id, report.analyzer_version):
        if not isinstance(identifier, str) or not identifier.strip():
            raise AnalysisFailure("Идентификаторы отчёта и анализатора обязательны")
    if type(report.review_required) is not bool:
        raise AnalysisFailure("review_required должен быть bool")
    try:
        created = datetime.fromisoformat(report.created_at)
    except (ValueError, TypeError) as exc:
        raise AnalysisFailure("Время отчёта имеет неверный формат") from exc
    if created.tzinfo is None or created.utcoffset() is None:
        raise AnalysisFailure("Часовой пояс времени отчёта обязателен")
    if not isinstance(report.findings, tuple):
        raise AnalysisFailure("findings должен быть неизменяемым tuple")
    for finding in report.findings:
        if not isinstance(finding, Finding):
            raise AnalysisFailure("Ожидается Finding")
        if not isinstance(finding.kind, str) or finding.kind not in {
            "similarity", "rubric_feedback", "processing_error"
        }:
            raise AnalysisFailure("Неизвестный kind")
        score = finding.score
        if score is not None:
            if type(score) not in (int, float):
                raise AnalysisFailure("score должен быть числом, а не bool или строкой")
            if not 0 <= score <= 1 or not math.isfinite(score):
                raise AnalysisFailure("score должен быть конечным числом 0–1 либо None")
        if not isinstance(finding.explanation, str) or not finding.explanation.strip():
            raise AnalysisFailure("Объяснение наблюдения обязательно")
        if not isinstance(finding.evidence, tuple):
            raise AnalysisFailure("evidence должен быть неизменяемым tuple")
        for evidence in finding.evidence:
            if not isinstance(evidence, Evidence):
                raise AnalysisFailure("Ожидается Evidence")
            if (
                evidence.submission_id != value.submission_id
                or not isinstance(evidence.field, str)
                or evidence.field not in {"text", "code"}
            ):
                raise AnalysisFailure("Evidence ссылается на неизвестный объект или поле")
            lines = getattr(value, evidence.field).splitlines()
            if type(evidence.start_line) is not int or type(evidence.end_line) is not int:
                raise AnalysisFailure("Номер строки должен быть int")
            if not 1 <= evidence.start_line <= evidence.end_line <= len(lines):
                raise AnalysisFailure("Evidence выходит за границы исходного текста")


def make_report(value: Submission, ctx: AnalysisContext, analyzer_id: str, version: str,
                findings: tuple[Finding, ...], *, review_required: bool = False) -> AnalysisReport:
    """Создать и проверить отчёт по сдаче value и настройкам ctx.

    Вход: value (Submission) — сдача; ctx (AnalysisContext) — настройки;
    analyzer_id и version (str) — имя и версия анализатора;
    findings (tuple[Finding, ...]) — наблюдения; review_required (bool) — флаг проверки.
    Выход: AnalysisReport — созданный и проверенный отчёт. ID и время
    UTC создаются автоматически. Неверные данные вызывают AnalysisFailure.
    """
    report = AnalysisReport(
        report_id=str(uuid4()), submission_id=value.submission_id,
        submission_hash=submission_hash(value), analyzer_id=analyzer_id,
        analyzer_version=version, config_hash=stable_hash(ctx.config_payload()),
        findings=findings, created_at=datetime.now(timezone.utc).isoformat(),
        review_required=review_required,
    )
    validate_report(report, value, ctx)
    return report
