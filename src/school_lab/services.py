"""Приём сдач, регистрация анализаторов и запуск проверки."""
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from .errors import AnalysisFailure, SubmissionError
from .hashing import submission_hash
from .models import AnalysisReport
from .parsing import parse_submission
from .reports import AnalysisContext, Analyzer, validate_report
from .repositories import SubmissionRepository


@dataclass(frozen=True, slots=True)
class Acceptance:
    """Результат приёма одной записи.

    Вход: status — accepted или duplicate; submission_id и content_hash — строки.
    Выход: неизменяемый объект с результатом приёма.
    """

    status: Literal["accepted", "duplicate"]
    submission_id: str
    content_hash: str


class SubmissionService:
    """Проверяет входные данные и сохраняет версии в переданном репозитории."""

    def __init__(self, repository: SubmissionRepository) -> None:
        """Подключить хранилище.

        Вход: repository — объект с методами get и add.
        Выход: None; сервис готов к приёму записей.
        """
        self.repository = repository

    def accept(self, raw: object) -> Acceptance:
        """Принять новую сдачу или распознать повтор уже принятой.

        Вход: raw — внешний объект, ожидается словарь по схеме Submission.
        Выход: Acceptance со статусом accepted или duplicate, ID и хешем.
        Неверная схема и другой хеш занятого ID вызывают SubmissionError.
        Вход и ранее сохранённая сдача не изменяются.
        """
        submission = parse_submission(raw)
        digest = submission_hash(submission)
        previous = self.repository.get(submission.submission_id)

        if previous is not None:
            if submission_hash(previous) != digest:
                raise SubmissionError(
                    "conflict", "submission_id", "Для новой версии требуется новый ID"
                )
            return Acceptance("duplicate", submission.submission_id, digest)

        self.repository.add(submission)
        return Acceptance("accepted", submission.submission_id, digest)


class AnalyzerRegistry:
    """Хранит функции создания анализаторов под уникальными именами.

    Вход: конструктор без аргументов.
    Выход: пустой реестр с методами register и create.
    """

    def __init__(self) -> None:
        """Создать пустой реестр анализаторов.

        Вход: без аргументов.
        Выход: None; объект реестра инициализирован.
        """
        self._factories: dict[str, Callable[[], Analyzer]] = {}

    def register(self, name: str, factory: Callable[[], Analyzer]) -> None:
        """Связать имя с функцией, создающей анализатор без аргументов.

        Пустое или занятое имя и невызываемая фабрика вызывают AnalysisFailure.

        Вход: name (str) — имя; factory (Callable[[], Analyzer]) — фабрика.
        Выход: None; фабрика добавлена в реестр под указанным именем.
        """
        if not isinstance(name, str) or not name.strip():
            raise AnalysisFailure("Требуется непустое уникальное имя анализатора")
        if name in self._factories:
            raise AnalysisFailure("Требуется непустое уникальное имя анализатора")
        if not callable(factory):
            raise AnalysisFailure("Фабрика должна быть вызываемым объектом")
        self._factories[name] = factory

    def create(self, name: str) -> Analyzer:
        """Создать анализатор по имени и проверить его основной контракт.

        Неизвестное имя, ошибка фабрики и неверные атрибуты анализатора
        вызывают AnalysisFailure.

        Вход: name (str) — зарегистрированное имя анализатора.
        Выход: Analyzer — новый экземпляр с проверенным интерфейсом.
        """
        if not isinstance(name, str) or not name.strip():
            raise AnalysisFailure("Требуется непустое имя анализатора")
        try:
            factory = self._factories[name]
        except KeyError as exc:
            raise AnalysisFailure(f"Неизвестный анализатор: {name}") from exc
        try:
            analyzer = factory()
            if getattr(analyzer, "analyzer_id", None) != name:
                raise AnalysisFailure("Имя регистрации не совпадает с analyzer_id")
            analyze_method = getattr(analyzer, "analyze", None)
            version = getattr(analyzer, "analyzer_version", None)
            if not callable(analyze_method):
                raise AnalysisFailure("Некорректный контракт анализатора")
            if not isinstance(version, str) or not version.strip():
                raise AnalysisFailure("Некорректный контракт анализатора")
        except AnalysisFailure:
            raise
        except Exception as exc:
            raise AnalysisFailure(f"Ошибка фабрики {name}: {exc}") from exc
        return analyzer


class AnalysisService:
    """Запускает зарегистрированный анализатор для уже принятой сдачи.

    Вход: repository — хранилище сдач; registry — реестр анализаторов.
    Выход: сервис с методом run, возвращающим проверенный отчёт.
    """

    def __init__(self, repository: SubmissionRepository, registry: AnalyzerRegistry) -> None:
        """Подключить хранилище сдач и реестр доступных анализаторов.

        Вход: repository (SubmissionRepository) — хранилище;
        registry (AnalyzerRegistry) — реестр анализаторов.
        Выход: None; объект сервиса инициализирован.
        """
        self.repository = repository
        self.registry = registry

    def run(
        self, submission_id: str, analyzer_name: str, context: AnalysisContext
    ) -> AnalysisReport:
        """Выполнить анализ по ID сдачи, имени анализатора и контексту.

        Вход: submission_id, analyzer_name (str) — ID сдачи и имя анализатора;
        context (AnalysisContext) — задание и настройки анализа.
        Выход: AnalysisReport — проверенный отчёт анализа.
        Отсутствие сдачи, неверный контекст, сбой анализатора или неверный отчёт
        вызывают AnalysisFailure.
        """
        value = self.repository.get(submission_id)
        if value is None:
            raise AnalysisFailure("Анализ возможен после приёма сдачи")
        if value.assignment_id != context.assignment.assignment_id:
            raise AnalysisFailure("Требуется контекст соответствующего задания")
        analyzer = self.registry.create(analyzer_name)
        try:
            report = analyzer.analyze(value, context)
            validate_report(report, value, context)
            if (
                report.analyzer_id != analyzer.analyzer_id
                or report.analyzer_version != analyzer.analyzer_version
            ):
                raise AnalysisFailure("Отчёт не соответствует вызванному анализатору")
        except AnalysisFailure:
            raise
        except Exception as exc:
            raise AnalysisFailure(f"Ошибка анализатора {analyzer_name}: {exc}") from exc
        return report
