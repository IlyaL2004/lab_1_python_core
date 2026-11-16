"""Разбиение по происхождению, проверка разметки и метрики сходства."""
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math

from .models import Submission


@dataclass(frozen=True, slots=True)
class LabelledPair:
    """Явная метка пары, полученная из истории создания корпуса.

    Вход: left_id/right_id — ID; split — часть корпуса; два bool — метки;
    explanation — известное происхождение. Выход: неизменяемая разметка.
    """

    left_id: str
    right_id: str
    split: str
    derivation_relation: bool
    template_only: bool
    explanation: str


def validate_split(
    submissions: Sequence[Submission],
    provenance: Mapping[str, dict],
    split: Mapping[str, list[str]],
) -> dict[str, str]:
    """Проверить полное разбиение документов без утечки семейств.

    Вход: сдачи, происхождение по ID и списки ID validation/test.
    Выход: соответствие ID → часть; нарушение контракта вызывает ValueError.
    """
    if not isinstance(provenance, Mapping) or not isinstance(split, Mapping):
        raise ValueError("Provenance и split должны быть JSON-объектами")
    if any(type(identifier) is not str for identifier in provenance):
        raise ValueError("ID в provenance должны быть строками")
    documents = {item.submission_id: item for item in submissions}
    if len(documents) != len(submissions) or set(provenance) != set(documents):
        raise ValueError("ID сдач должны быть уникальны и полностью покрыты provenance")
    if set(split) != {"validation", "test"}:
        raise ValueError("Для фиксированных методов требуются validation и test")
    for meta in provenance.values():
        if not isinstance(meta, Mapping):
            raise ValueError("Запись provenance должна быть JSON-объектом")
        family = meta.get("family_id")
        if type(family) is not str or not family.strip():
            raise ValueError("Для каждого документа требуется family_id")
        parent = meta.get("parent_id")
        if parent is not None and type(parent) is not str:
            raise ValueError("parent_id должен быть строкой либо null")
    membership: dict[str, str] = {}
    family_parts: dict[str, str] = {}
    for part, identifiers in split.items():
        if type(identifiers) is not list or not identifiers:
            raise ValueError("Каждая часть должна быть непустым списком ID")
        for identifier in identifiers:
            if type(identifier) is not str:
                raise ValueError("ID в split должны быть строками")
            if identifier not in documents or identifier in membership:
                raise ValueError("Неизвестный ID или повтор документа в split")
            meta = provenance[identifier]
            family = meta["family_id"]
            if family in family_parts and family_parts[family] != part:
                raise ValueError("Утечка: семейство присутствует в разных частях")
            parent = meta.get("parent_id")
            if parent is not None:
                if parent not in provenance or parent == identifier:
                    raise ValueError("Некорректный parent_id")
                if provenance[parent].get("family_id") != family:
                    raise ValueError("Потомок и родитель должны иметь общее семейство")
            membership[identifier] = part
            family_parts[family] = part
    if set(membership) != set(documents):
        raise ValueError("Split должен включать все документы корпуса")
    checked: set[str] = set()
    for identifier in provenance:
        path: set[str] = set()
        current = identifier
        while current is not None and current not in checked:
            if current in path:
                raise ValueError("Цикл в цепочке parent_id: происхождение не имеет корня")
            path.add(current)
            current = provenance[current].get("parent_id")
        checked.update(path)
    return membership


