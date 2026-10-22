"""Неизменяемые модели данных, соответствующие контрактам задания."""
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class AIDeclaration:
    """Сведения об использовании ИИ и проверке результата студентом.

    Если used=True, при разборе сдачи требуются tool и purpose.

    Вход: used и verified_by_student (bool) — флаги использования ИИ и проверки;
    tool и purpose (str | None) — инструмент и цель его применения.
    Выход: экземпляр AIDeclaration с этими сведениями.
    """

    used: bool
    tool: str | None
    purpose: str | None
    verified_by_student: bool


@dataclass(frozen=True, slots=True)
class Submission:
    """Версия сдачи: идентификаторы, содержимое и декларация ИИ.

    Данные проверяет parse_submission; сама модель только хранит значения.
    submitted_at сохраняет исходную запись даты и часового пояса из JSON.

    Вход: schema_version (int); submission_id, student_id, assignment_id,
    submitted_at, text, code (str) — ID, время и содержимое сдачи;
    template_id (str | None) — ID шаблона; ai_declaration (AIDeclaration).
    Выход: экземпляр Submission с переданными данными сдачи.
    """

    schema_version: int
    submission_id: str
    student_id: str
    assignment_id: str
    submitted_at: str  # Сохраняется оригинальное представление из валидного JSON.
    text: str
    code: str
    template_id: str | None
    ai_declaration: AIDeclaration


@dataclass(frozen=True, slots=True)
class Assignment:
    """Задание с разрешённым шаблоном и перечнем требований.

    Вход: assignment_id (str) — ID задания; allowed_template (str) — шаблон;
    requirements (tuple[str, ...]) — требования к работе.
    Выход: экземпляр Assignment с параметрами задания.
    """

    assignment_id: str
    allowed_template: str
    requirements: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Evidence:
    """Ссылка на строки поля text или code; обе границы включены.

    Нумерация строк начинается с 1. submission_id указывает исходную сдачу.

    Вход: submission_id (str) — ID сдачи; field (str) — "text" или "code";
    start_line и end_line (int) — первая и последняя строки фрагмента.
    Выход: экземпляр Evidence со ссылкой на фрагмент.
    """

    submission_id: str
    field: Literal["text", "code"]
    start_line: int
    end_line: int


@dataclass(frozen=True, slots=True)
class Finding:
    """Наблюдение анализатора с пояснением и ссылками на исходные строки.

    kind задаёт тип наблюдения, score содержит оценку либо None.

    Вход: kind (str) — вид наблюдения; score (float | None) — оценка 0–1;
    evidence (tuple[Evidence, ...]) — ссылки; explanation (str) — пояснение.
    Выход: экземпляр Finding с результатом наблюдения.
    """

    kind: Literal["similarity", "rubric_feedback", "processing_error"]
    score: float | None
    evidence: tuple[Evidence, ...]
    explanation: str


@dataclass(frozen=True, slots=True)
class AnalysisReport:
    """Результат анализа с хешами входных данных и версией анализатора.

    findings хранит наблюдения, review_required отмечает необходимость
    проверки преподавателем. Отчёт не является автоматическим вердиктом.

    Вход: report_id, submission_id, submission_hash, analyzer_id,
    analyzer_version, config_hash, created_at (str) — ID, хеши, версия и время;
    findings (tuple[Finding, ...]) и review_required (bool) — результаты и флаг проверки.
    Выход: экземпляр AnalysisReport с данными отчёта.
    """

    report_id: str
    submission_id: str
    submission_hash: str
    analyzer_id: str
    analyzer_version: str
    config_hash: str
    findings: tuple[Finding, ...]
    created_at: str
    review_required: bool
