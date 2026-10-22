"""Импорт и анализ сдач: JSON в stdout, ошибки запуска в stderr."""
import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .application import Application, build_application
from .errors import AnalysisFailure, SubmissionError
from .models import Assignment
from .parsing import decode_json, require_fields, require_object, require_string
from .reports import AnalysisContext
from .sources import ImportSummary, import_file


def load_assignments(path: Path) -> dict[str, Assignment]:
    """Прочитать задания и проверить их поля.

    Вход: path — путь к UTF-8 JSON со списком заданий.
    Выход: словарь Assignment по ID; ошибки схемы вызывают SubmissionError.
    Ошибки чтения файла и кодировки передаются вызывающему коду.
    """
    raw = decode_json(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise SubmissionError("schema_error", "assignments", "Ожидается список заданий")
    result = {}
    for item in raw:
        data = require_object(item, "assignment")
        require_fields(data, frozenset({"assignment_id", "allowed_template", "requirements"}))
        name = require_string(data["assignment_id"], "assignment_id")
        template = require_string(data["allowed_template"], "allowed_template", nonempty=False)
        requirements = data["requirements"]
        if not isinstance(requirements, list):
            raise SubmissionError("schema_error", "requirements", "Ожидается список строк")
        if name in result:
            raise SubmissionError("conflict", "assignment_id", "Задание повторяется")
        checked_requirements = []
        for requirement in requirements:
            checked_requirements.append(require_string(requirement, "requirements"))
        result[name] = Assignment(name, template, tuple(checked_requirements))
    return result


def create_parser() -> argparse.ArgumentParser:
    """Описать аргументы команд import и analyze.

    Вход: без аргументов.
    Выход: ArgumentParser; обработка файлов ещё не запускается.
    """
    parser = argparse.ArgumentParser(prog="school-lab", description="Приём сдач и анализ")
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser("import", help="Импорт файла сдач")
    importer.add_argument("path", type=Path)
    analyze = commands.add_parser("analyze", help="Импорт и анализ в одном процессе")
    analyze.add_argument("path", type=Path)
    analyze.add_argument("--assignments", type=Path, required=True)
    analyze.add_argument("--analyzer", action="append", dest="analyzers")
    analyze.add_argument("--config-version", default="1")
    return parser


def analyze_accepted(
    app: Application,
    summary: ImportSummary,
    assignments: dict[str, Assignment],
    analyzer_names: list[str],
    config_version: str,
) -> dict[str, object]:
    """Запустить выбранные анализаторы для принятых версий сдач.

    Вход: app — приложение; summary — итоги импорта; assignments — задания по ID;
    analyzer_names — имена анализаторов; config_version — версия настроек.
    Выход: словарь со списками reports и analysis_errors для вывода в JSON.
    Ошибка одного анализатора не мешает остальным; дубликаты повторно не анализируются.
    """
    reports = []
    errors = []
    for submission_id in summary.accepted:
        value = app.repository.get(submission_id)
        if value is None:
            raise AnalysisFailure("Принятая сдача отсутствует в репозитории")
        assignment = assignments.get(value.assignment_id)
        if assignment is None:
            errors.append({
                "submission_id": submission_id,
                "code": "analysis_error",
                "message": "Задание не найдено",
            })
            continue
        context = AnalysisContext(assignment, config_version=config_version)
        for name in analyzer_names:
            try:
                report = app.analysis.run(submission_id, name, context)
                reports.append(asdict(report))
            except AnalysisFailure as error:
                errors.append({
                    "submission_id": submission_id,
                    "analyzer_id": name,
                    "code": "analysis_error",
                    "message": str(error),
                })
    return {"reports": reports, "analysis_errors": errors}


def main(argv: list[str] | None = None) -> int:
    """Импортировать сдачи и при необходимости выполнить анализ.

    Вход: argv — список аргументов; при None используется командная строка.
    Выход: 0 при успехе, 1 при ошибках записей/анализа, 2 при ошибке источника/настроек.
    Итоги печатаются в stdout, ошибки запуска — в stderr.
    Неверные аргументы argparse завершают запуск через SystemExit(2).
    """
    args = create_parser().parse_args(argv)
    try:
        app = build_application()
        assignments = {}
        if args.command == "analyze":
            require_string(args.config_version, "config_version")
            assignments = load_assignments(args.assignments)
        summary = import_file(args.path, app.submissions)
        output = summary.to_dict()
        if args.command == "analyze":
            analysis = analyze_accepted(
                app, summary, assignments,
                args.analyzers or ["length", "template"], args.config_version,
            )
            output.update(analysis)
        print(json.dumps(output, ensure_ascii=False, allow_nan=False))
        return 1 if summary.errors or output.get("analysis_errors") else 0
    except (OSError, UnicodeError, SubmissionError, AnalysisFailure) as error:
        code = "source_unavailable" if isinstance(error, (OSError, UnicodeError)) else "configuration_error"
        print(json.dumps({"code": code, "message": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