def validate_pairs(
    rows: Sequence[dict],
    submissions: Sequence[Submission],
    provenance: Mapping[str, dict],
    split: Mapping[str, list[str]],
) -> list[LabelledPair]:
    """Проверить явные метки, допустимость пар и отсутствие межчастных пар.

    Вход: JSON-строки разметки, сдачи, provenance и split.
    Выход: список LabelledPair; несогласованность вызывает ValueError.
    """
    membership = validate_split(submissions, provenance, split)
    documents = {item.submission_id: item for item in submissions}
    seen: set[tuple[str, str]] = set()
    result = []
    fields = {"left_id", "right_id", "split", "derivation_relation", "template_only", "explanation"}
    for row in rows:
        if type(row) is not dict or set(row) != fields:
            raise ValueError("Неверные поля размеченной пары")
        left_id, right_id, part = row["left_id"], row["right_id"], row["split"]
        if type(left_id) is not str or type(right_id) is not str:
            raise ValueError("ID пары должны быть строками")
        if left_id not in documents or right_id not in documents:
            raise ValueError("Пара ссылается на неизвестную сдачу")
        left, right = documents[left_id], documents[right_id]
        if (left_id == right_id or left.student_id == right.student_id
                or left.assignment_id != right.assignment_id):
            raise ValueError("Пара недопустима для межстуденческого сравнения")
        key = tuple(sorted((left_id, right_id)))
        if key in seen:
            raise ValueError("Повтор размеченной пары, включая обратную ориентацию")
        seen.add(key)
        if membership[left_id] != part or membership[right_id] != part:
            raise ValueError("Оба конца пары должны принадлежать указанной части")
        label, template_only = row["derivation_relation"], row["template_only"]
        if type(label) is not bool or type(template_only) is not bool:
            raise ValueError("Метки пары должны быть bool")
        same_family = provenance[left_id]["family_id"] == provenance[right_id]["family_id"]
        if label != same_family or (template_only and label):
            raise ValueError("Метки противоречат известному происхождению")
        if type(row["explanation"]) is not str or not row["explanation"].strip():
            raise ValueError("Требуется описание происхождения пары")
        result.append(LabelledPair(**row))
    if {pair.split for pair in result} != {"validation", "test"}:
        raise ValueError("Размеченные пары требуются в обеих частях")
    return result


def _ratio(numerator: int, denominator: int) -> float | None:
    """Вход: числитель и знаменатель. Выход: доля либо None при нуле."""
    return numerator / denominator if denominator else None


def metrics(rows: Sequence[tuple[bool, float | None]], threshold: float) -> dict:
    """Посчитать качество на определённых score и отдельно долю отказов.

    Вход: пары (известная метка, score либо None) и конечный порог 0–1.000001.
    Выход: матрица ошибок, precision/recall/F1 и показатели отказов.
    При нулевом знаменателе метрика равна None. Ошибки входа — ValueError.
    """
    if (type(threshold) not in (int, float) or not 0 <= threshold <= 1.000001
            or not math.isfinite(threshold)):
        raise ValueError("Порог должен быть числом от 0 до 1.000001")
    counts = Counter({"tp": 0, "fp": 0, "fn": 0, "tn": 0})
    abstentions = positive_abstentions = positives = 0
    for label, score in rows:
        if type(label) is not bool:
            raise ValueError("Метка должна быть bool")
        positives += label
        if score is None:
            abstentions += 1
            positive_abstentions += label
            continue
        if (type(score) not in (int, float) or not 0 <= score <= 1
                or not math.isfinite(score)):
            raise ValueError("Score должен быть конечным числом 0–1 либо None")
        predicted = score >= threshold
        key = "tp" if label and predicted else "fn" if label else "fp" if predicted else "tn"
        counts[key] += 1
    tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
    return {
        **counts,
        "total_pairs": len(rows),
        "defined_pairs": len(rows) - abstentions,
        "precision": _ratio(tp, tp + fp),
        "recall_defined": _ratio(tp, tp + fn),
        "f1_defined": _ratio(2 * tp, 2 * tp + fp + fn),
        "abstentions": abstentions,
        "abstention_rate": _ratio(abstentions, len(rows)),
        "positive_abstentions": positive_abstentions,
        "recall_all_positives": _ratio(tp, positives),
    }


def choose_threshold(rows: Sequence[tuple[bool, float | None]]) -> tuple[float, dict]:
    """Подобрать порог только по переданной настроечной части.

    Вход: validation-метки и score. Выход: (порог, метрики validation).
    Максимизируется F1; при равенстве берётся больший порог. Точка выше 1
    позволяет не выдавать ни одного сигнала. Пустой вход вызывает ValueError.
    Неопределённый F1 при выборе считается нулём, но в метриках остаётся None.
    """
    if not rows:
        raise ValueError("Для выбора порога нужны размеченные validation-пары")
    metrics(rows, 0.0)
    thresholds = sorted({0.0, 1.000001} | {float(score) for _, score in rows if score is not None})
    candidates = [(threshold, metrics(rows, threshold)) for threshold in thresholds]
    return max(candidates, key=lambda item: (
        item[1]["f1_defined"] if item[1]["f1_defined"] is not None else 0,
        item[0],
    ))
