"""Последовательные замеры потока признаков и исходного попарного сравнения."""

import cProfile
import gc
import hashlib
import io
import json
import platform
import pstats
import random
import sys
import tracemalloc
from collections import Counter
from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import asdict, dataclass
from itertools import combinations, islice
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter

from .errors import SubmissionError
from .models import Assignment, Evidence, Submission
from .parsing import load_assignments
from .pipeline import file_sha256, iter_features, iter_submissions
from .similarity import compare_submissions, eligible
from .similarity_models import FeatureOccurrence, SimilarityConfig


@dataclass(frozen=True, slots=True)
class FeatureIndexEntry:
    """Признаки одной записи без хранения исходного текста и кода.

    Вход: ID студента/задания, хеши, признаки, их координаты, шаблон и статус.
    Выход: неизменяемая запись нагрузочного индекса; исходник читается отдельно.
    """

    student_id: str
    assignment_id: str
    submission_hash: str
    config_hash: str
    features: frozenset[str]
    occurrences: tuple[FeatureOccurrence, ...]
    excluded_template: tuple[Evidence, ...]
    status: str


def build_feature_index(
    path: Path,
    assignments: dict[str, Assignment],
    config: SimilarityConfig,
) -> dict[str, FeatureIndexEntry]:
    """Лениво прочитать JSONL и накопить индекс признаков.

    Вход: path — файл сдач; assignments — задания; config — метод и настройки.
    Выход: словарь по submission_id без исходных text/code.
    Ошибка строки или неизвестное задание вызывают ValueError: нагрузка должна
    содержать ровно заданное число корректных записей.
    """
    result = {}
    errors = []
    try:
        with closing(iter_features(path, assignments, config, errors)) as features:
            for prepared in features:
                result[prepared.submission_id] = FeatureIndexEntry(
                    prepared.student_id,
                    prepared.assignment_id,
                    prepared.submission_hash,
                    prepared.config_hash,
                    prepared.features,
                    prepared.occurrences,
                    prepared.excluded_template,
                    prepared.status,
                )
    except SubmissionError as exc:
        message = "Неизвестное задание" if exc.field == "assignment_id" else exc.message
        raise ValueError(message) from exc
    if errors:
        raise ValueError(f"Ошибочные записи в нагрузке: {len(errors)}")
    return result


def write_workload(
    path: Path, examples: Sequence[Submission], size: int, seed: int,
) -> None:
    """Записать искусственную нагрузку до начала измерения.

    Вход: path — выходной JSONL; examples — образцы; size — число записей;
    seed — начальное состояние генератора выбора образцов.
    Выход: None; создаётся UTF-8 файл с уникальными ID и студентами.
    Эти повторения кода предназначены для нагрузки, а не оценки качества.
    """
    if not examples:
        raise ValueError("Для нагрузки нужен хотя бы один пример")
    if type(size) is not int or size < 1:
        raise ValueError("Размер нагрузки должен быть положительным целым")
    if type(seed) is not int:
        raise ValueError("seed должен быть целым числом")
    generator = random.Random(seed)
    with path.open("w", encoding="utf-8", newline="\n") as output:
        for number in range(size):
            raw = asdict(generator.choice(examples))
            raw["submission_id"] = f"load-{number:06d}"
            raw["student_id"] = f"load-student-{number:06d}"
            output.write(json.dumps(raw, ensure_ascii=False, allow_nan=False) + "\n")


def pair_results(
    submissions: Sequence[Submission],
    assignments: dict[str, Assignment],
    config: SimilarityConfig,
) -> list[dict[str, object]]:
    """Сравнить допустимые пары с повторным извлечением признаков.

    Вход: submissions — фиксированная небольшая выборка; assignments/config —
    задания и метод сравнения.
    Выход: детерминированный список словарей PairResult, включая evidence.
    Для каждой пары признаки обеих сдач вычисляются заново. Порог 0.5 нужен
    только для воспроизводимого отчёта нагрузки, а не для оценки качества.
    """
    results = []
    for left, right in combinations(submissions, 2):
        if not eligible(left, right):
            continue
        report = compare_submissions(
            left, right, assignments[left.assignment_id], config,
        )
        results.append(report.to_dict())
    return results


