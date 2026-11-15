"""Токеновые окна и нормализованные AST-поддеревья с исходными координатами."""
import ast
import io
import sys
import tokenize
from dataclasses import asdict, dataclass

from .analyzers import template_ranges
from .hashing import canonical_json, stable_hash, submission_hash
from .models import Assignment, Evidence, Submission
from .similarity_models import FeatureOccurrence, PreparedSubmission, SimilarityConfig


class InvalidSource(ValueError):
    """Вход: причина ошибки Python. Выход: исключение извлечения признаков."""


class UnsupportedAST(ValueError):
    """Вход: неподдерживаемая конструкция. Выход: явный отказ AST-метода."""


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


def _remove_template_statements(tree: ast.Module, excluded: tuple[tuple[int, int], ...]) -> None:
    """Вход: дерево и шаблон. Выход: None; полностью покрытые statements удалены."""
    class Remove(ast.NodeTransformer):
        def visit(self, node: ast.AST) -> ast.AST | None:
            """Вход: узел. Выход: узел либо None для целиком исключённого statement."""
            if isinstance(node, ast.stmt):
                first, last = node.lineno, node.end_lineno or node.lineno
                if any(start <= first and last <= end for start, end in excluded):
                    return None
            return super().visit(node)

    Remove().visit(tree)


def _normalize_function(function: ast.FunctionDef) -> None:
    """Нормализовать поддерживаемую функцию в переданном дереве.

    Вход: одна функция без вложенных областей, defaults и аннотаций.
    Выход: None; связанные имена заменены последовательно на v0, v1, ... .
    Внешние имена получают отдельную метку external:, исключающую коллизии.
    """
    nodes = list(ast.walk(function))
    forbidden = (
        ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp,
        ast.Global, ast.Nonlocal, ast.ClassDef, ast.Import, ast.ImportFrom,
        ast.AsyncFunctionDef, ast.AsyncFor, ast.AsyncWith, ast.Match,
        ast.NamedExpr, ast.TypeAlias,
    )
    if any(isinstance(node, forbidden) for node in nodes):
        raise UnsupportedAST("Вложенные области, import, async, match, :=, type и global/nonlocal не поддерживаются")
    if sum(isinstance(node, ast.FunctionDef) for node in nodes) != 1:
        raise UnsupportedAST("Вложенные функции не поддерживаются")
    if (function.decorator_list or function.returns or function.args.defaults
            or any(value is not None for value in function.args.kw_defaults)
            or getattr(function, "type_params", [])):
        raise UnsupportedAST("Декораторы, аннотации, defaults и параметры типов не поддерживаются")
    if any(isinstance(node, (ast.AnnAssign, ast.arg))
           and getattr(node, "annotation", None) is not None for node in nodes):
        raise UnsupportedAST("Аннотации не поддерживаются")
    if any(isinstance(node, ast.ExceptHandler) and node.name for node in nodes):
        raise UnsupportedAST("Связывание имени в except не поддерживается")
    local_names = {
        node.id for node in nodes
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del))
    }
    local_names.update(node.arg for node in nodes if isinstance(node, ast.arg))
    if function.name not in local_names and any(
        isinstance(node, ast.Name) and node.id == function.name for node in nodes
    ):
        raise UnsupportedAST("Рекурсия и ссылки функции на собственное имя не поддерживаются")
    mapping: dict[str, str] = {}
    # Порядок полей AST не всегда равен порядку текста: например, у Dict
    # сначала идут все keys, затем values. Поэтому порядок имён задают координаты.
    named_nodes = [node for node in nodes if isinstance(node, ast.arg)
                   or isinstance(node, ast.Name) and node.id in local_names]
    for node in sorted(named_nodes, key=lambda item: (item.lineno, item.col_offset)):
        name = node.arg if isinstance(node, ast.arg) else node.id
        if name not in mapping:
            mapping[name] = f"v{len(mapping)}"

    function.name = "function"
    for node in nodes:
        if isinstance(node, ast.arg):
            node.arg = mapping[node.arg]
        elif isinstance(node, ast.Name):
            node.id = mapping[node.id] if node.id in local_names else f"external:{node.id}"


def ast_occurrences(
    source: str, excluded: tuple[tuple[int, int], ...] = (),
) -> tuple[FeatureOccurrence, ...]:
    """Получить AST-поддеревья с координатами, сохранёнными до нормализации.

    Вход: source — код одной синхронной функции; excluded — шаблонные строки.
    Выход: вхождения поддеревьев statements, expressions и аргументов.
    Пустой код даёт (); неподдерживаемые конструкции и превышение глубины
    обработки дерева — UnsupportedAST.
    """
    tree = parse_source(source)
    try:
        positions = {
            id(node): (node.lineno, node.end_lineno or node.lineno)
            for node in ast.walk(tree)
            if hasattr(node, "lineno")
        }
        _remove_template_statements(tree, excluded)
        if not tree.body:
            return ()
        if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
            raise UnsupportedAST("AST-метод поддерживает одну синхронную функцию")
        function = tree.body[0]
        _normalize_function(function)
        result = []
        for node in ast.walk(function):
            if not isinstance(node, (ast.stmt, ast.expr, ast.arg)):
                continue
            start, end = positions[id(node)]
            if not overlaps(start, end, excluded):
                result.append(FeatureOccurrence(
                    ast.dump(node, include_attributes=False), start, end,
                ))
        return tuple(result)
    except RecursionError as exc:
        raise UnsupportedAST("Превышена поддерживаемая глубина обработки AST") from exc


def configuration_payload(assignment: Assignment, config: SimilarityConfig) -> dict[str, object]:
    """Вход: задание и настройки. Выход: все версии и правила для хеша конфигурации."""
    return {
        "assignment": asdict(assignment), "config": asdict(config),
        "method_version": config.method_version,
        "template_policy": "exact-physical-lines-overlapping-ranges-v2",
        "python_ast_version": list(sys.version_info[:3]) if config.method == "ast" else None,
    }


def prepare_submission(
    submission: Submission, assignment: Assignment, config: SimilarityConfig,
) -> PreparedSubmission:
    """Один раз извлечь признаки и исходные координаты принятой сдачи.

    Вход: submission — версия ЛР1; assignment — её задание; config — настройки.
    Выход: PreparedSubmission; SyntaxError и неподдерживаемый AST записаны
    в status/reason, исходник не исполняется и не изменяется.
    """
    if submission.assignment_id != assignment.assignment_id:
        raise ValueError("Контекст относится к другому заданию")
    ranges = template_ranges(submission.code, assignment.allowed_template) if config.exclude_template else ()
    excluded = tuple(Evidence(submission.submission_id, "code", first, last) for first, last in ranges)
    occurrences: tuple[FeatureOccurrence, ...] = ()
    status, reason = "ok", None
    try:
        if config.method == "token":
            occurrences = token_occurrences(submission.code, config.n, ranges)
        else:
            occurrences = ast_occurrences(submission.code, ranges)
    except InvalidSource as exc:
        status, reason = "invalid_source", str(exc)
    except UnsupportedAST as exc:
        status, reason = "unsupported", str(exc)
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
