"""Построчный импорт сдач из файла JSONL."""
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .errors import SubmissionError
from .parsing import decode_json
from .services import SubmissionService


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


def import_file(path: Path, service: SubmissionService) -> ImportSummary:
    """Прочитать файл и передать каждую запись сервису приёма.

    Вход: path — путь к UTF-8 JSONL; service — сервис с репозиторием.
    Выход: ImportSummary с принятыми ID, повторами и ошибками отдельных строк.
    Пустая или повреждённая строка считается ошибкой, чтение продолжается.
    Ошибки доступа к файлу и кодировки передаются вызывающему коду.
    Блок with закрывает файл и при успешном завершении, и при исключении.
    """
    summary = ImportSummary()
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            try:
                raw = decode_json(line)
                result = service.accept(raw)
            except SubmissionError as error:
                summary.errors.append({"line_no": line_no, **error.to_dict()})
            else:
                if result.status == "accepted":
                    summary.accepted.append(result.submission_id)
                else:
                    summary.duplicate.append(result.submission_id)
    return summary
