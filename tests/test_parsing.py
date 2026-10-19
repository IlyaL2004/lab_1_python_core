"""Проверки схемы сдачи, ошибок JSON и неизменности данных."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, asdict
import json

import pytest

from school_lab.errors import SubmissionError
from school_lab.models import AIDeclaration, Submission
from school_lab.parsing import decode_json, parse_submission


def test_valid_submission(raw_submission: dict) -> None:
    """Корректный JSON превращается в модели с исходными значениями."""
    line = json.dumps(raw_submission, ensure_ascii=False)
    submission = parse_submission(decode_json(line))

    assert isinstance(submission, Submission)
    assert isinstance(submission.ai_declaration, AIDeclaration)
    assert asdict(submission) == raw_submission


@pytest.mark.parametrize("empty_field", ["text", "code"])
def test_one_content_field_can_be_empty(raw_submission: dict, empty_field: str) -> None:
    """Достаточно непустого текста или непустого кода."""
    raw_submission[empty_field] = ""

    submission = parse_submission(raw_submission)

    assert asdict(submission) == raw_submission


def test_empty_content_is_rejected(raw_submission: dict) -> None:
    """Пробелы и переводы строк не считаются содержимым сдачи."""
    raw_submission["text"] = " \n"
    raw_submission["code"] = "\t "

    with pytest.raises(SubmissionError) as caught:
        parse_submission(raw_submission)

    assert caught.value.code == "schema_error"
    assert caught.value.field == "text/code"


@pytest.mark.parametrize("raw", [None, [], "submission", {1: "value"}])
def test_submission_must_be_object(raw: object) -> None:
    """Вход должен быть словарём со строковыми ключами."""
    with pytest.raises(SubmissionError) as caught:
        parse_submission(raw)

    assert caught.value.code == "schema_error"
    assert caught.value.field == "submission"


@pytest.mark.parametrize(
    "field",
    [
        "schema_version", "submission_id", "student_id", "assignment_id",
        "submitted_at", "text", "code", "template_id", "ai_declaration",
    ],
)
def test_missing_field(raw_submission: dict, field: str) -> None:
    """Каждое поле схемы обязательно, даже если его значение может быть None."""
    del raw_submission[field]

    with pytest.raises(SubmissionError) as caught:
        parse_submission(raw_submission)

    assert caught.value.code == "schema_error"
    assert caught.value.field == field


def test_unknown_field(raw_submission: dict) -> None:
    """Лишнее поле отклоняется с указанием его имени."""
    raw_submission["extra"] = "value"

    with pytest.raises(SubmissionError) as caught:
        parse_submission(raw_submission)

    assert caught.value.code == "schema_error"
    assert caught.value.field == "extra"


@pytest.mark.parametrize(
    "field, value, code",
    [
        ("schema_version", True, "schema_error"),
        ("schema_version", "1", "schema_error"),
        ("schema_version", 1.0, "schema_error"),
        ("schema_version", 2, "unsupported_version"),
        ("submission_id", "", "schema_error"),
        ("student_id", " \t", "schema_error"),
        ("assignment_id", 1, "schema_error"),
        ("submitted_at", "2026-10-19T12:30:00", "schema_error"),
        ("submitted_at", "19 октября", "schema_error"),
        ("submitted_at", 123, "schema_error"),
        ("text", None, "schema_error"),
        ("code", [], "schema_error"),
        ("template_id", "   ", "schema_error"),
        ("template_id", False, "schema_error"),
        ("ai_declaration", None, "schema_error"),
    ],
)
def test_invalid_field(raw_submission: dict, field: str, value: object, code: str) -> None:
    """Неверные значения отклоняются без преобразования типов."""
    raw_submission[field] = value

    with pytest.raises(SubmissionError) as caught:
        parse_submission(raw_submission)

    assert caught.value.code == code
    assert caught.value.field == field


@pytest.mark.parametrize("field", ["used", "tool", "purpose", "verified_by_student"])
def test_missing_declaration_field(raw_submission: dict, field: str) -> None:
    """Вложенная декларация тоже требует полного набора полей."""
    del raw_submission["ai_declaration"][field]

    with pytest.raises(SubmissionError) as caught:
        parse_submission(raw_submission)

    assert caught.value.code == "schema_error"
    assert caught.value.field == "ai_declaration." + field


def test_unknown_declaration_field(raw_submission: dict) -> None:
    """Имя лишнего вложенного поля содержит путь к декларации."""
    raw_submission["ai_declaration"]["extra"] = True

    with pytest.raises(SubmissionError) as caught:
        parse_submission(raw_submission)

    assert caught.value.code == "schema_error"
    assert caught.value.field == "ai_declaration.extra"


@pytest.mark.parametrize(
    "field, value",
    [
        ("used", "false"),
        ("used", 0),
        ("verified_by_student", "true"),
        ("verified_by_student", 1),
        ("tool", " "),
        ("tool", []),
        ("purpose", ""),
        ("purpose", False),
    ],
)
def test_invalid_declaration_value(raw_submission: dict, field: str, value: object) -> None:
    """Флаги требуют bool, а указанные инструмент и цель — непустые строки."""
    raw_submission["ai_declaration"][field] = value

    with pytest.raises(SubmissionError) as caught:
        parse_submission(raw_submission)

    assert caught.value.code == "schema_error"
    assert caught.value.field == "ai_declaration." + field


@pytest.mark.parametrize("missing_value", ["tool", "purpose"])
def test_used_ai_requires_tool_and_purpose(raw_submission: dict, missing_value: str) -> None:
    """При used=True инструмент и цель не могут быть None."""
    raw_submission["ai_declaration"].update(
        used=True, tool="Учебный помощник", purpose="Разбор примера",
    )
    raw_submission["ai_declaration"][missing_value] = None

    with pytest.raises(SubmissionError) as caught:
        parse_submission(raw_submission)

    assert caught.value.code == "schema_error"
    assert caught.value.field == "ai_declaration"


def test_preserves_original_values(raw_submission: dict) -> None:
    """Проверка не обрезает строки и не меняет запись времени."""
    raw_submission["text"] = "  Объяснение\r\n\r\n"
    raw_submission["code"] = "\n    print(2 + 3)  \n"
    raw_submission["submitted_at"] = "2026-10-19T09:30:00.000Z"
    raw_submission["template_id"] = " template-1 "
    raw_submission["ai_declaration"].update(
        used=True, tool=" Учебный помощник ", purpose=" Разбор примера\n",
    )
    original = deepcopy(raw_submission)

    submission = parse_submission(raw_submission)

    assert raw_submission == original
    assert asdict(submission) == original


def test_invalid_input_remains_unchanged(raw_submission: dict) -> None:
    """Ошибка проверки не изменяет ни словарь, ни вложенную декларацию."""
    raw_submission["submitted_at"] = "без даты"
    original = deepcopy(raw_submission)

    with pytest.raises(SubmissionError):
        parse_submission(raw_submission)

    assert raw_submission == original


def test_model_is_independent_of_input(raw_submission: dict) -> None:
    """Изменение исходного словаря после разбора не меняет модель."""
    original = deepcopy(raw_submission)
    submission = parse_submission(raw_submission)

    raw_submission["text"] = "Другой текст"
    raw_submission["ai_declaration"]["used"] = True

    assert asdict(submission) == original


def test_models_are_frozen(raw_submission: dict) -> None:
    """Поля сдачи и её декларации нельзя переназначить."""
    submission = parse_submission(raw_submission)

    with pytest.raises(FrozenInstanceError):
        submission.text = "Изменённый текст"

    with pytest.raises(FrozenInstanceError):
        submission.ai_declaration.used = True


@pytest.mark.parametrize(
    "line, field",
    [
        ('{"broken":', "json"),
        ('{"student_id": "a", "student_id": "b"}', "student_id"),
        ('{"ai_declaration": {"used": true, "used": false}}', "used"),
        ('{"value": NaN}', "json"),
        ('{"value": Infinity}', "json"),
        ('{"value": -Infinity}', "json"),
    ],
)
def test_invalid_json(line: str, field: str) -> None:
    """Синтаксические ошибки, повторы ключей и нестандартные числа запрещены."""
    with pytest.raises(SubmissionError) as caught:
        decode_json(line)

    assert caught.value.code == "invalid_json"
    assert caught.value.field == field


def test_error_to_dict() -> None:
    """Ошибка предоставляет код, поле и сообщение для последующей записи в JSON."""
    error = SubmissionError("schema_error", "student_id", "Пустой идентификатор")

    assert error.to_dict() == {
        "code": "schema_error",
        "field": "student_id",
        "message": "Пустой идентификатор",
    }
    assert str(error) == "Пустой идентификатор"
