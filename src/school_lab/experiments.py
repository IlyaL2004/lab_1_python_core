"""Воспроизводимая оценка token/AST по заранее размеченному корпусу."""
from dataclasses import asdict
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import platform
import sys

from .evaluation import choose_threshold, metrics, validate_pairs
from . import features
from .hashing import stable_hash
from .parsing import decode_json, load_assignments
from .pipeline import file_sha256, iter_submissions
from .similarity import compare_prepared, compare_submissions
from .similarity_models import SimilarityConfig


def _read_json(path: Path) -> object:
    """Вход: путь к небольшому manifest. Выход: проверенный JSON-объект."""
    return decode_json(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict]:
    """Вход: файл явных меток. Выход: список записей, разобранных построчно."""
    with path.open(encoding="utf-8") as stream:
        return [decode_json(line) for line in stream if line.strip()]


def _write_json(path: Path, value: object) -> None:
    """Вход: путь и JSON-значение. Выход: None; UTF-8 файл с запретом NaN."""
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _digest(value: object) -> str:
    """Вход: JSON-значение. Выход: стабильный SHA-256 сортированной сериализации."""
    raw = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(raw.encode("ascii")).hexdigest()


def _environment() -> dict:
    """Вход: текущее окружение. Выход: версии Python, ОС и используемых пакетов."""
    packages = {}
    for name in ("school-lab", "pytest"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    return {"python": sys.version, "implementation": platform.python_implementation(),
            "platform": platform.platform(), "packages": packages}


def _kind(label: bool, score: float | None, threshold: float) -> str:
    """Вход: метка, score и порог. Выход: TP/FP/FN/TN либо ABSTENTION."""
    if score is None:
        return "ABSTENTION"
    return "TP" if label and score >= threshold else "FN" if label else "FP" if score >= threshold else "TN"


def _diagnostics(directory: Path, assignments: dict) -> list[dict]:
    """Выполнить отдельные диагностические случаи с заранее заданными порогами.

    Вход: каталог данных и задания. Выход: результаты с evidence и объяснением;
    эти примеры не входят в метрики validation/test и не меняют их пороги.
    Конфигурации отдельных случаев различаются; признаки вычисляются для каждой пары.
    """
    documents_path = directory / "diagnostics.jsonl"
    cases_path = directory / "diagnostic_cases.json"
    if not documents_path.exists() and not cases_path.exists():
        return []
    documents = {item.submission_id: item for item in iter_submissions(documents_path)}
    result = []
    for case in _read_json(cases_path):
        left, right = documents[case["left_id"]], documents[case["right_id"]]
        for method in case["methods"]:
            for exclude in case["exclude_template"]:
                config = SimilarityConfig(method=method, exclude_template=exclude)
                compared = compare_submissions(left, right, assignments[left.assignment_id], config,
                                               threshold=case["threshold"])
                result.append({
                    "name": case["name"], "scope": "diagnostic-not-test",
                    "derivation_relation": case["label"], "explanation": case["explanation"],
                    "config": asdict(config), "threshold": case["threshold"],
                    "kind": _kind(case["label"], compared.score, case["threshold"]),
                    "expected_signal": case["label"],
                    "predicted_signal": None if compared.score is None else compared.score >= case["threshold"],
                    "result": compared.to_dict(),
                })
    return result


def run_experiment(
    corpus_path: Path,
    assignments_path: Path,
    provenance_path: Path,
    split_path: Path,
    pairs_path: Path,
    output_dir: Path,
    *,
    seed: int = 42,
) -> dict:
    """Оценить два фиксированных метода и сохранить воспроизводимый отчёт.

    Вход: JSONL сдач, задания, provenance, split, JSONL меток, каталог и seed.
    Выход: сводка run.json. Split/метки проверяются до извлечения признаков;
    порог выбирается по validation, затем без изменения применяется к test.
    Признаки каждой сдачи вычисляются один раз для каждого метода в локальном индексе.
    Извлечение зависит только от самой сдачи и конфигурации, без обучения на корпусе.
    Seed записывается для протокола: фиксированный split не пересоздаётся.
    """
    if type(seed) is not int:
        raise ValueError("Seed должен быть целым числом")
    documents = list(iter_submissions(corpus_path))
    provenance, split = _read_json(provenance_path), _read_json(split_path)
    pairs = validate_pairs(_read_jsonl(pairs_path), documents, provenance, split)
    assignments = load_assignments(assignments_path)
    if any(item.assignment_id not in assignments for item in documents):
        raise ValueError("Для каждой сдачи необходимо описание задания")
    configuration_path = corpus_path.parent / "config.json"
    settings = _read_json(configuration_path) if configuration_path.exists() else {}
    if not isinstance(settings, dict):
        raise ValueError("Конфигурация эксперимента должна быть JSON-объектом")
    if settings.get("methods", ["token", "ast"]) != ["token", "ast"]:
        raise ValueError("Эксперимент сравнивает token и ast в зафиксированном порядке")
    feature_settings = {name: settings[name] for name in
                        ("n", "exclude_template", "max_evidence", "config_version") if name in settings}
    validation_pairs = [pair for pair in pairs if pair.split == "validation"]
    all_results, method_metrics, configurations, config_hashes = [], {}, {}, {}
    for method in ("token", "ast"):
        config = SimilarityConfig(method=method, **feature_settings)
        configurations[method] = asdict(config)
        config_hashes[method] = {
            assignment_id: stable_hash(features.configuration_payload(assignments[assignment_id], config))
            for assignment_id in dict.fromkeys(item.assignment_id for item in documents)
        }
        prepared = {
            item.submission_id: features.prepare_submission(item, assignments[item.assignment_id], config)
            for item in documents
        }
        validation_scores = [
            (pair.derivation_relation,
             compare_prepared(prepared[pair.left_id], prepared[pair.right_id]).score)
            for pair in validation_pairs
        ]
        threshold, validation_metrics = choose_threshold(validation_scores)
        # Отложенные пары сравниваются только после фиксации порога.
        test_rows = []
        template_rows = []
        for pair in pairs:
            compared = compare_prepared(
                prepared[pair.left_id], prepared[pair.right_id], threshold=threshold,
            )
            if pair.split == "test":
                test_rows.append((pair.derivation_relation, compared.score))
                if pair.template_only:
                    template_rows.append((pair.derivation_relation, compared.score))
            all_results.append({
                **asdict(pair), "method": method, "threshold": threshold,
                "kind": _kind(pair.derivation_relation, compared.score, threshold),
                "expected_signal": pair.derivation_relation,
                "predicted_signal": None if compared.score is None else compared.score >= threshold,
                "left_family": provenance[pair.left_id]["family_id"],
                "right_family": provenance[pair.right_id]["family_id"],
                "result": compared.to_dict(),
            })
        method_metrics[method] = {
            "threshold": threshold, "threshold_selected_on": "validation",
            "validation": validation_metrics, "test": metrics(test_rows, threshold),
            "test_template_only": metrics(template_rows, threshold),
        }
    test_errors = []
    for row in all_results:
        if row["split"] != "test" or row["kind"] not in {"FP", "FN", "ABSTENTION"}:
            continue
        reason = row["result"].get("reason")
        if row["kind"] == "ABSTENTION":
            analysis = f"Метод отказался от оценки: {reason}. Отказ учитывается в coverage и operational recall."
        elif row["kind"] == "FP":
            analysis = ("По provenance корни независимы. Общая постановка и короткие конструкции "
                        "могут давать одинаковые признаки; для AST дополнительная причина — потеря различий при нормализации.")
        else:
            transformations = [provenance[row[key]].get("transformation", "не указано")
                               for key in ("left_id", "right_id")]
            analysis = (f"Общий корень известен; преобразования {transformations}. "
                        "Изменение значений токенов или структуры снизило score ниже выбранного порога.")
        test_errors.append({**row, "analysis": analysis})
    diagnostics = _diagnostics(corpus_path.parent, assignments)
    source_directory = Path(__file__).resolve().parent
    source_files = {path.name: file_sha256(path) for path in sorted(source_directory.glob("*.py"))}
    inputs = {name: {"file": path.name, "sha256": file_sha256(path)} for name, path in {
        "corpus": corpus_path, "assignments": assignments_path, "provenance": provenance_path,
        "split": split_path, "pairs": pairs_path,
    }.items()}
    for name in ("diagnostics.jsonl", "diagnostic_cases.json", "config.json", "corpus_manifest.json"):
        path = corpus_path.parent / name
        if path.exists():
            inputs[name] = {"file": name, "sha256": file_sha256(path)}
    run = {
        "schema_version": 1, "seed": seed, "environment": _environment(),
        "configuration": configurations, "config_hashes_by_assignment": config_hashes,
        "configuration_sha256": _digest(configurations), "inputs": inputs,
        "corpus_sha256": inputs["corpus"]["sha256"],
        "source_sha256": _digest(source_files), "source_files_sha256": source_files,
        "documents": len(documents), "labelled_pairs": len(pairs),
        "template_only_pairs": sum(pair.template_only for pair in pairs),
        "families": len({meta["family_id"] for meta in provenance.values()}),
        "split": split,
        "split_families": {part: sorted({provenance[identifier]["family_id"] for identifier in identifiers})
                           for part, identifiers in split.items()},
        "threshold_selection": "maximum validation F1; ties choose larger threshold; test never tunes threshold",
        "abstention_policy": "metrics on defined scores plus abstention rate and recall across all positive pairs",
        "metrics": method_metrics,
        "test_error_count": len(test_errors), "diagnostic_comparisons": len(diagnostics),
        "artifacts": {"pairs": "pair_results.jsonl", "metrics": "metrics.json",
                      "errors": "error_analysis.json", "manifest": "run.json"},
        "limitations": [
            "Синтетический корпус: результаты не оценивают плагиат, использование ИИ или качество алгоритма.",
            "Пары зависимы; разнообразие определяется семействами, а не только числом пар.",
            "Некоторые конструкции AST намеренно не поддерживаются и дают явный отказ.",
            "Диагностические случаи имеют заранее заданные пороги и не включены в test-метрики.",
            "После анализа test новые изменения требуют нового отложенного корпуса для независимой оценки.",
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "pair_results.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
        for row in all_results:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
    _write_json(output_dir / "metrics.json", method_metrics)
    _write_json(output_dir / "error_analysis.json", {"test_errors": test_errors, "diagnostics": diagnostics})
    _write_json(output_dir / "run.json", run)
    return run
