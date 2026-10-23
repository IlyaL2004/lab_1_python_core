"""Загрузка внешних анализаторов и их явное включение через CLI."""

import json
from importlib.metadata import entry_points
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from school_lab import cli, plugins
from school_lab.analyzers import LengthAnalyzer
from school_lab.application import build_application
from school_lab.errors import AnalysisFailure
from school_lab.services import AnalyzerRegistry


class SampleAnalyzer(LengthAnalyzer):
    """Тестовый анализатор с отдельным именем и поддерживаемой версией API."""

    plugin_api_version = 1
    analyzer_id = "test-plugin"


def test_loader_selects_group_and_accepts_empty_result(monkeypatch) -> None:
    """Пустая группа расширений допустима и не добавляет анализаторов."""
    groups = []

    def discover(*, group):
        groups.append(group)
        return ()

    monkeypatch.setattr(plugins, "entry_points", discover)

    assert plugins.load_plugins(AnalyzerRegistry()) == ()
    assert groups == ["school_lab.analyzers"]


def test_loader_registers_plugins_in_name_order(monkeypatch) -> None:
    """Порядок обнаружения пакетов не влияет на порядок регистрации."""
    class FirstAnalyzer(SampleAnalyzer):
        analyzer_id = "alpha"

    class LastAnalyzer(SampleAnalyzer):
        analyzer_id = "zeta"

    entries = [
        SimpleNamespace(name="zeta", load=lambda: LastAnalyzer),
        SimpleNamespace(name="alpha", load=lambda: FirstAnalyzer),
    ]
    monkeypatch.setattr(plugins, "entry_points", lambda **kwargs: entries)
    registry = AnalyzerRegistry()

    assert plugins.load_plugins(registry) == ("alpha", "zeta")
    assert isinstance(registry.create("alpha"), FirstAnalyzer)
    assert isinstance(registry.create("zeta"), LastAnalyzer)


def test_loader_wraps_discovery_failure(monkeypatch) -> None:
    """Ошибка чтения метаданных превращается в понятную ошибку настройки."""
    def discover(**kwargs):
        raise OSError("metadata unavailable")

    monkeypatch.setattr(plugins, "entry_points", discover)

    with pytest.raises(AnalysisFailure, match="metadata unavailable"):
        plugins.load_plugins(AnalyzerRegistry())


def test_loader_wraps_import_failure(monkeypatch) -> None:
    """Неудачный импорт расширения не позволяет вернуть готовое приложение."""
    def load():
        raise ImportError("dependency missing")

    entry = SimpleNamespace(name="test-plugin", load=load)
    monkeypatch.setattr(plugins, "entry_points", lambda **kwargs: [entry])

    with pytest.raises(AnalysisFailure, match="test-plugin"):
        plugins.load_plugins(AnalyzerRegistry())


@pytest.mark.parametrize("version", [None, 0, 2, True, 1.0, "1"])
def test_loader_requires_exact_supported_api_version(version, monkeypatch) -> None:
    """Версия API — целое число 1; bool, float и строки не подходят."""
    monkeypatch.setattr(SampleAnalyzer, "plugin_api_version", version)
    entry = SimpleNamespace(name="test-plugin", load=lambda: SampleAnalyzer)
    monkeypatch.setattr(plugins, "entry_points", lambda **kwargs: [entry])

    with pytest.raises(AnalysisFailure):
        plugins.load_plugins(AnalyzerRegistry())


def test_loader_requires_api_version_attribute(monkeypatch) -> None:
    """Фабрика без объявления версии API не является готовым плагином."""
    monkeypatch.delattr(SampleAnalyzer, "plugin_api_version")
    entry = SimpleNamespace(name="test-plugin", load=lambda: SampleAnalyzer)
    monkeypatch.setattr(plugins, "entry_points", lambda **kwargs: [entry])

    with pytest.raises(AnalysisFailure):
        plugins.load_plugins(AnalyzerRegistry())


@pytest.mark.parametrize("attribute,value", [
    ("analyzer_id", "another-name"),
    ("analyzer_version", " "),
    ("analyze", None),
])
def test_loader_checks_created_analyzer(attribute, value, monkeypatch) -> None:
    """Совпадение API недостаточно: проверяются ID, версия и метод analyze."""
    monkeypatch.setattr(SampleAnalyzer, attribute, value)
    entry = SimpleNamespace(name="test-plugin", load=lambda: SampleAnalyzer)
    monkeypatch.setattr(plugins, "entry_points", lambda **kwargs: [entry])

    with pytest.raises(AnalysisFailure):
        plugins.load_plugins(AnalyzerRegistry())


def test_loader_rejects_non_callable_factory(monkeypatch) -> None:
    """Entry point должен возвращать вызываемую фабрику, а не объект настроек."""
    factory = SimpleNamespace(plugin_api_version=1)
    entry = SimpleNamespace(name="test-plugin", load=lambda: factory)
    monkeypatch.setattr(plugins, "entry_points", lambda **kwargs: [entry])

    with pytest.raises(AnalysisFailure):
        plugins.load_plugins(AnalyzerRegistry())


def test_loader_checks_factory_before_analysis(monkeypatch) -> None:
    """Фабрика с ошибкой отклоняется уже при загрузке расширений."""
    def broken_factory():
        raise RuntimeError("cannot construct analyzer")

    broken_factory.plugin_api_version = 1
    entry = SimpleNamespace(name="test-plugin", load=lambda: broken_factory)
    monkeypatch.setattr(plugins, "entry_points", lambda **kwargs: [entry])

    with pytest.raises(AnalysisFailure, match="cannot construct analyzer"):
        plugins.load_plugins(AnalyzerRegistry())


