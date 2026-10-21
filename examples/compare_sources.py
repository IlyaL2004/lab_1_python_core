"""Сравнение импорта из JSONL и памяти через один интерфейс."""
import json
from pathlib import Path

from school_lab.parsing import decode_json
from school_lab.repositories import InMemorySubmissionRepository
from school_lab.services import SubmissionService
from school_lab.sources import InMemorySource, JSONLSource, SubmissionSource, import_source


def show_imports(name: str, source: SubmissionSource) -> None:
    """Показать первый и повторный импорт в один репозиторий.

    Вход: name — подпись; source — источник с методом open.
    Выход: None; оба итога выводятся в терминал в формате JSON.
    """
    service = SubmissionService(InMemorySubmissionRepository())
    first = import_source(source, service)
    repeated = import_source(source, service)
    result = {"source": name, "first": first.to_dict(), "repeated": repeated.to_dict()}
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> None:
    """Прочитать учебные данные и сравнить два способа их передачи.

    Вход: файл data/submissions.jsonl относительно корня проекта.
    Выход: None; результаты импорта из файла и памяти выводятся в терминал.
    """
    path = Path(__file__).resolve().parents[1] / "data" / "submissions.jsonl"
    with path.open("r", encoding="utf-8") as handle:
        values = [decode_json(line) for line in handle]

    show_imports("JSONL", JSONLSource(path))
    show_imports("memory", InMemorySource(values))


if __name__ == "__main__":
    main()
