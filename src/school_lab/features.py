"""Токеновые окна с исходными координатами и исключением разрешённого шаблона."""
import ast
import io
import tokenize
from dataclasses import asdict, dataclass

from .analyzers import template_ranges
from .hashing import canonical_json, stable_hash, submission_hash
from .models import Assignment, Evidence, Submission
from .similarity_models import FeatureOccurrence, PreparedSubmission, SimilarityConfig


class InvalidSource(ValueError):
    """Вход: причина ошибки Python. Выход: исключение извлечения признаков."""


@dataclass(frozen=True, slots=True)
class LocatedToken:
    """Вход: тип/текст токена и строки. Выход: токен с исходными координатами."""

    value: tuple[str, str]
    start_line: int
    end_line: int


SKIP_TOKENS = frozenset({
    tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
    tokenize.DEDENT, tokenize.ENDMARKER, tokenize.ENCODING,
})


def overlaps(start: int, end: int, ranges: tuple[tuple[int, int], ...]) -> bool:
    """Вход: диапазон и исключения. Выход: пересекается ли хотя бы одно исключение."""
    return any(start <= last and first <= end for first, last in ranges)


def parse_source(source: str) -> ast.Module:
    """Проверить синтаксис без исполнения кода.

    Вход: source — исходный Python. Выход: AST либо InvalidSource при ошибке.
    Дерево используется только для проверки синтаксиса и не нормализуется.
    """
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError, UnicodeError, RecursionError) as exc:
        raise InvalidSource(f"Некорректный Python: {exc}") from exc


def source_tokens(source: str) -> tuple[LocatedToken, ...]:
    """Получить значимые токены, сохранив литералы и имена.

    Вход: source — Python-код. Выход: tuple токенов с координатами оригинала;
    некорректный исходник вызывает InvalidSource.
    """
    parse_source(source)
    result = []
    try:
        for item in tokenize.generate_tokens(io.StringIO(source, newline=None).readline):
            if item.type in SKIP_TOKENS:
                continue
            if item.type == tokenize.ERRORTOKEN:
                raise InvalidSource("Недопустимый токен")
            result.append(LocatedToken(
                (tokenize.tok_name[item.type], item.string),
                item.start[0], item.end[0],
            ))
    except (SyntaxError, tokenize.TokenError, UnicodeError) as exc:
        raise InvalidSource(f"Ошибка токенизации: {exc}") from exc
    return tuple(result)


def token_occurrences(
    source: str, n: int = 3, excluded: tuple[tuple[int, int], ...] = (),
) -> tuple[FeatureOccurrence, ...]:
    """Построить окна до исключения шаблона, не создавая ложных соседств.

    Вход: source — код; n — положительная длина окна; excluded — исходные строки.
    Выход: вхождения токеновых n-грамм, не пересекающих исключения.
    """
    if type(n) is not int or n < 1:
        raise ValueError("n должен быть положительным целым числом")
    tokens = source_tokens(source)
    result = []
    for index in range(max(0, len(tokens) - n + 1)):
        window = tokens[index:index + n]
        start, end = window[0].start_line, window[-1].end_line
        if not overlaps(start, end, excluded):
            feature = canonical_json([item.value for item in window])
            result.append(FeatureOccurrence(feature, start, end))
    return tuple(result)


def configuration_payload(assignment: Assignment, config: SimilarityConfig) -> dict[str, object]:
    """Вход: задание и настройки. Выход: все версии и правила для хеша конфигурации."""
    return {
        "assignment": asdict(assignment), "config": asdict(config),
        "method_version": config.method_version,
        "template_policy": "exact-physical-lines-overlapping-ranges-v2",
        "python_ast_version": None,
    }


def prepare_submission(
    submission: Submission, assignment: Assignment, config: SimilarityConfig,
) -> PreparedSubmission:
    """Один раз извлечь признаки и исходные координаты принятой сдачи.

    Вход: submission — версия ЛР1; assignment — её задание; config — настройки.
    Выход: PreparedSubmission; ошибки исходного Python записаны в status/reason.
    Исходник не исполняется и не изменяется.
    """
    if submission.assignment_id != assignment.assignment_id:
        raise ValueError("Контекст относится к другому заданию")
    ranges = template_ranges(submission.code, assignment.allowed_template) if config.exclude_template else ()
    excluded = tuple(Evidence(submission.submission_id, "code", first, last) for first, last in ranges)
    occurrences: tuple[FeatureOccurrence, ...] = ()
    status, reason = "ok", None
    try:
        occurrences = token_occurrences(submission.code, config.n, ranges)
    except InvalidSource as exc:
        status, reason = "invalid_source", str(exc)
    return PreparedSubmission(
        submission_id=submission.submission_id,
        student_id=submission.student_id,
        assignment_id=submission.assignment_id,
        code=submission.code,
        submission_hash=submission_hash(submission),
        config_hash=stable_hash(configuration_payload(assignment, config)),
        config=config,
        features=frozenset(item.feature for item in occurrences),
        occurrences=occurrences,
        excluded_template=excluded,
        status=status,
        reason=reason,
    )
