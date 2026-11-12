# Входные и выходные данные

API завершённой ЛР1 сохраняется. Ниже добавлены функции ЛР2 по основному Python Core; архив документации ЛР1 находится в [lab1/API.md](lab1/API.md). Версия пакета — 1.0.0, формат `Submission` — версия 1.

## ЛР2 поток, происхождение и разметка

| Функция или модель | Вход | Выход |
|---|---|---|
| `pipeline.iter_submissions(path, errors=None)` | Путь к UTF-8 JSONL и необязательный список ошибок | Ленивый итератор проверенных `Submission`; одинаковые ID/хеши пропускаются |
| `pipeline.file_sha256(path)` | Путь к файлу | SHA-256 как 64 шестнадцатеричных символа; файл читается блоками по 64 КиБ |
| `source_text.physical_lines(source)` | Исходная строка | `list[str]` без разделителей CRLF, CR и LF; конечный перевод строки не добавляет пустую строку |
| `evaluation.validate_split(submissions, provenance, split)` | Сдачи, происхождение по ID, списки ID `validation`/`test` | Словарь ID → часть; несогласованность вызывает `ValueError` |
| `evaluation.validate_pairs(rows, submissions, provenance, split)` | Явные записи разметки и проверяемый корпус | Список `LabelledPair`; несогласованность вызывает `ValueError` |
| `evaluation.LabelledPair(...)` | `left_id`, `right_id`, `split`, bool-метки `derivation_relation`/`template_only`, строка `explanation` | Неизменяемая разметка пары |
| `scripts/check_lab2_corpus.py` | Сохранённый корпус из `data/lab2/` | JSON проверки в stdout; прежние файлы результатов не изменяются |
| `scripts/build_lab2_corpus.py` | Необязательный `--output`; по умолчанию `data/lab2/` | Файлы фиксированного искусственного корпуса и статистика в stdout; существующие файлы корпуса перезаписываются |

У `iter_submissions` ошибки JSON, схемы и конфликтов при переданном `errors` добавляются в этот список с `line_no`, после чего чтение продолжается. Без списка первая `SubmissionError` передаётся вызывающему коду. Пустая физическая строка JSONL — ошибка формата. Ошибки файла и кодировки не скрываются. Генератор хранит ID и хеши принятых записей, но не весь исходный корпус. При раннем прекращении обхода вызывающий код закрывает генератор:

```python
from contextlib import closing
from pathlib import Path
from school_lab.pipeline import iter_submissions

with closing(iter_submissions(Path("data/lab2/submissions.jsonl"))) as rows:
    first = next(rows)
```

`validate_split` требует полное непересекающееся разбиение, непустые семейства, допустимых родителей без циклов и размещение каждого семейства только в одной части. `validate_pairs` дополнительно проверяет разные сдачи и студентов одного задания, отсутствие повторов и межчастных пар, строгие bool-метки и объяснение происхождения. В этом искусственном корпусе положительная `derivation_relation` соответствует общему семейству; `template_only` не может быть положительной производной парой.

`physical_lines` возвращает `[]` для пустого текста. Form feed и Unicode-разделители внутри строки не создают новых исходных строк. Эта политика используется анализаторами и проверкой evidence. Непарные Unicode-суррогаты отклоняются parser как ошибка поля; корректные строки сохраняются без нормализации. JSON CLI выводится с ASCII-экранированием, а `json.loads` восстанавливает исходные Unicode-значения.

## Токеновые признаки и сравнение

