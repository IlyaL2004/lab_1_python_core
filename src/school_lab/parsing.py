"""Разбор JSON и строгая проверка полей сдачи без сторонних библиотек."""
from datetime import datetime
from collections.abc import Mapping
import json
from .models import AIDeclaration, Submission
from .errors import SubmissionError

SUBMISSION_FIELDS = frozenset({
    "schema_version",
    "submission_id",
    "student_id",
    "assignment_id",
    "submitted_at",
    "text",
    "code",
    "template_id",
    "ai_declaration",
})
DECLARATION_FIELDS = frozenset({
    "used",
    "tool",
    "purpose",
    "verified_by_student",
})


def require_object(raw: object, name: str) -> dict[str, object]:
    """Вернуть словарь со строковыми ключами или вызвать SubmissionError.

    Вход: raw (object) — проверяемые данные; name (str) — имя поля для ошибки.
    Выход: dict[str, object] — исходный словарь после проверки ключей.
    """
    if not isinstance(raw, dict):
        raise SubmissionError("schema_error", name, "Ожидается JSON-объект со строковыми ключами")
    for key in raw:
        if not isinstance(key, str):
            raise SubmissionError("schema_error", name, "Ожидается JSON-объект со строковыми ключами")
    return raw


def require_fields(
    raw: Mapping[str, object], expected: frozenset[str], prefix: str = ""
) -> None:
    """Проверить точное совпадение полей словаря с ожидаемым набором.

    Вход: raw (Mapping[str, object]) — данные; expected (frozenset[str]) — поля;
    prefix (str) — префикс имени поля в ошибке, например "ai_declaration.".
    Выход: None, если набор полей совпадает.
    При пропущенном или лишнем поле вызывается SubmissionError.
    """
    missing = expected - raw.keys()
    extra = raw.keys() - expected
    if missing:
        name = sorted(missing)[0]
        raise SubmissionError("schema_error", prefix + name, "Обязательное поле отсутствует")
    if extra:
        name = sorted(extra)[0]
        raise SubmissionError("schema_error", prefix + name, "Неизвестное поле не допускается")


def require_string(value: object, name: str, *, nonempty: bool = True) -> str:
    """Проверить строку; nonempty=False разрешает пустое содержимое.

    Вход: value (object) — значение; name (str) — имя поля;
    nonempty (bool) — нужно ли требовать непустую строку.
    Выход: str — исходная строка без преобразования и обрезки пробелов.
    Ошибка типа или обязательной непустоты вызывает SubmissionError.
    """
    if not isinstance(value, str):
        raise SubmissionError("schema_error", name, "Ожидается строка без преобразования типа")
    if nonempty and not value.strip():
        raise SubmissionError("schema_error", name, "Ожидается непустая строка")
    return value


def optional_string(value: object, name: str) -> str | None:
    """Вернуть None или проверенную непустую строку.

    Вход: value (object) — значение; name (str) — имя поля для ошибки.
    Выход: str | None — исходная непустая строка или None.
    """
    if value is None:
        return None
    return require_string(value, name)


def require_bool(value: object, name: str) -> bool:
    """Вернуть bool; строки и числа отклонить с SubmissionError.

    Вход: value (object) — значение; name (str) — имя поля для ошибки.
    Выход: bool — исходное логическое значение.
    """
    if type(value) is not bool:
        raise SubmissionError("schema_error", name, "Ожидается bool, а не строка или число")
    return value


def validate_timestamp(value: object) -> str:
    """Проверить дату ISO с часовым поясом и вернуть исходную строку.

    Вход: value (object) — проверяемая строка даты и времени.
    Выход: str — исходная запись ISO с часовым поясом.
    Неверный формат и отсутствие часового пояса вызывают SubmissionError.
    """
    text = require_string(value, "submitted_at")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise SubmissionError(
            "schema_error", "submitted_at", "Ожидается дата и время ISO с часовым поясом"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SubmissionError("schema_error", "submitted_at", "Часовой пояс обязателен")
    return text


def parse_submission(raw: object) -> Submission:
    """Преобразовать декодированный JSON-объект в проверенную сдачу.

    Проверяет версию схемы, поля, типы, время, содержимое и декларацию ИИ.
    Вход: raw (object) — декодированный JSON-объект сдачи.
    Выход: Submission — сдача, прошедшая проверку полей.
    Нарушение контракта вызывает SubmissionError
    с кодом ошибки и именем проблемного поля. Типы не преобразуются.
    """
    data = require_object(raw, "submission")
    require_fields(data, SUBMISSION_FIELDS)
    version = data["schema_version"]
    if type(version) is not int:
        raise SubmissionError("schema_error", "schema_version", "Ожидается целое число; bool не допускается")
    if version != 1:
        raise SubmissionError("unsupported_version", "schema_version", "Поддерживается версия схемы 1")

    declaration_data = require_object(data["ai_declaration"], "ai_declaration")
    require_fields(declaration_data, DECLARATION_FIELDS, "ai_declaration.")
    declaration = AIDeclaration(
        used=require_bool(declaration_data["used"], "ai_declaration.used"),
        tool=optional_string(declaration_data["tool"], "ai_declaration.tool"),
        purpose=optional_string(declaration_data["purpose"], "ai_declaration.purpose"),
        verified_by_student=require_bool(
            declaration_data["verified_by_student"], "ai_declaration.verified_by_student"
        ),
    )
    if declaration.used and (declaration.tool is None or declaration.purpose is None):
        raise SubmissionError("schema_error", "ai_declaration", "Инструмент и цель применения ИИ обязательны")

    text = require_string(data["text"], "text", nonempty=False)
    source_code = require_string(data["code"], "code", nonempty=False)
    if not text.strip() and not source_code.strip():
        raise SubmissionError("schema_error", "text/code", "Текст или код должны быть непустыми")
    return Submission(
        schema_version=version,
        submission_id=require_string(data["submission_id"], "submission_id"),
        student_id=require_string(data["student_id"], "student_id"),
        assignment_id=require_string(data["assignment_id"], "assignment_id"),
        submitted_at=validate_timestamp(data["submitted_at"]),
        text=text,
        code=source_code,
        template_id=optional_string(data["template_id"], "template_id"),
        ai_declaration=declaration,
    )


def reject_repeated_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Собрать JSON-объект из пар, отклоняя повтор ключа с SubmissionError.

    Вход: pairs (list[tuple[str, object]]) — пары ключей и значений JSON.
    Выход: dict[str, object] — словарь с уникальными ключами.
    """
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SubmissionError("invalid_json", key, "Повтор ключа в JSON-объекте не допускается")
        result[key] = value
    return result


def reject_constant(value: str) -> object:
    """Отклонить NaN и Infinity, которые json.loads разрешает по умолчанию.

    Вход: value (str) — запись NaN, Infinity или -Infinity из JSON.
    Выход: значение не возвращается; всегда вызывается SubmissionError.
    """
    raise SubmissionError("invalid_json", "json", f"Нестандартное значение не допускается: {value}")


def decode_json(line: str) -> object:
    """Разобрать строку JSON, запрещая повтор ключей и NaN/Infinity.

    Вход: line (str) — текст JSON.
    Выход: object — декодированное значение JSON; схему проверяет parse_submission.
    Синтаксические ошибки JSON вызывают SubmissionError с кодом invalid_json.
    """
    try:
        return json.loads(
            line,
            object_pairs_hook=reject_repeated_keys,
            parse_constant=reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise SubmissionError(
            "invalid_json", "json", f"Ошибка JSON около столбца {exc.colno}"
        ) from exc
