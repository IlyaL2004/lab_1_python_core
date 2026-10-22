"""Простые анализаторы: длина, разрешённый шаблон, пояснение и декларация ИИ."""
from .models import Submission, Evidence, Finding, AnalysisReport
from .reports import AnalysisContext, make_report
from .errors import AnalysisFailure


class MissingExplanationAnalyzer:
    """Отмечать сдачи, в которых код есть, а текстовое пояснение отсутствует.

    Вход: конструктор без аргументов.
    Выход: анализатор с методом analyze и именем missing-explanation.
    """

    analyzer_id = "missing-explanation"
    analyzer_version = "1.0"

    def analyze(self, submission: Submission, context: AnalysisContext) -> AnalysisReport:
        """Вернуть отчёт со ссылкой на код и запросить проверку при отсутствии пояснения.

        Вход: submission (Submission) — сдача; context (AnalysisContext) — задание и настройки.
        Выход: AnalysisReport; review_required=True, если код есть, а пояснения нет.
        """
        missing = not submission.text.strip() and bool(submission.code.strip())
        evidence = ()
        if missing:
            code_line_count = len(submission.code.splitlines())
            evidence = (Evidence(submission.submission_id, "code", 1, code_line_count),)
            explanation = "Код представлен без пояснения."
        else:
            explanation = "Отсутствие пояснения при непустом коде не обнаружено."
        finding = Finding("rubric_feedback", None, evidence, explanation)
        return make_report(
            submission, context, self.analyzer_id, self.analyzer_version,
            (finding,), review_required=missing,
        )


def nonempty_line_numbers(text: str) -> tuple[int, ...]:
    """Вернуть номера непустых строк text, считая с 1 и сохраняя исходные позиции.

    Вход: text (str) — многострочный текст.
    Выход: tuple[int, ...] с номерами непустых строк; () при их отсутствии.
    """
    numbers = []
    for number, line in enumerate(text.splitlines(), start=1):
        if line.strip():
            numbers.append(number)
    return tuple(numbers)


class LengthAnalyzer:
    """Подсчитывать непустые строки текста и кода без оценки правильности решения.

    Вход: конструктор без аргументов.
    Выход: анализатор с методом analyze и именем length.
    """

    analyzer_id = "length"
    analyzer_version = "1.0"

    def analyze(self, submission: Submission, context: AnalysisContext) -> AnalysisReport:
        """Вернуть количества строк и ссылки на каждую учтённую строку сдачи.

        Вход: submission (Submission) — сдача; context (AnalysisContext) — задание и настройки.
        Выход: AnalysisReport с числом непустых строк текста и кода и ссылками на них.
        """
        text_lines = nonempty_line_numbers(submission.text)
        code_lines = nonempty_line_numbers(submission.code)
        evidence = []
        for line_no in text_lines:
            evidence.append(Evidence(submission.submission_id, "text", line_no, line_no))
        for line_no in code_lines:
            evidence.append(Evidence(submission.submission_id, "code", line_no, line_no))
        finding = Finding(
            kind="rubric_feedback",
            score=None,
            evidence=tuple(evidence),
            explanation=(
                f"Непустые строки: text={len(text_lines)}, code={len(code_lines)}. "
                "Длина не оценивает корректность решения."
            ),
        )
        return make_report(submission, context, self.analyzer_id, self.analyzer_version, (finding,))


def template_ranges(source_code: str, template: str) -> tuple[tuple[int, int], ...]:
    """Найти точные вхождения строк template в source_code.

    Результат содержит пары (начальная строка, конечная строка), считая с 1.
    Пересекающиеся совпадения допускаются. Пустой шаблон не даёт совпадений.

    Вход: source_code (str) — код сдачи; template (str) — разрешённый шаблон.
    Выход: tuple[tuple[int, int], ...] с включительными границами совпадений или ().
    """
    if not template.strip():
        return ()
    source_lines = source_code.splitlines()
    template_lines = template.splitlines()
    width = len(template_lines)
    ranges = []
    for index in range(len(source_lines) - width + 1):
        if source_lines[index:index + width] == template_lines:
            ranges.append((index + 1, index + width))
    return tuple(ranges)


class TemplateAnalyzer:
    """Находить разрешённые блоки шаблона, не считая их нарушением.

    Вход: конструктор без аргументов; шаблон передаётся через AnalysisContext.
    Выход: анализатор с методом analyze и именем template.
    """

    analyzer_id = "template"
    analyzer_version = "1.0"

    def analyze(self, submission: Submission, context: AnalysisContext) -> AnalysisReport:
        """Вернуть точные совпадения с шаблоном задания и пояснение результата.

        Вызывает AnalysisFailure, если контекст относится к другому заданию.

        Вход: submission (Submission) — сдача; context (AnalysisContext) — задание с шаблоном.
        Выход: AnalysisReport с диапазонами точных совпадений и пояснением.
        """
        if submission.assignment_id != context.assignment.assignment_id:
            raise AnalysisFailure("Контекст относится к другому заданию")
        ranges = template_ranges(submission.code, context.assignment.allowed_template)
        evidence = []
        for start, end in ranges:
            evidence.append(Evidence(submission.submission_id, "code", start, end))
        if ranges:
            explanation = (
                f"Найдены разрешённые блоки шаблона: {len(ranges)}. "
                "Это допустимое совпадение, а не основание санкции."
            )
        else:
            explanation = (
                "Точное совпадение с разрешённым шаблоном не найдено. "
                "Нечёткое сходство не проверялось."
            )
        finding = Finding("similarity", None, tuple(evidence), explanation)
        return make_report(submission, context, self.analyzer_id, self.analyzer_version, (finding,))


class DeclarationAnalyzer:
    """Показывать декларацию ИИ и отмечать заявленную, но не проверенную помощь.

    Вход: конструктор без аргументов.
    Выход: анализатор с методом analyze и именем declaration.
    """

    analyzer_id = "declaration"
    analyzer_version = "1.0"

    def analyze(self, submission: Submission, context: AnalysisContext) -> AnalysisReport:
        """Вернуть описание декларации, не определяя авторство текста автоматически.

        Вход: submission (Submission) — сдача; context (AnalysisContext) — задание и настройки.
        Выход: AnalysisReport; нужна проверка, если помощь ИИ заявлена, но не проверена студентом.
        """
        declaration = submission.ai_declaration
        if declaration.used:
            explanation = (
                f"Использование ИИ заявлено. Инструмент: {declaration.tool}; "
                f"цель: {declaration.purpose}."
            )
        else:
            explanation = (
                "Использование ИИ не заявлено. "
                "Авторство текста автоматически не определялось."
            )
        finding = Finding("rubric_feedback", None, (), explanation)
        review_required = declaration.used and not declaration.verified_by_student
        return make_report(
            submission, context, self.analyzer_id, self.analyzer_version,
            (finding,), review_required=review_required,
        )