def _measure(
    operation: Callable[[], object],
    repeats: int,
    *,
    describe: Callable[[object], dict[str, object]] | None = None,
) -> dict[str, object]:
    """Измерить время и память отдельными прогонами.

    Вход: operation — синхронная операция; repeats >= 3; describe — сводка
    первого результата, вычисляемая после остановки таймера.
    Выход: сырые значения и медианы wall time / peak Python allocations.
    Сборка мусора перед каждым прогоном не входит в измеряемый участок.
    """
    if type(repeats) is not int or repeats < 3:
        raise ValueError("Нужно минимум три повтора времени и памяти")
    if tracemalloc.is_tracing():
        raise ValueError("Перед замером нужно остановить внешний tracemalloc")
    seconds = []
    peaks = []
    summary = {}
    for number in range(repeats):
        gc.collect()
        start = perf_counter()
        result = operation()
        seconds.append(perf_counter() - start)
        if number == 0 and describe is not None:
            summary = describe(result)
        del result
    for _ in range(repeats):
        gc.collect()
        tracemalloc.start()
        try:
            result = operation()
            _, peak = tracemalloc.get_traced_memory()
            peaks.append(peak)
            del result
        finally:
            tracemalloc.stop()
    return {
        "wall_seconds": seconds,
        "wall_seconds_median": median(seconds),
        "peak_bytes": peaks,
        "peak_bytes_median": median(peaks),
        **summary,
    }


def _describe_index(index: dict[str, FeatureIndexEntry]) -> dict[str, object]:
    """Вход: готовый индекс; выход: фактический размер и число статусов метода."""
    counts = Counter(entry.status for entry in index.values())
    return {
        "indexed_records": len(index),
        "status_counts": {
            status: counts[status] for status in ("ok", "unsupported", "invalid_source")
        },
        "empty_features": sum(not entry.features for entry in index.values()),
    }


def _profile(operation: Callable[[], object]) -> str:
    """Профилировать отдельный прогон; вход — операция, выход — текст cProfile."""
    profiler = cProfile.Profile()
    profiler.runcall(operation)
    output = io.StringIO()
    pstats.Stats(profiler, stream=output).strip_dirs().sort_stats("cumulative").print_stats(30)
    return output.getvalue()


def _save_json(path: Path, value: object) -> None:
    """Записать артефакт; вход — путь и JSON-значение, выход — None."""
    path.write_text(
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n",
        encoding="utf-8",
    )