| Функция или модель | Вход | Выход |
|---|---|---|
| `SimilarityConfig(...)` | `method="token"`, `n=3`, флаг исключения шаблона, предел evidence и версия | Проверенные неизменяемые настройки; неверные значения вызывают `ValueError` |
| `features.source_tokens(source)` | Python-код | Кортеж значимых токенов с исходными строками; неверный синтаксис вызывает `InvalidSource` |
| `features.token_occurrences(source, n=3, excluded=())` | Код, размер окна, диапазоны шаблона | Вхождения n-грамм, не пересекающие исключённые строки |
| `features.prepare_submission(submission, assignment, config)` | Принятая сдача, её задание, настройки | `PreparedSubmission`: хеши, признаки, вхождения, шаблон, статус и причина |
| `pipeline.iter_features(path, assignments, config, errors=None)` | JSONL, задания по ID, настройки, список ошибок | Ленивый итератор `PreparedSubmission`; неизвестное задание вызывает `SubmissionError` |
| `similarity.jaccard(left, right)` | Два множества | Число 0–1 либо `None` при пустом объединении |
| `similarity.eligible(left, right)` | Две сдачи или подготовленные записи | `bool`: разные ID и студенты одного задания |
| `similarity.compare_submissions(left, right, assignment, config, *, threshold=0.5)` | Две сдачи, задание, настройки, порог | Проверенный `PairResult`; признаки извлекаются при каждом вызове |
| `similarity.compare_prepared(left, right, *, threshold=0.5)` | Две подготовленные записи одной конфигурации и порог | Проверенный `PairResult` без повторной токенизации |
| `similarity.top_k(query, candidates, k=5, *, threshold=0.5)` | Подготовленный запрос, кандидаты, положительный k, порог | До k допустимых пар с определённым score, сортировка по score и ID |
| `similarity.validate_pair_result(result, left, right)` | Парный отчёт и две подготовленные записи | `None` либо `ValueError` при нарушении контракта |
| `PairResult.to_dict()` | Парный отчёт | Новый JSON-совместимый словарь |

`PreparedSubmission` хранит исходный код, ID студента/работы/задания, хеши, `frozenset` признаков, кортеж `FeatureOccurrence` и исключённые диапазоны. `FeatureOccurrence` содержит значение признака и включительные границы строк с 1. `PairEvidence` связывает один общий признак с двумя прежними объектами `Evidence` ЛР1. `PairResult` включает эти основания, исключения шаблона, score, статус, причину, порог и сигнал ручного разбора.

Статусы сравнения: `ok` — определённая оценка, `insufficient_data` — два пустых множества, `invalid_source` — некорректный Python, `ineligible` — недопустимая пара. При отказе score равен `None`, совпадений evidence нет, `review_required=False`. Порог — конечное число от 0 до 1.000001; значение выше 1 позволяет отключить сигналы. Пока порог задаётся вручную. Другая конфигурация или задание вызывают ошибку контекста. В top-k разные версии чужого студента сохраняются отдельно, а все версии автора запроса исключаются.

`iter_features()` закрывают так же, как `iter_submissions()`: через `contextlib.closing` при раннем выходе. Код в поступившей работе никогда не исполняется. Проверка синтаксиса использует `ast.parse`; сам метод сходства на этом этапе только токеновый.

Команда `compare path --assignments assignments.json` принимает `--method token`, `--query ID`, `--top-k K`, `--threshold X`, `--config-version V`, `--keep-template` и `--output path.json`. JSON содержит `config`, хеш корпуса, число принятых документов и сравнений, `pairs`, `undefined_pairs`, ошибки строк. При query в `pairs` попадает top-k; без query — все допустимые пары. Код завершения: 0 — приём успешен, 1 — ошибки отдельных записей, 2 — ошибка файла или настроек. Неопределённая оценка сама по себе не является ошибкой приёма. stdout/stderr содержат ASCII-экранированный JSON; выходной файл записывается в UTF-8.

CLI также содержит прежние `import` и `analyze`. AST-сравнение и команды `evaluate`, `benchmark` добавляются позднее.

## Модели

| Класс | Вход конструктора | Выход |
|---|---|---|
| `AIDeclaration` | `used`, `verified_by_student` — bool; `tool`, `purpose` — строка либо None | Неизменяемая декларация |
| `Submission` | Версия схемы, идентификаторы, время, text/code, template_id и AIDeclaration | Неизменяемая версия сдачи |
| `Assignment(assignment_id, allowed_template, requirements)` | ID, строка шаблона, кортеж строк требований | Неизменяемые условия задания |
| `Evidence(submission_id, field, start_line, end_line)` | ID сдачи, поле text/code, включительные номера строк с 1 | Ссылка на исходный фрагмент |
| `Finding(kind, score, evidence, explanation)` | Вид наблюдения, число 0–1 либо None, кортеж ссылок, пояснение | Неизменяемое наблюдение |
| `AnalysisReport(...)` | ID отчёта и сдачи, хеш сдачи, имя и версия анализатора, хеш конфигурации, кортеж наблюдений, время и флаг проверки | Неизменяемый отчёт |

