"""Неизменяемые настройки, признаки и результаты токенового сравнения."""
from dataclasses import asdict, dataclass
from typing import Literal

from .models import Evidence


@dataclass(frozen=True, slots=True)
class SimilarityConfig:
    """Настройки извлечения признаков и выборки оснований.

    Вход: method — token; n — размер токенового окна;
    exclude_template — исключать шаблон; max_evidence — предел примеров;
    config_version — непустая версия настроек.
    Выход: проверенная неизменяемая конфигурация.
    """

    method: Literal["token"] = "token"
    n: int = 3
    exclude_template: bool = True
    max_evidence: int = 5
    config_version: str = "1"

    def __post_init__(self) -> None:
        """Вход: поля конфигурации. Выход: None; неверные значения — ValueError."""
        if self.method != "token":
            raise ValueError("method должен быть token")
        if type(self.n) is not int or self.n < 1:
            raise ValueError("n должен быть положительным целым числом")
        if type(self.exclude_template) is not bool:
            raise ValueError("exclude_template должен быть bool")
        if type(self.max_evidence) is not int or self.max_evidence < 0:
            raise ValueError("max_evidence должен быть неотрицательным целым числом")
        if not isinstance(self.config_version, str) or not self.config_version.strip():
            raise ValueError("config_version должен быть непустой строкой")

    @property
    def method_version(self) -> str:
        """Вход: выбранный метод. Выход: версия его реализации."""
        return "token-ngram-v2"


@dataclass(frozen=True, slots=True)
class FeatureOccurrence:
    """Одно вхождение признака в исходный код.

    Вход: feature — строковое значение; start_line/end_line — строки с 1.
    Выход: вхождение с включительными границами исходного фрагмента.
    """

    feature: str
    start_line: int
    end_line: int


@dataclass(frozen=True, slots=True)
class PreparedSubmission:
    """Признаки одной версии сдачи и сведения для проверки результата.

    Вход: ID, исходный code, хеши, конфигурация, признаки, их вхождения,
    исключённые диапазоны шаблона и статус извлечения.
    Выход: неизменяемая запись для повторных сравнений без токенизации.
    """

    submission_id: str
    student_id: str
    assignment_id: str
    code: str
    submission_hash: str
    config_hash: str
    config: SimilarityConfig
    features: frozenset[str]
    occurrences: tuple[FeatureOccurrence, ...]
    excluded_template: tuple[Evidence, ...]
    status: str = "ok"
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class PairEvidence:
    """Общий признак и ссылки на обе исходные версии.

    Вход: feature — значение признака; left/right — исходные Evidence.
    Выход: одно объяснимое совпадение, ориентированное по сторонам пары.
    """

    feature: str
    left: Evidence
    right: Evidence


@dataclass(frozen=True, slots=True)
class PairResult:
    """Версионированный результат сравнения двух сдач.

    Вход: ID и хеши версий, метод и настройки, score/status/reason,
    evidence, исключённые диапазоны, порог и сигнал ручного разбора.
    Выход: неизменяемый парный отчёт; score=None означает отказ от оценки.
    """

    left_id: str
    right_id: str
    left_hash: str
    right_hash: str
    method: str
    method_version: str
    config_hash: str
    score: float | None
    status: str
    reason: str | None
    evidence: tuple[PairEvidence, ...]
    excluded_left: tuple[Evidence, ...]
    excluded_right: tuple[Evidence, ...]
    review_required: bool
    threshold: float
    schema_version: int = 1

    def to_dict(self) -> dict[str, object]:
        """Вход: текущий отчёт. Выход: новый JSON-совместимый словарь."""
        result = asdict(self)
        for field in ("evidence", "excluded_left", "excluded_right"):
            result[field] = list(result[field])
        return result
