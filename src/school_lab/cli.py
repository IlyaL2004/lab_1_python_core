"""Команда import: принять JSONL и вывести результат в JSON."""
import argparse
import json
import sys
from pathlib import Path

from .repositories import InMemorySubmissionRepository
from .services import SubmissionService
from .sources import import_file


def main(argv: list[str] | None = None) -> int:
    """Запустить команду импорта в новом хранилище в памяти.

    Вход: argv — список аргументов; при None используется командная строка.
    Выход: 0 при успехе, 1 при ошибках записей, 2 при ошибке файла/кодировки.
    Итоги печатаются в stdout, ошибки файла — в stderr.
    Неверные аргументы argparse завершают запуск через SystemExit(2).
    """
    parser = argparse.ArgumentParser(prog="school-lab", description="Приём сдач из JSONL")
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser("import", help="Импорт файла сдач")
    importer.add_argument("path", type=Path)
    args = parser.parse_args(argv)

    repository = InMemorySubmissionRepository()
    service = SubmissionService(repository)
    try:
        summary = import_file(args.path, service)
    except (OSError, UnicodeError) as error:
        message = {"code": "source_unavailable", "message": str(error)}
        print(json.dumps(message, ensure_ascii=False), file=sys.stderr)
        return 2

    print(json.dumps(summary.to_dict(), ensure_ascii=False))
    return 1 if summary.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