@pytest.mark.parametrize("name", ["", " "])
def test_loader_rejects_blank_name(name, monkeypatch) -> None:
    """Расширение должно иметь непустое имя регистрации."""
    entry = SimpleNamespace(name=name, load=lambda: SampleAnalyzer)
    monkeypatch.setattr(plugins, "entry_points", lambda **kwargs: [entry])

    with pytest.raises(AnalysisFailure):
        plugins.load_plugins(AnalyzerRegistry())


def test_plugin_cannot_replace_builtin(monkeypatch) -> None:
    """Совпадающее имя не перезаписывает встроенный анализатор."""
    app = build_application()
    entry = SimpleNamespace(name="length", load=lambda: SampleAnalyzer)
    monkeypatch.setattr(plugins, "entry_points", lambda **kwargs: [entry])

    with pytest.raises(AnalysisFailure):
        plugins.load_plugins(app.registry)

    assert type(app.registry.create("length")) is LengthAnalyzer


def test_loader_rejects_duplicate_plugin_names(monkeypatch) -> None:
    """Два пакета не могут зарегистрировать анализатор под одним именем."""
    entry = SimpleNamespace(name="test-plugin", load=lambda: SampleAnalyzer)
    monkeypatch.setattr(plugins, "entry_points", lambda **kwargs: [entry, entry])

    with pytest.raises(AnalysisFailure):
        plugins.load_plugins(AnalyzerRegistry())


def test_plugins_are_opt_in_and_applications_are_independent(monkeypatch) -> None:
    """Загрузка в одном приложении не добавляет плагин в остальные реестры."""
    discoveries = []
    entry = SimpleNamespace(name="test-plugin", load=lambda: SampleAnalyzer)

    def discover(**kwargs):
        discoveries.append(kwargs)
        return [entry]

    monkeypatch.setattr(plugins, "entry_points", discover)
    first = build_application()
    assert discoveries == []

    enabled = build_application(plugins=True)
    second = build_application()

    assert len(discoveries) == 1
    assert isinstance(enabled.registry.create("test-plugin"), SampleAnalyzer)
    for app in (first, second):
        with pytest.raises(AnalysisFailure):
            app.registry.create("test-plugin")


def test_cli_reports_plugin_failure_before_reading_input(temp_dir, monkeypatch, capsys) -> None:
    """Ошибка загрузки даёт configuration_error в stderr до импорта сдач."""
    def load():
        raise ImportError("dependency missing")

    entry = SimpleNamespace(name="test-plugin", load=load)
    monkeypatch.setattr(plugins, "entry_points", lambda **kwargs: [entry])

    result = cli.main([
        "analyze", str(temp_dir / "absent.jsonl"),
        "--assignments", str(temp_dir / "absent.json"), "--plugins",
    ])
    captured = capsys.readouterr()

    assert result == 2
    assert captured.out == ""
    error = json.loads(captured.err)
    assert error["code"] == "configuration_error"
    assert "test-plugin" in error["message"]


def test_import_does_not_discover_plugins(temp_dir, raw_submission, monkeypatch, capsys) -> None:
    """Команда приёма работает независимо от установленных расширений анализа."""
    def discover(**kwargs):
        pytest.fail("import must not discover plugins")

    monkeypatch.setattr(plugins, "entry_points", discover)
    path = temp_dir / "submission.jsonl"
    path.write_text(json.dumps(raw_submission) + "\n", encoding="utf-8")

    assert cli.main(["import", str(path)]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out)["accepted"] == ["submission-1"]


@pytest.mark.parametrize("options,returncode,analyzer_ids", [
    (["--plugins", "--analyzer", "external-declaration"], 0, ["external-declaration"]),
    (["--analyzer", "external-declaration"], 1, []),
    (["--plugins"], 0, ["length", "template"]),
])
def test_installed_plugin_in_separate_process(
    temp_dir: Path, raw_submission: dict, options, returncode, analyzer_ids,
) -> None:
    """Установленный entry point доступен с --plugins; выбор анализаторов остаётся явным."""
    installed = entry_points(group="school_lab.analyzers")
    assert any(entry.name == "external-declaration" for entry in installed), (
        "Установите учебный плагин перед проверкой: python -m pip install ./plugins/declaration"
    )
    submissions = temp_dir / "submissions.jsonl"
    assignments = temp_dir / "assignments.json"
    submissions.write_text(json.dumps(raw_submission) + "\n", encoding="utf-8")
    assignments.write_text(json.dumps([{
        "assignment_id": "lab-1", "allowed_template": "", "requirements": [],
    }]), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "school_lab.cli", "analyze", str(submissions),
         "--assignments", str(assignments), *options],
        capture_output=True, text=True, encoding="utf-8",
    )

    assert result.returncode == returncode, result.stderr
    assert result.stderr == ""
    output = json.loads(result.stdout)
    assert output["accepted"] == ["submission-1"]
    assert [report["analyzer_id"] for report in output["reports"]] == analyzer_ids
    if returncode == 1:
        assert len(output["analysis_errors"]) == 1
        assert output["analysis_errors"][0]["analyzer_id"] == "external-declaration"
    else:
        assert output["analysis_errors"] == []
        assert all(report["findings"] for report in output["reports"])
