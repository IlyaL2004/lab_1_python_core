"""Отдельно устанавливаемый анализатор декларации использования ИИ."""

from school_lab.models import AnalysisReport, Finding, Submission
from school_lab.reports import AnalysisContext, make_report


class ExternalDeclarationAnalyzer:
    """Проверять полноту декларации через внешний плагин с API версии 1.

    Вход: конструктор без аргументов.
    Выход: анализатор external-declaration с методом analyze.
    """

    plugin_api_version = 1
    analyzer_id = "external-declaration"
    analyzer_version = "1.0"

    def analyze(self, submission: Submission, context: AnalysisContext) -> AnalysisReport:
        """Вернуть пояснение о заполнении инструмента и цели использования ИИ.

        Вход: submission (Submission) — сдача; context (AnalysisContext) — настройки.
        Выход: AnalysisReport; нужна проверка, если ИИ заявлен, но инструмент
        или цель не заполнены. Исходная сдача не изменяется.
        """
        declaration = submission.ai_declaration
        has_tool = bool(declaration.tool and declaration.tool.strip())
        has_purpose = bool(declaration.purpose and declaration.purpose.strip())
        complete = has_tool and has_purpose
        if declaration.used:
            if complete:
                explanation = "Использование ИИ заявлено; инструмент и цель заполнены."
            else:
                explanation = "Использование ИИ заявлено, но инструмент или цель не заполнены."
        else:
            explanation = "Использование ИИ не заявлено. Это не доказывает происхождение работы."
        finding = Finding("rubric_feedback", None, (), explanation)
        review_required = declaration.used and not complete
        return make_report(
            submission, context, self.analyzer_id, self.analyzer_version,
            (finding,), review_required=review_required,
        )
