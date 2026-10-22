"""Создание хранилища, сервисов и реестра встроенных анализаторов."""
from dataclasses import dataclass

from .analyzers import (
    DeclarationAnalyzer,
    LengthAnalyzer,
    MissingExplanationAnalyzer,
    TemplateAnalyzer,
)
from .repositories import InMemorySubmissionRepository
from .services import AnalysisService, AnalyzerRegistry, SubmissionService


@dataclass
class Application:
    """Компоненты приложения с общим хранилищем сдач.

    Вход: repository — хранилище; submissions и analysis — сервисы;
    registry — реестр фабрик анализаторов.
    Выход: объект со ссылками на связанные компоненты.
    """

    repository: InMemorySubmissionRepository
    submissions: SubmissionService
    analysis: AnalysisService
    registry: AnalyzerRegistry


def build_application() -> Application:
    """Создать отдельное приложение с четырьмя встроенными анализаторами.

    Вход: без аргументов.
    Выход: Application с пустым хранилищем и готовыми сервисами.
    """
    repository = InMemorySubmissionRepository()
    registry = AnalyzerRegistry()
    analyzer_classes = (
        LengthAnalyzer, TemplateAnalyzer, DeclarationAnalyzer, MissingExplanationAnalyzer,
    )
    for analyzer in analyzer_classes:
        registry.register(analyzer.analyzer_id, analyzer)
    submissions = SubmissionService(repository)
    analysis = AnalysisService(repository, registry)
    return Application(repository, submissions, analysis, registry)
