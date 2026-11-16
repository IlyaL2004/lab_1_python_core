"""Фиксированные примеры метрик, отказов и выбора порога по validation."""
import pytest

from school_lab.evaluation import choose_threshold, metrics


def test_metrics_match_manual_matrix_with_positive_and_negative_abstentions():
    """Отказы не входят в матрицу определённых оценок и остаются в общих показателях."""
    rows = [
        (True, .9), (True, .8), (True, .2),
        (False, .7), (False, .1), (False, 0),
        (True, None), (False, None),
    ]
    assert metrics(rows, .5) == {
        "tp": 2, "fp": 1, "fn": 1, "tn": 2,
        "total_pairs": 8, "defined_pairs": 6,
        "precision": pytest.approx(2 / 3),
        "recall_defined": pytest.approx(2 / 3),
        "f1_defined": pytest.approx(2 / 3),
        "abstentions": 2, "abstention_rate": .25,
        "positive_abstentions": 1, "recall_all_positives": .5,
    }


def test_empty_metrics_keep_zero_counts_and_undefined_ratios():
    """Пустая выборка не создаёт точность или полноту из нулевых знаменателей."""
    assert metrics([], .5) == {
        "tp": 0, "fp": 0, "fn": 0, "tn": 0,
        "total_pairs": 0, "defined_pairs": 0,
        "precision": None, "recall_defined": None, "f1_defined": None,
        "abstentions": 0, "abstention_rate": None,
        "positive_abstentions": 0, "recall_all_positives": None,
    }


@pytest.mark.parametrize("rows,precision,recall,f1,recall_all", [
    ([(False, .1)], None, None, None, None),
    ([(True, .1)], None, 0, 0, 0),
    ([(False, .9)], 0, None, 0, None),
    ([(True, .9)], 1, 1, 1, 1),
    ([(True, None)], None, None, None, 0),
])
def test_zero_denominators_are_distinct_from_zero_quality(rows, precision, recall, f1, recall_all):
    """Неопределённая метрика отличается от определённого нулевого результата."""
    result = metrics(rows, .5)
    assert (result["precision"], result["recall_defined"], result["f1_defined"], result["recall_all_positives"]) == (
        precision, recall, f1, recall_all,
    )


def test_threshold_is_inclusive_for_both_classes():
    """Равенство score порогу даёт сигнал для положительной и отрицательной метки."""
    result = metrics([(True, .5), (False, .5), (True, .49), (False, .49)], .5)
    assert {key: result[key] for key in ("tp", "fp", "fn", "tn")} == {
        "tp": 1, "fp": 1, "fn": 1, "tn": 1,
    }


@pytest.mark.parametrize("threshold", [0, 1])
def test_integer_boundaries_are_valid_scores_and_thresholds(threshold):
    """Целые 0 и 1 допустимы; запрет bool не должен запрещать обычные числа."""
    result = metrics([(True, threshold), (False, threshold)], threshold)
    assert result["tp"] == result["fp"] == 1


def test_threshold_above_one_disables_all_signals():
    """Служебный порог выше 1 оставляет даже максимальные оценки без сигнала."""
    result = metrics([(True, 1), (False, 1), (True, None)], 1.000001)
    assert result["tp"] == result["fp"] == 0
    assert result["fn"] == result["tn"] == 1
    assert result["positive_abstentions"] == 1
    assert result["recall_all_positives"] == 0


@pytest.mark.parametrize("score", [
    True, False, -.01, 1.01, float("nan"), float("inf"), -float("inf"),
    "0.5", [], {}, pytest.param(10 ** 1000, id="huge-integer"),
])
def test_invalid_scores_are_rejected_before_threshold_selection(score):
    """Оба публичных API отклоняют bool, нечисловые и неконечные score."""
    with pytest.raises(ValueError, match="Score"):
        metrics([(True, score)], .5)
    with pytest.raises(ValueError, match="Score"):
        choose_threshold([(True, score)])


@pytest.mark.parametrize("threshold", [
    True, False, -.01, 1.1, float("nan"), float("inf"), -float("inf"),
    None, "0.5", pytest.param(10 ** 1000, id="huge-integer"),
])
def test_invalid_thresholds_are_rejected_even_for_empty_rows(threshold):
    """Пустая выборка не отменяет строгую проверку типа и диапазона порога."""
    with pytest.raises(ValueError, match="Порог"):
        metrics([], threshold)


@pytest.mark.parametrize("label", [0, 1, -1, None, "true", "", [], {}])
@pytest.mark.parametrize("score", [.5, None])
def test_labels_must_be_bool_including_abstentions(label, score):
    """Положительную метку нельзя подменить числом или строкой даже при отказе метода."""
    with pytest.raises(ValueError, match="Метка"):
        metrics([(label, score)], .5)
    with pytest.raises(ValueError, match="Метка"):
        choose_threshold([(label, score)])


def test_threshold_selection_maximizes_f1_instead_of_precision():
    """Порог с лучшим F1 выбирается даже при меньшей precision."""
    threshold, result = choose_threshold([(True, .2), (True, .8), (False, .5)])
    assert threshold == .2
    assert result["f1_defined"] == pytest.approx(.8)
    assert result["precision"] == pytest.approx(2 / 3)
    assert result["recall_defined"] == 1


def test_threshold_selection_uses_larger_observed_score_when_f1_ties():
    """При одинаковом F1 разных матриц выбран больший порог, вход не меняется."""
    rows = [(True, .2), (False, .3), (False, .4), (True, .8)]
    before = list(rows)
    threshold, result = choose_threshold(rows)
    assert threshold == .8
    assert result["f1_defined"] == pytest.approx(2 / 3)
    assert (result["tp"], result["fp"], result["fn"], result["tn"]) == (1, 0, 1, 2)
    assert rows == before


def test_threshold_selection_without_positives_avoids_false_signals():
    """Без положительных меток неопределённый F1 равен нулевому при выборе порога."""
    threshold, result = choose_threshold([(False, .2), (False, .9)])
    assert threshold == 1.000001
    assert (result["fp"], result["tn"]) == (0, 2)
    assert result["f1_defined"] is None
    assert result["recall_defined"] is None and result["recall_all_positives"] is None


def test_threshold_selection_with_only_abstentions_uses_no_signal_sentinel():
    """При всех score=None оба кандидата имеют неопределённый F1; выбирается больший."""
    threshold, result = choose_threshold([(True, None), (False, None)])
    assert threshold == 1.000001
    assert result["defined_pairs"] == 0 and result["f1_defined"] is None
    assert result["abstentions"] == 2 and result["abstention_rate"] == 1
    assert result["positive_abstentions"] == 1 and result["recall_all_positives"] == 0


def test_threshold_selection_requires_validation_rows():
    """Без validation-пар порог не подбирается."""
    with pytest.raises(ValueError, match="validation"):
        choose_threshold([])
