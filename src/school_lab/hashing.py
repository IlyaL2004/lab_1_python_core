"""Стабильное представление JSON и хеширование данных средствами Python."""
import hashlib
import json
from dataclasses import asdict
from .models import Submission


def canonical_json(value: object) -> str:
    """Вернуть JSON с упорядоченными ключами и без лишних пробелов.

    Вход: value (object) — данные, поддерживаемые json.dumps.
    Выход: str — JSON с одинаковым представлением одинаковых данных.
    Неподдерживаемый тип вызывает
    TypeError, а NaN и бесконечность — ValueError.
    """
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def stable_hash(value: object) -> str:
    """Вернуть SHA-256 канонического JSON в виде шестнадцатеричной строки.

    Вход: value (object) — данные, поддерживаемые json.dumps.
    Выход: str — хеш из 64 шестнадцатеричных символов.
    """
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def submission_hash(value: Submission) -> str:
    """Вычислить SHA-256 всех полей сдачи, включая декларацию ИИ.

    Вход: value (Submission) — сдача для хеширования.
    Выход: str — хеш сдачи из 64 шестнадцатеричных символов.
    """
    return stable_hash(asdict(value))
