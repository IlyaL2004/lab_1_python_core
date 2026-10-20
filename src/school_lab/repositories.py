"""Контракт хранилища сдач и простая реализация на словаре."""
from typing import Protocol
from .models import Submission
from .errors import SubmissionError


class SubmissionRepository(Protocol):
    """Методы хранилища, которые использует сервис приёма сдач."""

    def get(self, submission_id: str) -> Submission | None:
        """Вернуть сдачу по ID или None, если такой сдачи нет.

        Вход: submission_id (str) — ID искомой сдачи.
        Выход: Submission | None — найденная сдача или отсутствие результата.
        """
        ...

    def add(self, submission: Submission) -> None:
        """Сохранить сдачу; при повторном ID вызвать SubmissionError.

        Вход: submission (Submission) — проверенная сдача для сохранения.
        Выход: None; сдача добавлена в хранилище.
        """
        ...


class InMemorySubmissionRepository:
    """Хранилище сдач в памяти; данные существуют до завершения программы."""

    def __init__(self) -> None:
        """Создать пустой словарь сдач, доступных по submission_id.

        Вход: без аргументов.
        Выход: None; объект хранилища инициализирован.
        """
        self._items: dict[str, Submission] = {}

    def get(self, submission_id: str) -> Submission | None:
        """Вернуть сохранённую сдачу по ID или None.

        Вход: submission_id (str) — ID искомой сдачи.
        Выход: Submission | None — найденная сдача или отсутствие результата.
        """
        return self._items.get(submission_id)

    def add(self, submission: Submission) -> None:
        """Сохранить сдачу без перезаписи уже существующего ID.

        При занятом ID вызывает SubmissionError с кодом conflict.
        Проверку содержимого выполняет сервис до обращения к хранилищу.

        Вход: submission (Submission) — проверенная сдача для сохранения.
        Выход: None; сдача добавлена в хранилище.
        """
        if submission.submission_id in self._items:
            raise SubmissionError("conflict", "submission_id", "Идентификатор уже сохранён")
        self._items[submission.submission_id] = submission

    def __len__(self) -> int:
        """Вернуть количество сохранённых сдач для вызова len(repository).

        Вход: без аргументов; используется текущее хранилище.
        Выход: int — число сохранённых сдач.
        """
        return len(self._items)
