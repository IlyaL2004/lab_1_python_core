"""Ошибки приёма сдачи и выполнения анализа."""


class SubmissionError(Exception):
    """Ошибка приёма с кодом, именем поля и понятным сообщением."""

    def __init__(self, code: str, field: str, message: str) -> None:
        """Сохранить код ошибки, проблемное поле и описание причины.

        Вход: code, field, message (str) — код ошибки, поле и сообщение.
        Выход: None; объект ошибки инициализирован.
        """
        super().__init__(message)
        self.code = code
        self.field = field
        self.message = message

    def to_dict(self) -> dict[str, str]:
        """Вернуть поля ошибки в словаре для записи в JSON.

        Вход: без аргументов; используются данные текущей ошибки.
        Выход: dict[str, str] с ключами code, field и message.
        """
        return {"code": self.code, "field": self.field, "message": self.message}


class AnalysisFailure(Exception):
    """Ошибка настройки, запуска анализатора или проверки его отчёта.

    Вход: message (str) — описание причины ошибки.
    Выход: экземпляр исключения AnalysisFailure.
    """
