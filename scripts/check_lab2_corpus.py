"""Проверить фиксированный корпус ЛР2 и вывести сводку в JSON."""

from argparse import ArgumentParser
import json
from pathlib import Path
import sys

from school_lab.errors import SubmissionError
from school_lab.evaluation import validate_pairs
from school_lab.parsing import decode_json, load_assignments
from school_lab.pipeline import file_sha256, iter_submissions


def check_corpus(directory: Path) -> dict:
    """Вход: каталог корпуса. Выход: количества, разбиение и SHA-256 файлов.

    Для проверки связей документы сохраняются в списке; память растёт с корпусом.
    Ошибки чтения и проверки передаются вызывающему коду.
    """
    assignments = load_assignments(directory / "assignments.json")
    submissions = list(iter_submissions(directory / "submissions.jsonl"))
    for submission in submissions:
        if submission.assignment_id not in assignments:
            raise ValueError(f"Неизвестное задание: {submission.assignment_id}")
    provenance = decode_json((directory / "provenance.json").read_text(encoding="utf-8"))
    split = decode_json((directory / "split.json").read_text(encoding="utf-8"))
    with (directory / "pairs.jsonl").open(encoding="utf-8") as stream:
        rows = [decode_json(line) for line in stream if line.strip()]
    pairs = validate_pairs(rows, submissions, provenance, split)
    parts = {}
    for part, identifiers in split.items():
        part_pairs = [pair for pair in pairs if pair.split == part]
        parts[part] = {
            "submissions": len(identifiers),
            "families": len({provenance[identifier]["family_id"] for identifier in identifiers}),
            "pairs": len(part_pairs),
            "template_only": sum(pair.template_only for pair in part_pairs),
        }
    filenames = ("assignments.json", "submissions.jsonl", "provenance.json", "split.json", "pairs.jsonl")
    return {
        "status": "ok",
        "submissions": len(submissions),
        "families": len({meta["family_id"] for meta in provenance.values()}),
        "pairs": len(pairs),
        "template_only": sum(pair.template_only for pair in pairs),
        "split": parts,
        "input_sha256": {name: file_sha256(directory / name) for name in filenames},
    }


def main(argv: list[str] | None = None) -> int:
    """Вход: аргументы CLI либо None для sys.argv. Выход: 0 при успехе, 2 при ошибке."""
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path,
                        default=Path(__file__).resolve().parents[1] / "data" / "lab2")
    args = parser.parse_args(argv)
    try:
        result = check_corpus(args.data)
    except (OSError, UnicodeError, SubmissionError, ValueError) as exc:
        print(json.dumps({"code": "corpus_error", "message": str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
