"""Пример проверки одной записи из data/submission.json."""
import json
from dataclasses import asdict
from pathlib import Path

from school_lab.errors import SubmissionError
from school_lab.parsing import decode_json, parse_submission


def main() -> int:
    """Прочитать пример и вывести результат проверки в JSON.

    Вход: файл data/submission.json; аргументов нет.
    Выход: 0 при успехе, 1 при неверном JSON или схеме.
    """
    path = Path(__file__).resolve().parents[1] / "data" / "submission.json"
    text = path.read_text(encoding="utf-8")
    try:
        raw = decode_json(text)
        submission = parse_submission(raw)
    except SubmissionError as error:
        print(json.dumps(error.to_dict(), ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(asdict(submission), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
