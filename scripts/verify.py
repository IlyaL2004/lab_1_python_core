"""Сквозной запуск импорта и анализа с сохранением отчётов и измерений."""
import json
import platform
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path

from school_lab.application import build_application
from school_lab.cli import analyze_accepted, load_assignments
from school_lab.hashing import submission_hash
from school_lab.sources import JSONLSource, import_source


def main() -> None:
    """Повторить импорт, запустить пять анализаторов и сохранить результаты.

    Вход: data/submissions.jsonl, data/invalid_submissions.jsonl и data/assignments.json.
    Выход: None; results/reports.json с отчётами, results/verification.json со сводкой;
    та же сводка выводится в stdout. Требуется установленный внешний плагин.
    """
    root = Path(__file__).resolve().parents[1]
    output = root / "results"
    output.mkdir(exist_ok=True)
    source = JSONLSource(root / "data/submissions.jsonl")
    app = build_application(plugins=True)
    first = import_source(source, app.submissions)
    original_hashes = {}
    for submission_id in first.accepted:
        submission = app.repository.get(submission_id)
        if submission is None:
            raise RuntimeError("Принятая сдача отсутствует в репозитории")
        original_hashes[submission_id] = submission_hash(submission)
    repeated = import_source(source, app.submissions)
    assignments = load_assignments(root / "data/assignments.json")
    analyzer_names = ["length", "template", "declaration", "missing-explanation", "external-declaration"]
    analysis = analyze_accepted(app, first, assignments, analyzer_names, "1")
    mixed_app = build_application()
    mixed = import_source(JSONLSource(root / "data/invalid_submissions.jsonl"), mixed_app.submissions)

    if first.errors or repeated.errors or analysis["analysis_errors"]:
        raise RuntimeError("Контрольный импорт или анализ завершился ошибкой")
    if repeated.duplicate != first.accepted or repeated.accepted:
        raise RuntimeError("Повторный импорт не распознал принятые версии")
    for submission_id, digest in original_hashes.items():
        submission = app.repository.get(submission_id)
        if submission is None or submission_hash(submission) != digest:
            raise RuntimeError("Анализ изменил принятую сдачу")
    if len(analysis["reports"]) != len(first.accepted) * len(analyzer_names):
        raise RuntimeError("Получены не все отчёты анализа")

    measurements = analysis["measurements"]
    total_ms = sum(item["duration_ms"] for item in measurements)
    hashes = {}
    for name in ("submissions.jsonl", "invalid_submissions.jsonl", "assignments.json"):
        hashes[name] = sha256((root / "data" / name).read_bytes()).hexdigest()
    result = {
        "python": platform.python_version(),
        "system": platform.system(),
        "package_version": version("school-lab"),
        "plugin_version": version("school-declaration-plugin"),
        "data_sha256": hashes,
        "submission_hashes": original_hashes,
        "first_import": first.to_dict(),
        "repeated_import": repeated.to_dict(),
        "mixed_import": mixed.to_dict(),
        "analyzers": analyzer_names,
        "reports": len(analysis["reports"]),
        "analysis_errors": analysis["analysis_errors"],
        "measurements": measurements,
        "analysis_total_ms": total_ms,
        "analysis_average_ms": total_ms / len(measurements) if measurements else 0.0,
    }
    reports_json = json.dumps(analysis["reports"], ensure_ascii=False, allow_nan=False, indent=2)
    result_json = json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2)
    (output / "reports.json").write_text(reports_json + "\n", encoding="utf-8")
    (output / "verification.json").write_text(result_json + "\n", encoding="utf-8")
    print(result_json)


if __name__ == "__main__":
    main()
