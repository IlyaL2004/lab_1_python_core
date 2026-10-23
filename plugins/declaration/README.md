# Внешний анализатор декларации ИИ

Пакет `school-declaration-plugin` подключает анализатор `external-declaration`
через группу entry points `school_lab.analyzers`. Поддерживаемая версия API — `1`.
Основной пакет не импортирует `declaration_plugin` напрямую.

Установка в Git Bash из корня `lab_1_python_core_` после установки основной программы:

```bash
./.venv/Scripts/python.exe -m pip install --upgrade ./plugins/declaration
```

`ExternalDeclarationAnalyzer.analyze(submission, context)` получает `Submission`
и `AnalysisContext`, возвращает проверенный `AnalysisReport` с хешами сдачи
и настроек. Исходные данные не изменяются. Отчёт содержит пояснение о заполнении
полей `tool` и `purpose`; флаг `review_required` включается только при `used=True`
и незаполненном инструменте или цели. `verified_by_student` этот анализатор
не проверяет — для него есть встроенный анализатор `declaration`.

Парсер основной программы уже требует инструмент и цель при `used=True`.
Поэтому для корректно принятых сдач этот плагин демонстрирует независимое
подключение расширения. Он не выявляет дополнительные нарушения и не определяет
авторство работы. В тесте неполной декларации модель намеренно создаётся напрямую,
в обход парсера.
