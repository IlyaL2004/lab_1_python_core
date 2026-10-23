"""Создание сервисов с реестром встроенных и внешних анализаторов."""
from dataclasses import dataclass

from .analyzers import (
    DeclarationAnalyzer,
    LengthAnalyzer,
    MissingExplanationAnalyzer,
    TemplateAnalyzer,
)
from .plugins import load_plugins
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


def build_application(*, plugins: bool = False) -> Application:
    """Создать приложение и при необходимости подключить установленные плагины.

    Вход: plugins — загружать ли внешние анализаторы; по умолчанию False.
    Выход: Application с пустым хранилищем, сервисами и реестром анализаторов.
    Четыре встроенных анализатора доступны всегда; ошибки плагинов передаются выше.
    """
    repository = InMemorySubmissionRepository()
    registry = AnalyzerRegistry()
    analyzer_classes = (
        LengthAnalyzer, TemplateAnalyzer, DeclarationAnalyzer, MissingExplanationAnalyzer,
    )
    for analyzer in analyzer_classes:
        registry.register(analyzer.analyzer_id, analyzer)
    if plugins:
        load_plugins(registry)
    submissions = SubmissionService(repository)
    analysis = AnalysisService(repository, registry)
    return Application(repository, submissions, analysis, registry)
