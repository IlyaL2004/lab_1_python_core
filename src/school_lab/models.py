"""Неизменяемые модели данных, соответствующие контрактам задания."""
from dataclasses import dataclass


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
