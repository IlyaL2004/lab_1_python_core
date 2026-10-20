# Входные и выходные данные

## Модели

| Класс | Вход конструктора | Выход |
|---|---|---|
| `AIDeclaration` | `used`, `verified_by_student` — bool; `tool`, `purpose` — строка либо None | Неизменяемая декларация |
| `Submission` | Версия схемы, идентификаторы, время, text/code, template_id и AIDeclaration | Неизменяемая версия сдачи |

Конструкторы моделей не выполняют проверки схемы. Внешние данные проверяет `parse_submission`. Полный список полей виден в `models.py`.

## Разбор и валидация

| Функция | Вход | Выход |
|---|---|---|
| `decode_json(line)` | Строка JSON | Разобранный Python-объект |
| `parse_submission(raw)` | Объект, ожидается словарь полей сдачи | Проверенная `Submission` |
| `require_object(raw, name)` | Значение и имя поля для ошибки | Исходный словарь со строковыми ключами |
| `require_fields(raw, expected, prefix="")` | Словарь, набор допустимых полей, префикс имени | `None`, если поля соответствуют схеме |
| `require_string(value, name, nonempty=True)` | Значение, имя поля, флаг проверки пустоты | Исходная строка |
| `optional_string(value, name)` | Значение и имя поля | Непустая строка либо `None` |
| `require_bool(value, name)` | Значение и имя поля | Исходный `bool` |
| `validate_timestamp(value)` | Значение даты и времени | Исходная строка ISO с часовым поясом |
| `reject_repeated_keys(pairs)` | Список пар ключ–значение из JSON | Словарь без повторяющихся ключей |
| `reject_constant(value)` | Строка NaN, Infinity или -Infinity | Не возвращает значение: вызывает ошибку |

При нарушении правил вызывается `SubmissionError`. Parser не обрезает строки, не преобразует неверные типы и не изменяет входные данные. Разбор JSON отделён от проверки схемы: например, строка `[]` является корректным JSON, но не сдачей.

## Ошибки и пример запуска

| Объект | Вход | Выход |
|---|---|---|
| `SubmissionError(code, field, message)` | Три строки: код, поле, описание | Объект исключения |
| `error.to_dict()` | Данные текущей ошибки | Словарь с ключами code, field, message |
| `examples/validate_one.py:main()` | Файл `data/submission.json` | Код 0 или 1; JSON результата выводится в терминал |

Коды: `invalid_json` — ошибка формата; `schema_error` — нарушение схемы; `unsupported_version` — неподдерживаемая версия; `conflict` — занятой ID с другим содержимым. Подробные docstrings можно посмотреть через `help(parse_submission)` после импорта функции.

## Хеширование

| Функция | Вход | Выход |
|---|---|---|
| `canonical_json(value)` | JSON-совместимый объект | Строка JSON с устойчивым порядком ключей |
| `stable_hash(value)` | JSON-совместимый объект | SHA-256 в виде строки из 64 шестнадцатеричных символов |
| `submission_hash(value)` | Проверенная `Submission` | SHA-256 всех полей сдачи |

В хеш входят исходные строки времени, текста и кода. Порядок ключей не важен; изменение любого поля модели меняет сериализуемые данные. Встроенный `hash()` не используется для устойчивого отпечатка между процессами.

## Репозиторий и сервис приёма

`SubmissionRepository` — протокол с методами `get` и `add`. Сервис получает репозиторий через конструктор; общего глобального хранилища нет.

| Класс или метод | Вход | Выход |
|---|---|---|
| `InMemorySubmissionRepository()` | Без аргументов | Пустое хранилище в памяти |
| `repository.get(submission_id)` | ID сдачи — строка | Сохранённая `Submission` либо `None` |
| `repository.add(submission)` | Проверенная `Submission` | `None`; запись сохранена, занятый ID вызывает `conflict` |
| `len(repository)` | Репозиторий | Количество сохранённых сдач |
| `SubmissionService(repository)` | Объект с контрактом `SubmissionRepository` | Сервис приёма |
| `service.accept(raw)` | Объект по схеме сдачи | `Acceptance`: status, submission_id, content_hash |
| `Acceptance(status, submission_id, content_hash)` | Статус accepted/duplicate, ID и SHA-256 | Неизменяемая квитанция приёма |

Повтор с тем же ID и хешем возвращает `duplicate`, сохраняя прежний объект в репозитории. Другое содержимое с занятым ID вызывает `SubmissionError`; для новой версии требуется новый ID. Неверный вход проверяется до изменения хранилища.

## Файл и командная строка

| Класс или функция | Вход | Выход |
|---|---|---|
| `ImportSummary()` | Без аргументов; можно передать списки результатов | Отдельные списки accepted, duplicate, errors |
| `summary.to_dict()` | Данные текущего итога | Новый словарь с тремя списками для JSON |
| `import_file(path, service)` | `Path` к UTF-8 JSONL и `SubmissionService` | `ImportSummary`; ошибки записей содержат line_no, code, field, message |
| `cli.main(argv=None)` | Аргументы, например `["import", "data/submissions.jsonl"]` | Код 0/1/2 и JSON в stdout либо stderr |

`import_file` продолжает чтение после ошибочной записи. Файл закрывается через `with`; `OSError` и ошибки кодировки передаются вызывающему коду. CLI преобразует их в `source_unavailable` и возвращает 2. Ошибки отдельных записей дают код 1, успех — 0. Неверные аргументы обрабатывает `argparse` через `SystemExit(2)`.

В одном процессе можно повторно использовать тот же сервис:

```python
from pathlib import Path
from school_lab.repositories import InMemorySubmissionRepository
from school_lab.services import SubmissionService
from school_lab.sources import import_file

repository = InMemorySubmissionRepository()
service = SubmissionService(repository)
path = Path("data/submissions.jsonl")

first = import_file(path, service)   # Две записи accepted.
second = import_file(path, service)  # Те же две записи duplicate.
print(len(repository))             # 2
```
