"""Источники сдач из JSONL и памяти с общим порядком импорта."""
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

from .errors import SubmissionError
from .parsing import decode_json
from .services import SubmissionService


@dataclass(frozen=True, slots=True)
class SourceRecord:
    """Запись источника с данными либо ошибкой разбора JSON.

    Вход: line_no — номер с 1; raw — данные; error — SubmissionError либо None.
    Выход: неизменяемая запись; при error=None данные передаются parser.
    """

    line_no: int
    raw: object = None
    error: SubmissionError | None = None


class SubmissionSource(Protocol):
    """Контракт источника: метод open выдаёт записи внутри блока with."""

    def open(self) -> AbstractContextManager[Iterator[SourceRecord]]:
        """Предоставить итератор записей и управлять ресурсами источника.

        Вход: без аргументов; используется состояние источника.
        Выход: контекстный менеджер с итератором SourceRecord внутри with.
        """
        ...


class InMemorySource:
    """Повторно читать список записей, защищая его от внешних изменений."""

    def __init__(self, values: list[dict[str, object]]) -> None:
        """Сохранить независимую копию списка и вложенных данных.

        Вход: values — список словарей сдач; проверку схемы выполняет сервис.
        Выход: None; источник хранит копию исходных данных.
        """
        self._values = deepcopy(values)

    @contextmanager
    def open(self) -> Iterator[Iterator[SourceRecord]]:
        """Начать новый проход по записям в исходном порядке.

        Вход: без аргументов; используется сохранённый список.
        Выход: контекстный менеджер с итератором независимых копий записей.
        """
        def records() -> Iterator[SourceRecord]:
            """Вход: сохранённые данные. Выход: записи с номерами от 1."""
            for number, raw in enumerate(self._values, start=1):
                yield SourceRecord(number, raw=deepcopy(raw))

        yield records()


class JSONLSource:
    """Читать JSONL построчно и закрывать файл при выходе из with."""

    def __init__(self, path: Path) -> None:
        """Сохранить путь, не открывая файл.

        Вход: path — путь к JSONL в кодировке UTF-8.
        Выход: None; источник готов к открытию.
        """
        self.path = path

    @contextmanager
    def open(self) -> Iterator[Iterator[SourceRecord]]:
        """Открыть файл с начала и выдать итератор его строк.

        Вход: без аргументов; используется сохранённый путь.
        Выход: контекстный менеджер с итератором SourceRecord.
        Ошибки JSON сохраняются в записи; ошибки файла и UTF-8 передаются выше.
        Файл закрывается и при полном чтении, и при break или исключении.
        """
        with self.path.open("r", encoding="utf-8") as handle:
            def records() -> Iterator[SourceRecord]:
                """Вход: открытый файл. Выход: данные или ошибка каждой строки."""
                for number, line in enumerate(handle, start=1):
                    try:
                        raw = decode_json(line)
                    except SubmissionError as error:
                        yield SourceRecord(number, error=error)
                    else:
                        yield SourceRecord(number, raw=raw)

            yield records()


@dataclass
class ImportSummary:
    """Итоги импорта; списки создаются отдельно для каждого экземпляра.

    Вход: accepted и duplicate — списки ID; errors — ошибки с номерами строк.
    Выход: объект итогов импорта; по умолчанию все списки пусты.
    """

    accepted: list[str] = field(default_factory=list)
    duplicate: list[str] = field(default_factory=list)
    errors: list[dict[str, object]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """Подготовить результат для JSON.

        Вход: данные текущего экземпляра.
        Выход: новый словарь с ключами accepted, duplicate и errors.
        """
        return asdict(self)


def import_source(source: SubmissionSource, service: SubmissionService) -> ImportSummary:
    """Передать записи любого источника сервису приёма.

    Вход: source — источник с методом open; service — сервис с репозиторием.
    Выход: ImportSummary с принятыми ID, повторами и ошибками отдельных строк.
    Ошибки записей не прерывают импорт. Остальные исключения передаются выше;
    блок with освобождает ресурсы источника при любом выходе.
    """
    summary = ImportSummary()
    with source.open() as records:
        for record in records:
            if record.error is not None:
                summary.errors.append({"line_no": record.line_no, **record.error.to_dict()})
                continue
            try:
                result = service.accept(record.raw)
            except SubmissionError as error:
                summary.errors.append({"line_no": record.line_no, **error.to_dict()})
            else:
                if result.status == "accepted":
                    summary.accepted.append(result.submission_id)
                else:
                    summary.duplicate.append(result.submission_id)
    return summary


def import_file(path: Path, service: SubmissionService) -> ImportSummary:
    """Импортировать файл через общий механизм источников.

    Вход: path — путь к UTF-8 JSONL; service — сервис с репозиторием.
    Выход: ImportSummary; ошибки файла и кодировки передаются вызывающему коду.
    Сохраняет интерфейс файлового импорта предыдущего этапа.
    """
    return import_source(JSONLSource(path), service)
