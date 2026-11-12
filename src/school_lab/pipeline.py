"""Ленивый приём JSONL: одна исходная сдача за раз и отдельный индекс хешей."""

from collections.abc import Iterator
from contextlib import closing
from hashlib import sha256
from pathlib import Path
from .errors import SubmissionError
from .features import prepare_submission
from .hashing import submission_hash
from .models import Assignment, Submission
from .parsing import parse_submission
from .sources import JSONLSource
from .similarity_models import PreparedSubmission, SimilarityConfig


def file_sha256(path: Path) -> str:
    """Вычислить отпечаток файла без загрузки всего содержимого в память.

    Вход: path — существующий файл.
    Выход: SHA-256 в виде 64 шестнадцатеричных символов; чтение блоками по 64 КиБ.
    """
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def iter_submissions(
    path: Path, errors: list[dict[str, object]] | None = None,
) -> Iterator[Submission]:
    """Читать, проверять и выдавать новые версии без накопления исходных работ.

    Вход: path — JSONL; errors — необязательный список ошибок с line_no.
    Выход: итератор Submission. Идентичные повторы пропускаются, конфликт — ошибка.
    При errors=None первая ошибка записи передаётся вызывающему коду; иначе
    сохраняется в списке и чтение продолжается. Ошибки самого файла не скрываются.
    При раннем выходе вызывающий код закрывает генератор через contextlib.closing.
    Словарь ID/хешей растёт как O(N), но исходный корпус здесь не сохраняется.
    """
    seen: dict[str, str] = {}
    with JSONLSource(path).open() as records:
        for record in records:
            try:
                if record.error is not None:
                    raise record.error
                submission = parse_submission(record.raw)
                digest = submission_hash(submission)
                previous = seen.get(submission.submission_id)
                if previous is not None:
                    if previous != digest:
                        raise SubmissionError("conflict", "submission_id", "Для новой версии требуется новый ID")
                    continue
                seen[submission.submission_id] = digest
                yield submission
            except SubmissionError as exc:
                if errors is None:
                    raise
                errors.append({"line_no": record.line_no, **exc.to_dict()})


def iter_features(
    path: Path,
    assignments: dict[str, Assignment],
    config: SimilarityConfig,
    errors: list[dict[str, object]] | None = None,
) -> Iterator[PreparedSubmission]:
    """Связать ленивые стадии read → validate → features.

    Вход: JSONL, задания по ID, конфигурация метода и список ошибок приёма.
    Выход: итератор PreparedSubmission, включая явные отказы метода на плохом коде.
    Неизвестное задание вызывает SubmissionError; исходные сдачи не накапливаются.
    При раннем выходе закрыть генератор; закрытие передаётся файловому источнику.
    """
    with closing(iter_submissions(path, errors)) as submissions:
        for submission in submissions:
            assignment = assignments.get(submission.assignment_id)
            if assignment is None:
                raise SubmissionError("schema_error", "assignment_id", "Задание не найдено")
            yield prepare_submission(submission, assignment, config)