Конструкторы моделей не выполняют проверки схемы. Внешние сдачи проверяет `parse_submission`, файл заданий — `load_assignments`, готовые отчёты — `validate_report`. Полный список полей виден в `models.py`.

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
| `AnalysisFailure(message)` | Строка с описанием ошибки анализа или регистрации | Объект исключения; описание доступно через str(error) |
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

## Источники данных

`SubmissionSource` — протокол источника с методом `open()`. Контекстный менеджер возвращает итератор `SourceRecord`; проверку схемы выполняет сервис приёма, а не источник.

| Класс или метод | Вход | Выход |
|---|---|---|
| `SourceRecord(line_no, raw=None, error=None)` | Номер строки с 1; разобранные данные либо `SubmissionError` | Запись источника с данными или ошибкой |
| `SubmissionSource.open()` | Без аргументов | По контракту: контекстный менеджер итератора `SourceRecord` |
| `JSONLSource(path)` | `Path` к UTF-8 JSONL | Источник; файл ещё не открыт |
| `jsonl_source.open()` | Без аргументов | Контекстный менеджер итератора `SourceRecord` из файла |
| `InMemorySource(values)` | Список словарей сдач | Источник с глубокой копией входных данных |
| `memory_source.open()` | Без аргументов | Контекстный менеджер итератора копий записей из памяти |
| `StreamSource(stream)` | Открытый текстовый поток `TextIO`, например `StringIO` | Источник, использующий поток без передачи владения |
| `stream_source.open()` | Без аргументов | Контекстный менеджер итератора `SourceRecord` с текущей позиции потока |

Используйте `with source.open() as records:`. `JSONLSource` и `InMemorySource` при новом открытии начинают с первой записи. `JSONLSource` разбирает строки по мере обхода, сохраняет физические номера строк и помещает ошибки JSON в `record.error`. В том числе пустая строка становится ошибочной записью. `InMemorySource` копирует каждую выдаваемую запись, поэтому изменение вложенного словаря не влияет на следующий обход.

Файл закрывается при выходе из `with`, включая раннее прекращение обхода, отсутствие чтения и исключение. `OSError` и ошибки кодировки передаются вызывающему коду.

`StreamSource` читает с текущей позиции и нумерует строки каждого обхода с 1. Ошибка JSON становится записью с `error`; ошибка чтения передаётся вызывающему коду. Выход из контекста завершает итератор, но не закрывает и не перематывает поток. Владелец закрывает поток самостоятельно и вызывает `seek(0)` для повторного чтения. Для неперематываемого потока повтор с начала невозможен.

## Импорт и командная строка

| Класс или функция | Вход | Выход |
|---|---|---|
| `ImportSummary()` | Без аргументов; можно передать списки результатов | Отдельные списки accepted, duplicate, errors |
| `summary.to_dict()` | Данные текущего итога | Новый словарь с тремя списками для JSON |
| `import_source(source, service)` | Источник с контрактом `SubmissionSource` и `SubmissionService` | `ImportSummary`; ошибки записей содержат line_no, code, field, message |
| `import_file(path, service)` | `Path` к UTF-8 JSONL и `SubmissionService` | `ImportSummary`; ошибки записей содержат line_no, code, field, message |
| `cli.main(argv=None)` | Аргументы, например `["import", "data/submissions.jsonl"]` | Код 0/1/2 и JSON в stdout либо stderr |

`import_source` собирает `record.error` и `SubmissionError` сервиса по отдельным записям, продолжая обход. `import_file` — совместимая обёртка над `import_source(JSONLSource(path), service)`. Системные ошибки и ошибки кодировки прерывают импорт без отката уже принятых сдач. CLI преобразует их в `source_unavailable` и возвращает 2. Ошибки отдельных записей дают код 1, успех — 0. Неверные аргументы обрабатывает `argparse` через `SystemExit(2)`.

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

Для замены источника достаточно изменить его создание:

```python
from school_lab.sources import InMemorySource, JSONLSource, import_source

source = JSONLSource(Path("data/submissions.jsonl"))
# Другой вариант: source = InMemorySource([raw_submission])
summary = import_source(source, service)
print(summary.to_dict())
```

