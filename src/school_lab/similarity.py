"""Чистое сравнение признаков, проверка парных отчётов и полный top-k."""
import math
from collections.abc import Iterable, Set

from .features import prepare_submission
from .models import Assignment, Evidence, Submission
from .similarity_models import (
    PairEvidence, PairResult, PreparedSubmission, SimilarityConfig,
)
from .source_text import physical_lines


def jaccard(left: Set, right: Set) -> float | None:
    """Вычислить отношение пересечения множеств к объединению.

    Вход: left/right — множества признаков. Выход: float 0–1;
    None при двух пустых множествах, 0 при одном пустом.
    """
    common = len(left & right)
    size = len(left) + len(right) - common
    return common / size if size else None


def eligible(
    left: Submission | PreparedSubmission, right: Submission | PreparedSubmission,
) -> bool:
    """Вход: две версии. Выход: разные ли это студенты одного задания без self-match."""
    return (left.submission_id != right.submission_id
            and left.assignment_id == right.assignment_id
            and left.student_id != right.student_id)


def _validate_threshold(threshold: float) -> None:
    """Вход: порог. Выход: None либо ValueError; 1.000001 означает отсутствие сигналов."""
    if (type(threshold) not in (float, int) or not 0 <= threshold <= 1.000001
            or not math.isfinite(threshold)):
        raise ValueError("Порог должен быть конечным числом от 0 до 1.000001")


def _check_context(left: PreparedSubmission, right: PreparedSubmission) -> None:
    """Вход: подготовленные версии. Выход: None либо отказ при разных конфигурациях."""
    if left.config_hash != right.config_hash or left.config != right.config:
        raise ValueError("Для сравнения требуется одна конфигурация задания и метода")


def _pair_status(left: PreparedSubmission, right: PreparedSubmission) -> tuple[str, str | None]:
    """Вход: две версии. Выход: статус допустимости и извлечения, затем объяснение."""
    if not eligible(left, right):
        return "ineligible", "Нужны разные сдачи разных студентов одного задания"
    failures = [item for item in (left, right) if item.status == "invalid_source"]
    if failures:
        reason = "; ".join(f"{item.submission_id}: {item.reason}" for item in failures)
        return "invalid_source", reason
    if left.status != "ok" or right.status != "ok":
        raise ValueError("Неизвестный статус подготовленной сдачи")
    if not left.features and not right.features:
        return "insufficient_data", "После извлечения и исключения шаблона оба множества пусты"
    return "ok", None


def _evidence(left: PreparedSubmission, right: PreparedSubmission) -> tuple[PairEvidence, ...]:
    """Вход: две версии. Выход: стабильная выборка первых вхождений общих признаков."""
    left_index, right_index = {}, {}
    for prepared, index in ((left, left_index), (right, right_index)):
        for item in sorted(prepared.occurrences, key=lambda value: (value.start_line, value.end_line)):
            index.setdefault(item.feature, item)
    result = []
    for feature in sorted(left.features & right.features)[:left.config.max_evidence]:
        a, b = left_index[feature], right_index[feature]
        result.append(PairEvidence(
            feature,
            Evidence(left.submission_id, "code", a.start_line, a.end_line),
            Evidence(right.submission_id, "code", b.start_line, b.end_line),
        ))
    return tuple(result)


def compare_prepared(
    left: PreparedSubmission, right: PreparedSubmission, *, threshold: float = 0.5,
) -> PairResult:
    """Сравнить уже вычисленные признаки без повторного разбора исходников.

    Вход: left/right — версии одной конфигурации; threshold — порог сигнала.
    Выход: PairResult с исходным evidence; отказы имеют score=None.
    Разные конфигурации и неверный порог вызывают ValueError.
    """
    _validate_threshold(threshold)
    _check_context(left, right)
    status, reason = _pair_status(left, right)
    score = jaccard(left.features, right.features) if status == "ok" else None
    result = PairResult(
        left_id=left.submission_id, right_id=right.submission_id,
        left_hash=left.submission_hash, right_hash=right.submission_hash,
        method=left.config.method, method_version=left.config.method_version,
        config_hash=left.config_hash,
        score=score, status=status, reason=reason,
        evidence=_evidence(left, right) if status == "ok" else (),
        excluded_left=left.excluded_template, excluded_right=right.excluded_template,
        review_required=score is not None and score >= threshold,
        threshold=float(threshold),
    )
    validate_pair_result(result, left, right)
    return result


def compare_submissions(
    left: Submission, right: Submission, assignment: Assignment,
    config: SimilarityConfig, *, threshold: float = 0.5,
) -> PairResult:
    """Подготовить две сдачи и сравнить их; удобный исходный вариант без кеша.

    Вход: две сдачи, их задание, настройки и порог.
    Выход: PairResult; контекст другого задания вызывает ValueError.
    """
    return compare_prepared(
        prepare_submission(left, assignment, config),
        prepare_submission(right, assignment, config), threshold=threshold,
    )


