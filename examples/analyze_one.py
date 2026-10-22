"""Приём одной учебной сдачи и запуск двух анализаторов."""
import json
from dataclasses import asdict
from pathlib import Path

from school_lab.application import build_application
from school_lab.cli import load_assignments
from school_lab.parsing import decode_json
from school_lab.reports import AnalysisContext


def main() -> None:
    """Принять пример и вывести отчёты о длине и разрешённом шаблоне.

    Вход: data/submission.json и data/assignments.json из проекта.
    Выход: None; список двух отчётов выводится в JSON в терминал.
    """
    data = Path(__file__).resolve().parents[1] / "data"
    raw = decode_json((data / "submission.json").read_text(encoding="utf-8"))
    assignments = load_assignments(data / "assignments.json")
    app = build_application()
    accepted = app.submissions.accept(raw)
    context = AnalysisContext(assignments[raw["assignment_id"]])
    reports = []
    for name in ("length", "template"):
        report = app.analysis.run(accepted.submission_id, name, context)
        reports.append(asdict(report))
    print(json.dumps(reports, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
