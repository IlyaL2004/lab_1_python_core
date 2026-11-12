"""Token-сравнение через CLI с повторной подготовкой признаков каждой пары."""

import argparse
import json
import math
import sys
from contextlib import closing
from dataclasses import asdict
from itertools import combinations
from pathlib import Path

from .errors import AnalysisFailure, SubmissionError
from .parsing import load_assignments
from .pipeline import file_sha256, iter_submissions
from .similarity import compare_submissions, eligible
from .similarity_models import SimilarityConfig


def add_commands(commands: argparse._SubParsersAction) -> None:
    """Добавить token-команду compare к прежнему интерфейсу school-lab.

    Вход: commands — подкоманды ArgumentParser.
    Выход: None; зарегистрировано сравнение всех пар или top-k для запроса.
    """
    compare = commands.add_parser("compare", help="Сравнить код методом token")
    compare.add_argument("path", type=Path)
    compare.add_argument("--assignments", type=Path, required=True)
    compare.add_argument("--method", choices=("token",), default="token")
    compare.add_argument("--query", help="ID запроса; без него сравниваются все допустимые пары")
    compare.add_argument("--top-k", type=int, default=5)
    compare.add_argument("--threshold", type=float, default=0.5)
    compare.add_argument("--config-version", default="1")
    compare.add_argument("--keep-template", action="store_true")
    compare.add_argument("--output", type=Path)


def compare_corpus(args: argparse.Namespace) -> dict[str, object]:
    """Прочитать проверенные сдачи и сравнить допустимые пары без общего индекса.

    Вход: args — разобранные аргументы compare.
    Выход: JSON-совместимый словарь с парами, отказами, конфигурацией и ошибками.
    JSONL читается построчно; сдачи и отчёты накапливаются в памяти.
    Признаки обеих сдач извлекаются заново для каждой допустимой пары.
    """
    if args.top_k < 1:
        raise ValueError("top-k должен быть положительным")
    if not math.isfinite(args.threshold) or not 0 <= args.threshold <= 1.000001:
        raise ValueError("threshold должен быть конечным числом от 0 до 1.000001")
    config = SimilarityConfig(
        method=args.method, exclude_template=not args.keep_template,
        config_version=args.config_version,
    )
    assignments = load_assignments(args.assignments)
    errors: list[dict[str, object]] = []
    with closing(iter_submissions(args.path, errors)) as rows:
        submissions = list(rows)
    for submission in submissions:
        if submission.assignment_id not in assignments:
            raise SubmissionError("schema_error", "assignment_id", "Задание не найдено")
    if args.query is None:
        candidates = ((left, right) for left, right in combinations(submissions, 2) if eligible(left, right))
    else:
        query = next((item for item in submissions if item.submission_id == args.query), None)
        if query is None:
            raise ValueError("ID запроса отсутствует среди принятых сдач")
        candidates = ((query, item) for item in submissions if eligible(query, item))
    results = [
        compare_submissions(
            left, right, assignments[left.assignment_id], config, threshold=args.threshold,
        )
        for left, right in candidates
    ]
    undefined = [item for item in results if item.score is None]
    if args.query is None:
        selected = results
    else:
        selected = sorted(
            (item for item in results if item.score is not None),
            key=lambda item: (-item.score, item.right_id),
        )[:args.top_k]
    return {
        "schema_version": 1,
        "config": asdict(config),
        "corpus_sha256": file_sha256(args.path),
        "threshold": args.threshold,
        "query": args.query,
        "documents": len(submissions),
        "comparisons": len(results),
        "pairs": [item.to_dict() for item in selected],
        "undefined_pairs": [item.to_dict() for item in undefined],
        "errors": errors,
    }


def run_command(args: argparse.Namespace) -> int:
    """Запустить compare, сохранив машинный JSON и коды завершения.

    Вход: args — аргументы compare.
    Выход: 0 при успехе, 1 при частичном приёме, 2 при ошибке запуска.
    JSON терминала экранирует Unicode; файл результата записывается в UTF-8.
    SyntaxError исходника отражается в парном отчёте как отказ метода.
    """
    try:
        result = compare_corpus(args)
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2) + "\n",
                encoding="utf-8",
            )
        print(json.dumps(result, ensure_ascii=True, allow_nan=False))
        return 1 if result["errors"] else 0
    except (OSError, UnicodeError, SubmissionError, AnalysisFailure, ValueError) as exc:
        code = "source_unavailable" if isinstance(exc, (OSError, UnicodeError)) else "configuration_error"
        print(json.dumps({"code": code, "message": str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 2
