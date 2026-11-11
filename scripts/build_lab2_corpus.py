"""Создать фиксированный синтетический корпус; оценки методов здесь не вычисляются."""
from argparse import ArgumentParser
import io
from itertools import combinations
import json
from pathlib import Path
import tokenize


TEMPLATE = "from typing import Iterable"
ROOTS = {
    "squares": [
        "def solve(values):\n    total = 0\n    for value in values:\n        if value >= 0:\n            total += value * value\n    return total\n",
        "def solve(numbers):\n    return sum(number ** 2 for number in numbers if number >= 0)\n",
        "def solve(items):\n    position = 0\n    result = 0\n    while position < len(items):\n        current = items[position]\n        if current < 0:\n            position += 1\n            continue\n        result = result + pow(current, 2)\n        position += 1\n    return result\n",
        "def solve(sequence):\n    squares = []\n    for entry in sequence:\n        if entry >= 0:\n            squares.append(entry ** 2)\n    return sum(squares)\n",
    ],
    "count_positive": [
        "def solve(values):\n    count = 0\n    for value in values:\n        if value > 0:\n            count += 1\n    return count\n",
        "def solve(numbers):\n    return len([number for number in numbers if number > 0])\n",
        "def solve(items):\n    position = len(items) - 1\n    count = 0\n    while position >= 0:\n        count += int(items[position] > 0)\n        position -= 1\n    return count\n",
        "def solve(sequence):\n    flags = []\n    for entry in sequence:\n        flags.append(entry > 0)\n    return sum(flags)\n",
    ],
    "maximum": [
        "def solve(values):\n    best = values[0]\n    for value in values[1:]:\n        if value > best:\n            best = value\n    return best\n",
        "def solve(numbers):\n    return max(numbers)\n",
        "def solve(items):\n    ordered = sorted(items)\n    return ordered[-1]\n",
        "def solve(sequence):\n    position = 1\n    largest = sequence[0]\n    while position < len(sequence):\n        candidate = sequence[position]\n        largest = candidate if candidate > largest else largest\n        position += 1\n    return largest\n",
    ],
    "reverse": [
        "def solve(values):\n    return values[::-1]\n",
        "def solve(numbers):\n    return list(reversed(numbers))\n",
        "def solve(items):\n    result = []\n    for item in items:\n        result.insert(0, item)\n    return result\n",
        "def solve(sequence):\n    answer = []\n    position = len(sequence)\n    while position:\n        position -= 1\n        answer.append(sequence[position])\n    return answer\n",
    ],
}
DESCRIPTIONS = {
    "squares": "Сумма квадратов неотрицательных элементов.",
    "count_positive": "Количество строго положительных элементов.",
    "maximum": "Максимум непустой последовательности.",
    "reverse": "Новая последовательность в обратном порядке.",
    "template": "Диагностические короткие фрагменты с общим разрешённым import.",
    "standard": "Проверка положительности числа одним сравнением.",
}


def renamed(source: str) -> str:
    """Вход: один из корней корпуса. Выход: потомок с переименованными локальными NAME."""
    preserved = {"def", "solve", "return", "for", "in", "if", "else", "while", "continue",
                 "sum", "len", "pow", "int", "max", "sorted", "list", "reversed", "append", "insert"}
    names = {}
    tokens = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.NAME and token.string not in preserved:
            name = names.setdefault(token.string, f"renamed_{len(names)}")
            token = token._replace(string=name)
        tokens.append(token)
    return tokenize.untokenize(tokens)


def submission(identifier: str, assignment: str, source: str) -> dict:
    """Вход: ID, задание и код. Выход: синтетическая запись схемы Submission v1."""
    return {
        "schema_version": 1, "submission_id": identifier,
        "student_id": f"synthetic-author-{identifier}", "assignment_id": assignment,
        "submitted_at": "2026-11-11T12:00:00+03:00",
        "text": "Синтетический пример корпуса для оценки алгоритмов сходства.",
        "code": source, "template_id": "allowed-import-v1",
        "ai_declaration": {"used": False, "tool": None, "purpose": None, "verified_by_student": False},
    }


def write_json(path: Path, value: object) -> None:
    """Вход: путь и JSON-значение. Выход: None; записан читаемый UTF-8 JSON."""
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    """Вход: путь и записи. Выход: None; одна JSON-запись на физическую строку."""
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")