def top_k(
    query: PreparedSubmission, candidates: Iterable[PreparedSubmission],
    k: int = 5, *, threshold: float = 0.5,
) -> list[PairResult]:
    """Найти не более k допустимых кандидатов полным перебором.

    Вход: query, итератор кандидатов, положительный k и порог.
    Выход: список по убыванию score, затем ID; отказы не ранжируются,
    нулевые оценки допускаются. Самосопоставления и версии автора исключены.
    """
    if type(k) is not int or k < 1:
        raise ValueError("k должен быть положительным целым числом")
    _validate_threshold(threshold)
    results = []
    seen: set[str] = set()
    for candidate in candidates:
        if not eligible(query, candidate) or candidate.submission_id in seen:
            continue
        seen.add(candidate.submission_id)
        result = compare_prepared(query, candidate, threshold=threshold)
        if result.score is not None:
            results.append(result)
    return sorted(results, key=lambda item: (-item.score, item.right_id))[:k]


def _validate_reference(reference: Evidence, prepared: PreparedSubmission) -> None:
    """Вход: ссылка и оригинал. Выход: None либо ValueError для неверных строк/ID."""
    if (not isinstance(reference, Evidence) or reference.submission_id != prepared.submission_id
            or reference.field != "code"):
        raise ValueError("Evidence должен ссылаться на код соответствующей сдачи")
    if type(reference.start_line) is not int or type(reference.end_line) is not int:
        raise ValueError("Границы Evidence должны быть целыми числами")
    if not 1 <= reference.start_line <= reference.end_line <= len(physical_lines(prepared.code)):
        raise ValueError("Evidence выходит за границы оригинала")


def validate_pair_result(
    result: PairResult, left: PreparedSubmission, right: PreparedSubmission,
) -> None:
    """Проверить хеши, score и связь каждого признака с исходными координатами.

    Вход: result и обе подготовленные версии, на которых он получен.
    Выход: None при корректном отчёте; ValueError при нарушении контракта.
    """
    if not isinstance(result, PairResult):
        raise ValueError("Ожидается PairResult")
    _check_context(left, right)
    _validate_threshold(result.threshold)
    if type(result.schema_version) is not int or result.schema_version != 1:
        raise ValueError("Неподдерживаемая схема парного отчёта")
    expected = (left.submission_id, right.submission_id, left.submission_hash,
                right.submission_hash, left.config.method, left.config.method_version,
                left.config_hash)
    actual = (result.left_id, result.right_id, result.left_hash, result.right_hash,
              result.method, result.method_version, result.config_hash)
    if actual != expected:
        raise ValueError("Отчёт ссылается на другую версию или конфигурацию")
    status, reason = _pair_status(left, right)
    if result.status != status or result.reason != reason:
        raise ValueError("Статус или причина не соответствуют входным данным")
    expected_score = jaccard(left.features, right.features) if status == "ok" else None
    if result.score is not None and (
        type(result.score) not in (float, int) or not 0 <= result.score <= 1
        or not math.isfinite(result.score)
    ):
        raise ValueError("score должен быть конечным числом 0–1 или None")
    if result.score != expected_score:
        raise ValueError("score не соответствует признакам")
    if (type(result.review_required) is not bool or result.review_required != (
        expected_score is not None and expected_score >= result.threshold
    )):
        raise ValueError("Сигнал ручного разбора не соответствует порогу")
    if (result.excluded_left != left.excluded_template
            or result.excluded_right != right.excluded_template):
        raise ValueError("Исключения шаблона не соответствуют оригиналам")
    for references, prepared in ((result.excluded_left, left), (result.excluded_right, right)):
        for reference in references:
            _validate_reference(reference, prepared)
    if not isinstance(result.evidence, tuple) or len(result.evidence) > left.config.max_evidence:
        raise ValueError("Неверная коллекция evidence")
    if status != "ok" and result.evidence:
        raise ValueError("При отказе совпадений быть не должно")
    common_features = left.features & right.features
    seen: set[str] = set()
    for item in result.evidence:
        if (not isinstance(item, PairEvidence) or not isinstance(item.feature, str)
                or item.feature in seen):
            raise ValueError("Некорректное или повторное основание")
        seen.add(item.feature)
        if item.feature not in common_features:
            raise ValueError("Evidence содержит несовпавший признак")
        for reference, prepared in ((item.left, left), (item.right, right)):
            _validate_reference(reference, prepared)
            if not any(
                occurrence.feature == item.feature
                and occurrence.start_line == reference.start_line
                and occurrence.end_line == reference.end_line
                for occurrence in prepared.occurrences
            ):
                raise ValueError("Признак не находится на указанных исходных строках")
            if any(reference.start_line <= excluded.end_line
                   and excluded.start_line <= reference.end_line
                   for excluded in prepared.excluded_template):
                raise ValueError("Evidence пересекается с разрешённым шаблоном")
    if status == "ok" and left.config.max_evidence > 0 and common_features and not result.evidence:
        raise ValueError("Общие признаки требуют хотя бы одного примера evidence")
