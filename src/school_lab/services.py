"""Приём проверенной сдачи с обработкой повторов и конфликтов ID."""
from dataclasses import dataclass
from typing import Literal

from .errors import SubmissionError
from .hashing import submission_hash
from .parsing import parse_submission
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