def build_corpus(directory: Path) -> dict[str, int]:
    """Сохранить корпус, происхождение, split и метки до запуска сравнения.

    Вход: каталог данных. Выход: число сдач/семейств/пар/template_only.
    Корни перечислены явно; rename не создаёт новое независимое семейство.
    """
    directory.mkdir(parents=True, exist_ok=True)
    documents, provenance = [], {}
    split = {"validation": [], "test": []}
    for assignment, roots in ROOTS.items():
        for index, source in enumerate(roots):
            family = f"{assignment}-family-{index}"
            part = "validation" if index in (0, 2) else "test"
            variants = (source, "# Изменены оформление и комментарий\n" + source.replace(" = ", "="), renamed(source))
            for variant, code in enumerate(variants):
                identifier = f"{family}-v{variant}"
                documents.append(submission(identifier, assignment, TEMPLATE + "\n\n" + code))
                split[part].append(identifier)
                provenance[identifier] = {
                    "family_id": family, "parent_id": f"{family}-v0" if variant else None,
                    "transformation": ("original", "comments-and-format", "local-renaming")[variant],
                    "origin": "Корень явно записан отдельно для другого способа решения; потомки созданы только из него.",
                }
    for index in range(8):
        identifier = f"template-independent-{index}"
        code = TEMPLATE + f"\n\nslot_{index} = {index + 10}\n"
        documents.append(submission(identifier, "template", code))
        split["validation" if index < 4 else "test"].append(identifier)
        provenance[identifier] = {
            "family_id": identifier, "parent_id": None, "transformation": "independent-fragment",
            "origin": "По сценарию независимый однооператорный фрагмент; общий исходный текст только разрешённый import.",
        }
    membership = {identifier: part for part, ids in split.items() for identifier in ids}
    pairs = []
    for left, right in combinations(documents, 2):
        left_id, right_id = left["submission_id"], right["submission_id"]
        if left["assignment_id"] != right["assignment_id"] or membership[left_id] != membership[right_id]:
            continue
        related = provenance[left_id]["family_id"] == provenance[right_id]["family_id"]
        template_only = left["assignment_id"] == "template"
        pairs.append({
            "left_id": left_id, "right_id": right_id, "split": membership[left_id],
            "derivation_relation": related, "template_only": template_only,
            "explanation": "Потомки одного явно заданного корня." if related else
                "Независимые короткие фрагменты разделяют только разрешённый import." if template_only else
                "Разные явно записанные корни одного задания; общего предка в сценарии нет.",
        })
    assignments = [{"assignment_id": name, "allowed_template": TEMPLATE,
                    "requirements": [description]} for name, description in DESCRIPTIONS.items()]
    diagnostics = [
        submission("diag-rename-base", "squares", TEMPLATE + "\n" + ROOTS["squares"][0]),
        submission("diag-rename-child", "squares", TEMPLATE + "\n" + renamed(ROOTS["squares"][0])),
        submission("diag-template-a", "template", TEMPLATE + "\nx = 1\n"),
        submission("diag-template-b", "template", TEMPLATE + "\ny = 2\n"),
        submission("diag-standard-a", "standard", "def is_positive(x):\n    return x > 0\n"),
        submission("diag-standard-b", "standard", "def is_positive(x):\n    return x > 0\n"),
        submission("diag-empty-a", "standard", ""),
        submission("diag-empty-b", "standard", "# Только комментарий\n"),
        submission("diag-invalid", "standard", "def broken(:\n"),
    ]
    diagnostic_cases = [
        {"name": "rename-fn", "left_id": "diag-rename-base", "right_id": "diag-rename-child",
         "label": True, "threshold": 0.5, "methods": ["token", "ast"], "exclude_template": [True],
         "explanation": "Известный потомок получен переименованием; токеновый метод чувствителен к NAME. AST проверяется по собственному контракту."},
        {"name": "template-fp", "left_id": "diag-template-a", "right_id": "diag-template-b",
         "label": False, "threshold": 0.2, "methods": ["token"], "exclude_template": [False, True],
         "explanation": "Независимые короткие фрагменты имеют общий разрешённый import. Сравниваются две разные конфигурации."},
        {"name": "independent-standard-fp", "left_id": "diag-standard-a", "right_id": "diag-standard-b",
         "label": False, "threshold": 0.9, "methods": ["token", "ast"], "exclude_template": [True],
         "explanation": "По сценарию авторы независимо выбрали стандартное одношаговое решение. Равное представление не доказывает происхождение."},
        {"name": "empty-abstention", "left_id": "diag-empty-a", "right_id": "diag-empty-b",
         "label": False, "threshold": 0.5, "methods": ["token", "ast"], "exclude_template": [True],
         "explanation": "В обоих входах нет значимых признаков; отказ отражается отдельно от отрицательной оценки."},
        {"name": "syntax-abstention", "left_id": "diag-invalid", "right_id": "diag-standard-a",
         "label": False, "threshold": 0.5, "methods": ["token", "ast"], "exclude_template": [True],
         "explanation": "Некорректный синтаксис не позволяет надёжно сравнить представления; требуется явный отказ."},
    ]
    write_jsonl(directory / "submissions.jsonl", documents)
    write_json(directory / "assignments.json", assignments)
    write_json(directory / "provenance.json", provenance)
    write_json(directory / "split.json", split)
    write_jsonl(directory / "pairs.jsonl", pairs)
    write_jsonl(directory / "diagnostics.jsonl", diagnostics)
    write_json(directory / "diagnostic_cases.json", diagnostic_cases)
    write_json(directory / "config.json", {
        "seed": 42, "methods": ["token", "ast"], "n": 3,
        "exclude_template": True, "max_evidence": 5, "config_version": "1",
        "threshold_rule": "max-validation-f1-then-larger-threshold",
        "split_rule": "explicit-families-before-pairs-no-cross-split-pairs",
        "ast_unsupported": "comprehensions and generator expressions remain explicit abstentions",
    })
    counts = {"submissions": len(documents), "families": len({meta["family_id"] for meta in provenance.values()}),
              "pairs": len(pairs), "template_only": sum(pair["template_only"] for pair in pairs)}
    write_json(directory / "corpus_manifest.json", {
        **counts, "seed": 42, "construction": "Explicit deterministic roots; seed does not generate independent families.",
        "label_source": "Known synthetic construction history, never similarity scores.",
        "split_fixed_before_scoring": True,
        "synthetic_authors": True,
        "limitations": ["Малый искусственный корпус не представляет реальные студенческие работы.",
                        "Пары зависимы: одна сдача участвует в нескольких сравнениях.",
                        "Семейства одного задания сохраняют общие естественные конструкции.",
                        "AI-декларации — поля синтетических персонажей, не заявление об авторстве проекта."],
    })
    return counts


def main() -> None:
    """Вход: необязательный --output. Выход: файлы корпуса и статистика в stdout."""
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/lab2"))
    args = parser.parse_args()
    print(json.dumps(build_corpus(args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