Здесь `raw_submission` — словарь по схеме сдачи. Полный пример с отдельными репозиториями для сравнения находится в `examples/compare_sources.py`.

## Контекст и отчёты анализа

| Класс или функция | Вход | Выход |
|---|---|---|
| `AnalysisContext(assignment, mode="baseline", config_version="1")` | `Assignment`, режим baseline/stub и версия настроек | Неизменяемый контекст запуска |
| `context.config_payload()` | Без аргументов; поля контекста | Новый JSON-совместимый словарь задания, режима и версии |
| `make_report(value, ctx, analyzer_id, version, findings, *, review_required=False)` | `Submission`, `AnalysisContext`, имя и версия анализатора, кортеж `Finding`, флаг проверки | Созданный и проверенный `AnalysisReport` |
| `validate_report(report, value, ctx)` | Отчёт, исходная сдача и контекст | `None` при успехе; иначе `AnalysisFailure` |

`make_report` автоматически создаёт `report_id` через UUID, время `created_at` в UTC, SHA-256 сдачи и конфигурации. `config_hash` учитывает задание, режим и версию настроек. На одинаковом входе хеши сохраняются; ID и время нового отчёта создаются заново. Требования задания сохраняются в контексте и участвуют в хеше, но произвольные требования встроенные анализаторы автоматически не проверяют.

Допустимые `Finding.kind`: `similarity`, `rubric_feedback`, `processing_error`. `score=None` означает отсутствие числовой оценки. Число должно быть конечным и находиться в диапазоне 0–1; `bool` не подходит. `explanation` не может быть пустым. `findings` и `evidence` хранятся в кортежах. Проверка отчёта связывает его с конкретной сдачей и конфигурацией и проверяет ссылки на существующие строки `text`/`code`.

## Анализаторы

`Analyzer` — `Protocol` с атрибутами `analyzer_id`, `analyzer_version` и методом `analyze`. Наследоваться от протокола не требуется. У встроенных анализаторов конструкторы без аргументов; все они возвращают наблюдения со `score=None`, не изменяя сдачу.

| Класс или метод | Вход | Выход |
|---|---|---|
| `Analyzer.analyze(submission, context)` | `Submission` и `AnalysisContext` | По контракту: `AnalysisReport` |
| `LengthAnalyzer().analyze(submission, context)` | Сдача и контекст | Отчёт с количеством непустых строк и ссылками на них |
| `TemplateAnalyzer().analyze(submission, context)` | Сдача и контекст с разрешённым шаблоном | Отчёт с диапазонами точных совпадений строк кода |
| `DeclarationAnalyzer().analyze(submission, context)` | Сдача и контекст | Отчёт о декларации; review_required=True при used=True и verified_by_student=False |
| `MissingExplanationAnalyzer().analyze(submission, context)` | Сдача и контекст | Отчёт об отсутствии пояснения; review_required=True при непустом коде и пустом тексте |
| `nonempty_line_numbers(text)` | Многострочная строка | Кортеж номеров непустых строк с 1 |
| `template_ranges(source_code, template)` | Строка кода и строка шаблона | Кортеж включительных диапазонов совпадений; пустой шаблон даёт () |

Имена для реестра: `length`, `template`, `declaration`, `missing-explanation`. Номера `Evidence` относятся к исходному содержимому, включая пустые строки при нумерации. `template_ranges` сравнивает строки точно, допускает пересекающиеся совпадения и не ищет нечёткое сходство. Анализаторы не определяют авторство и не назначают санкции автоматически.

## Реестр, сервис анализа и сборка приложения

| Класс или метод | Вход | Выход |
|---|---|---|
| `AnalyzerRegistry()` | Без аргументов | Пустой реестр фабрик |
| `registry.register(name, factory)` | Уникальное непустое имя и вызываемая фабрика без аргументов | `None`; фабрика зарегистрирована |
| `registry.create(name)` | Зарегистрированное имя | Экземпляр `Analyzer` с проверенными именем, версией и методом analyze |
| `AnalysisService(repository, registry)` | `SubmissionRepository` и `AnalyzerRegistry` | Сервис анализа принятых сдач |
| `analysis.run(submission_id, analyzer_name, context)` | ID принятой сдачи, имя анализатора и подходящий `AnalysisContext` | Проверенный `AnalysisReport` |
| `Application(repository, submissions, analysis, registry)` | Репозиторий, сервис приёма, сервис анализа и реестр | Объект со связанными компонентами приложения |
| `build_application(*, plugins=False)` | Необязательный именованный флаг загрузки установленных плагинов | Новое `Application` с пустым репозиторием, четырьмя встроенными анализаторами и, при `plugins=True`, внешними анализаторами |

