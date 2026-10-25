"""Типизированное измерение времени без изменения результата функции."""
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from time import perf_counter
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")


@dataclass(frozen=True, slots=True)
class Measurement:
    """Длительность одного вызова функции.

    Вход: function — имя функции; duration_ms — длительность в миллисекундах.
    Выход: неизменяемый объект с результатом измерения.
    """

    function: str
    duration_ms: float


def timed(measurements: list[Measurement]) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Создать декоратор, записывающий время каждого вызова в переданный список.

    Вход: measurements — список для накопления измерений.
    Выход: декоратор, сохраняющий типы аргументов и результата функции.
    Измерение записывается и при исключении; исключение передаётся дальше.
    """
    def decorate(function: Callable[P, R]) -> Callable[P, R]:
        """Вход: измеряемая функция. Выход: обёртка с её типами и метаданными."""
        @wraps(function)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            """Вход: аргументы исходной функции. Выход: её исходный результат."""
            start = perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                duration_ms = (perf_counter() - start) * 1000
                measurements.append(Measurement(function.__qualname__, duration_ms))

        return wrapped

    return decorate