def run_benchmarks(
    corpus_path: Path,
    assignments_path: Path,
    output_dir: Path,
    *,
    sizes: tuple[int, ...] = (100, 1000, 5000),
    repeats: int = 3,
    seed: int = 42,
) -> dict[str, object]:
    """Сохранить замеры двух методов и профиль исходного сравнения пар.

    Вход: corpus_path/assignments_path — исходные данные; output_dir — артефакты;
    sizes — положительные уникальные размеры; repeats >= 3; seed — целое число.
    Выход: сводка benchmark.json; дополнительно baseline.json и profile_before.txt.
    Для нагрузки читаются не более 32 образцов; пары берутся из первых 12.
    На больших размерах измеряется построение индекса, а не сравнение всех пар.
    """
    if not sizes or any(type(size) is not int or size < 1 for size in sizes):
        raise ValueError("Нужны положительные целые размеры нагрузки")
    if len(set(sizes)) != len(sizes):
        raise ValueError("Размеры нагрузки не должны повторяться")
    if type(repeats) is not int or repeats < 3:
        raise ValueError("Нужно минимум три повтора времени и памяти")
    if type(seed) is not int:
        raise ValueError("seed должен быть целым числом")
    if tracemalloc.is_tracing():
        raise ValueError("Перед замером нужно остановить внешний tracemalloc")

    assignments = load_assignments(assignments_path)
    errors = []
    source = iter_submissions(corpus_path, errors)
    try:
        examples = list(islice(source, 32))
    finally:
        source.close()
    if not examples or errors:
        raise ValueError("Корпус должен содержать корректные образцы нагрузки")
    if any(item.assignment_id not in assignments for item in examples):
        raise ValueError("В корпусе есть неизвестное задание")
    sample = examples[:12]
    pair_count = sum(eligible(left, right) for left, right in combinations(sample, 2))
    if not pair_count:
        raise ValueError("Для профилирования нужна хотя бы одна допустимая пара")

    output_dir.mkdir(parents=True, exist_ok=True)
    configs = [SimilarityConfig(method=method) for method in ("token", "ast")]
    scaling = []
    workloads = []
    with TemporaryDirectory(prefix="school-lab2-load-") as directory:
        for size in sizes:
            path = Path(directory) / f"load-{size}.jsonl"
            write_workload(path, examples, size, seed)
            workloads.append({"records": size, "sha256": file_sha256(path)})
            for config in configs:
                def operation() -> dict[str, FeatureIndexEntry]:
                    return build_feature_index(path, assignments, config)

                measurements = _measure(operation, repeats, describe=_describe_index)
                if measurements["indexed_records"] != size:
                    raise AssertionError("Размер индекса не совпадает с нагрузкой")
                scaling.append({
                    "method": config.method,
                    "method_version": config.method_version,
                    "config": asdict(config),
                    "records": size,
                    **measurements,
                })

    methods = []
    profiles_before = []
    for config in configs:
        def before() -> list[dict[str, object]]:
            return pair_results(sample, assignments, config)

        original = before()
        encoded = json.dumps(original, sort_keys=True, ensure_ascii=True).encode("ascii")
        # Профиль записывается отдельно от обычных замеров perf_counter.
        profiles_before.append(f"METHOD {config.method}\n{_profile(before)}")
        measurements = _measure(before, repeats)
        methods.append({
            "method": config.method,
            "config": asdict(config),
            "method_version": config.method_version,
            "pairs": len(original),
            "result_sha256": hashlib.sha256(encoded).hexdigest(),
            "measurements": measurements,
        })

    baseline = {
        "sample_ids": [item.submission_id for item in sample],
        "records": len(sample),
        "eligible_pairs": pair_count,
        "strategy": "Извлечение признаков обеих сдач заново для каждой пары",
        "threshold": 0.5,
        "result_hash_format": "SHA-256 JSON PairResult.to_dict: sort_keys=True, ensure_ascii=True, default separators",
        "methods": methods,
    }
    source_directory = Path(__file__).resolve().parent
    source_files = {
        path.name: file_sha256(path) for path in sorted(source_directory.glob("*.py"))
    }
    source_encoded = json.dumps(
        source_files, sort_keys=True, ensure_ascii=True, separators=(",", ":"),
    ).encode("ascii")
    result = {
        "schema_version": 1,
        "environment": {
            "python": sys.version,
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "seed": seed,
        "repeats": repeats,
        "corpus_sha256": file_sha256(corpus_path),
        "assignments_sha256": file_sha256(assignments_path),
        "source_files_sha256": source_files,
        "source_sha256": hashlib.sha256(source_encoded).hexdigest(),
        "workload": {
            "synthetic": True,
            "example_ids": [item.submission_id for item in examples],
            "files": workloads,
            "generation_measured": False,
        },
        "measurement": {
            "scaling": "JSONL read -> validate -> features -> complete feature index",
            "index_memory": "Пропорциональна объёму признаков и координат; text/code не хранятся. AST-поддеревья могут занимать квадратичный объём по длине кода",
            "pairwise_at_large_sizes": False,
            "pairwise": "Первые 12 образцов: признаки извлекаются заново для каждой допустимой пары",
            "wall_time": "perf_counter, без cProfile и tracemalloc",
            "peak_memory": "tracemalloc peak Python allocations, не RSS процесса",
            "separate_time_and_memory_runs": True,
            "concurrency": False,
        },
        "scaling": scaling,
        "baseline": baseline,
        "artifacts": [
            "benchmark.json", "baseline.json", "profile_before.txt",
        ],
    }
    _save_json(output_dir / "baseline.json", baseline)
    (output_dir / "profile_before.txt").write_text("\n".join(profiles_before), encoding="utf-8")
    _save_json(output_dir / "benchmark.json", result)
    return result