Занятое или пустое имя, невызываемая фабрика, неизвестный анализатор и нарушение его контракта вызывают `AnalysisFailure`. Имя регистрации должно совпадать с `analyzer_id`, версия — быть непустой строкой. `run` отклоняет неизвестную сдачу, контекст другого задания и отчёт другого анализатора; исключения анализатора преобразуются в `AnalysisFailure`.

Новый анализатор подключается через `app.registry.register("custom", CustomAnalyzer)`. Класс должен создавать экземпляры без аргументов и соблюдать контракт `Analyzer`; менять `AnalysisService` не требуется. Установленные пакеты подключаются через `build_application(plugins=True)`.

## Установленные плагины

Модуль `school_lab.plugins` использует `importlib.metadata`. Константа `PLUGIN_GROUP` равна `"school_lab.analyzers"`, `PLUGIN_API_VERSION` — целому числу `1`.

| Класс или функция | Вход | Выход |
|---|---|---|
| `load_plugins(registry)` | `AnalyzerRegistry`, куда нужно добавить установленные плагины | Кортеж зарегистрированных имён, отсортированный по имени; `()` при отсутствии точек входа |
| `ExternalDeclarationAnalyzer()` | Без аргументов; класс из отдельного пакета `declaration_plugin` | Анализатор `external-declaration` версии `1.0` с `plugin_api_version=1` |
| `ExternalDeclarationAnalyzer.analyze(submission, context)` | `Submission` и подходящий `AnalysisContext` | `AnalysisReport` о полноте декларации с `score=None`; необходимость проверки отмечена при недостающих `tool`/`purpose` и `used=True` |

Точка входа должна указывать на вызываемую фабрику без аргументов с атрибутом `plugin_api_version`, равным `1` типа `int`; `True` не подходит. Фабрика возвращает объект с непустыми `analyzer_id`, `analyzer_version` и вызываемым методом `analyze`. `analyzer_id` должен совпадать с именем точки входа. Внешний пакет объявляет её в `pyproject.toml`:

```toml
[project.entry-points."school_lab.analyzers"]
external-declaration = "declaration_plugin:ExternalDeclarationAnalyzer"
```

`load_plugins` вызывает `.load()` точки входа, регистрирует фабрику и создаёт экземпляр для проверки контракта. Ошибки обнаружения, импорта, версии API, создания объекта и регистрации преобразуются в `AnalysisFailure`. Занятые имена не перезаписываются. После ошибки загрузки нельзя продолжать работу с тем же частично заполненным реестром: нужно создать новый. Загрузка выполняет код пакета; плагины должны быть доверенными.

`ExternalDeclarationAnalyzer` проверяет непустые `tool` и `purpose`, если `used=True`. Он не меняет сдачу и не определяет авторство. Для данных, принятых через parser, полнота декларации уже проверена; отчёт дополнительно фиксирует её состояние и демонстрирует расширение анализа отдельным пакетом. Пакет `school-declaration-plugin` версии `1.0.0` устанавливается из `plugins/declaration` и требует `school-lab>=0.5,<2`.

## CLI анализа и загрузка заданий

| Функция | Вход | Выход |
|---|---|---|
| `parsing.load_assignments(path)` | `Path` к UTF-8 JSON со списком заданий | Словарь `dict[str, Assignment]` по assignment_id |
| `create_parser()` | Без аргументов | `ArgumentParser` для import и analyze |
| `analyze_accepted(app, summary, assignments, analyzer_names, config_version)` | Приложение, итог импорта, задания по ID, список имён анализаторов и версия настроек | Словарь со списками reports, analysis_errors и measurements |
| `cli.main(argv=None)` | Аргументы, например `["analyze", "data/submissions.jsonl", "--assignments", "data/assignments.json"]` | Код 0/1/2 и JSON результата в stdout либо ошибки запуска в stderr |
| `examples/analyze_one.py:main()` | Учебные файлы submission.json и assignments.json | `None`; JSON списка двух отчётов length и template выводится в терминал |

