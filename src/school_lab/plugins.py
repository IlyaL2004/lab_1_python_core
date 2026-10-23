"""Обнаружение установленных анализаторов через entry points Python."""
from importlib.metadata import entry_points

from .errors import AnalysisFailure
from .services import AnalyzerRegistry

PLUGIN_GROUP = "school_lab.analyzers"
PLUGIN_API_VERSION = 1


def load_plugins(registry: AnalyzerRegistry) -> tuple[str, ...]:
    """Найти установленные плагины и добавить их фабрики в реестр.

    Вход: registry — реестр анализаторов; плагины берутся из группы PLUGIN_GROUP.
    Выход: кортеж загруженных имён в алфавитном порядке; () при отсутствии плагинов.
    Фабрика должна иметь целочисленный plugin_api_version=1 и создавать Analyzer.
    Ошибки обнаружения, импорта и контракта вызывают AnalysisFailure.
    После ошибки частично заполненный реестр не используют.
    entry.load() исполняет код плагина, поэтому подключают доверенные пакеты.
    """
    try:
        entries = sorted(entry_points(group=PLUGIN_GROUP), key=lambda entry: entry.name)
    except Exception as error:
        raise AnalysisFailure(f"Не удалось найти плагины: {error}") from error

    loaded = []
    for entry in entries:
        try:
            factory = entry.load()
            api_version = getattr(factory, "plugin_api_version", None)
            if type(api_version) is not int or api_version != PLUGIN_API_VERSION:
                raise AnalysisFailure("Поддерживается только plugin API 1 (целое число)")
            registry.register(entry.name, factory)
            registry.create(entry.name)
        except Exception as error:
            raise AnalysisFailure(f"Не удалось загрузить плагин {entry.name}: {error}") from error
        loaded.append(entry.name)
    return tuple(loaded)
