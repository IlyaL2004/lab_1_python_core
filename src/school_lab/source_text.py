"""Единая нумерация исходных строк для токенов, AST и evidence."""


def physical_lines(source: str) -> list[str]:
    """Вход: исходный текст. Выход: строки без разделителей CRLF, CR и LF.

    Конечный перевод строки не добавляет пустую строку. Остальные символы,
    включая form feed и Unicode-разделители внутри литералов, сохраняются.
    """
    if not source:
        return []
    lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return lines[:-1] if lines[-1] == "" else lines