`load_assignments` находится в `parsing.py`; прежний импорт `school_lab.cli.load_assignments` сохранён. Функция требует точные поля `assignment_id`, `allowed_template`, `requirements`. ID непустой и не повторяется, шаблон — строка, требования — список непустых строк. Допускаются пустой шаблон и пустой список требований. Неверные JSON/схема вызывают `SubmissionError`, ошибки чтения — `OSError` или `UnicodeError`.

`analyze_accepted` обрабатывает только `summary.accepted`. Каждый анализатор даёт отдельный отчёт; дубликаты не анализируются повторно. При отсутствии задания ошибка содержит `submission_id`, `code`, `message`; при ошибке анализатора — также `analyzer_id`. Ошибка одной проверки не прерывает следующие. Каждый вызов `AnalysisService.run`, успешный или ошибочный, добавляет в `measurements` словарь с `function` и `duration_ms`. Если задание не найдено, вызова и измерения нет. Список создаётся заново при каждом вызове `analyze_accepted`. `cli.main` объединяет результат с accepted, duplicate, errors. Команда `import` не добавляет measurements.

Обязательны путь JSONL и `--assignments`. Без `--analyzer` запускаются length и template; повторяемый параметр задаёт собственный список. `--plugins` разрешает загрузку внешних анализаторов, но не меняет выбор по умолчанию. Для внешнего анализатора нужно передать оба параметра: `--plugins --analyzer external-declaration`. У команды `import` флага `--plugins` нет; её запуск не загружает плагины.

`--config-version` по умолчанию равен `1` и не может быть пустым. Успех даёт 0, ошибки отдельных строк или анализа — 1. Ошибка чтения даёт `source_unavailable`, ошибка схемы заданий, конфигурации или загрузки плагина — `configuration_error`, в обоих случаях код завершения 2. Неверные аргументы обрабатывает `argparse` через `SystemExit(2)`.

## Декоратор измерения времени

| Класс или функция | Вход | Выход |
|---|---|---|
| `Measurement(function, duration_ms)` | Имя функции — str; длительность в миллисекундах — float | Неизменяемое измерение |
| `timed(measurements)` | Список `list[Measurement]` для результатов | Типизированный декоратор синхронной функции |
| `timed(measurements)(function)` | Функция с параметрами `P` и результатом `R` | Обёртка с прежним контрактом `Callable[P, R]` |
| Вызов обёрнутой функции | Аргументы исходной функции | Тот же результат либо исходное исключение; в список добавляется одно измерение |

`ParamSpec` описывает параметры, `TypeVar` — результат. `functools.wraps` сохраняет метаданные и атрибут `__wrapped__`; `inspect.signature` по умолчанию видит сигнатуру исходной функции. При этом сама обёртка принимает `*args` и `**kwargs`. Декоратор не преобразует аргументы и не проверяет их типы во время выполнения.

Длительность считается через `perf_counter`; запись в список находится в `finally`. Глобального списка нет, поэтому вызывающий код определяет срок хранения измерений. Они не входят в `AnalysisReport`, `submission_hash` или `config_hash`. Для генераторов и асинхронных функций декоратор не измеряет выполнение их тела.

## Сквозная проверка

| Функция | Вход | Выход |
|---|---|---|
| `scripts/verify.py:main()` | Без аргументов; файлы `data/submissions.jsonl`, `data/invalid_submissions.jsonl`, `data/assignments.json` и установленный плагин | `None`; файлы `results/reports.json`, `results/verification.json` с отчётами и итогами проверки; сводка также выводится в stdout |

Скрипт импортирует основной файл дважды в один репозиторий, запускает пять анализаторов и отдельно проверяет смешанный файл. Результаты содержат SHA-256 файлов данных, количество отчётов и измерения времени; повторный запуск обновляет файлы. Ошибка основного импорта или анализа, а также нарушение ожидаемого повторного импорта вызывает `RuntimeError`.
