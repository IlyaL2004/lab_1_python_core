"""Проверки происхождения, разбиения, разметки и создания корпуса."""
import json
from pathlib import Path
import runpy

import pytest

from school_lab.evaluation import validate_pairs, validate_split
from school_lab.parsing import parse_submission
from school_lab.pipeline import iter_submissions


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "lab2"


@pytest.fixture
def split_example(raw_submission):
    """Вход: шаблон сдачи. Выход: четыре сдачи двух независимых семейств."""
    documents, provenance = [], {}
    for identifier, family, parent in (("a", "first", None), ("b", "first", "a"),
                                       ("c", "second", None), ("d", "second", "c")):
        raw = {**raw_submission, "submission_id": identifier, "student_id": identifier}
        documents.append(parse_submission(raw))
        provenance[identifier] = {"family_id": family, "parent_id": parent}
    split = {"validation": ["a", "b"], "test": ["c", "d"]}
    rows = [{"left_id": left, "right_id": right, "split": part,
             "derivation_relation": True, "template_only": False, "explanation": "Общий известный корень."}
            for left, right, part in (("a", "b", "validation"), ("c", "d", "test"))]
    return documents, provenance, split, rows


def test_disjoint_family_split_is_accepted(split_example):
    documents, provenance, split, rows = split_example
    assert validate_split(documents, provenance, split) == {"a": "validation", "b": "validation", "c": "test", "d": "test"}
    assert len(validate_pairs(rows, documents, provenance, split)) == 2


@pytest.mark.parametrize("field", ["provenance", "split"])
@pytest.mark.parametrize("value", [None, [], "invalid"])
def test_split_rejects_non_object_json_roots(split_example, field, value):
    documents, provenance, split, _ = split_example
    with pytest.raises(ValueError, match="JSON-объектами"):
        validate_split(documents, value if field == "provenance" else provenance,
                       value if field == "split" else split)


@pytest.mark.parametrize("value", [None, [], "invalid"])
def test_split_rejects_non_object_provenance_records(split_example, value):
    documents, provenance, split, _ = split_example
    provenance["a"] = value
    with pytest.raises(ValueError, match="Запись provenance"):
        validate_split(documents, provenance, split)


@pytest.mark.parametrize("value", [None, [], {}, 1, True])
def test_split_rejects_non_string_document_ids(split_example, value):
    documents, provenance, split, _ = split_example
    split["validation"][0] = value
    with pytest.raises(ValueError, match="ID в split"):
        validate_split(documents, provenance, split)


def test_split_rejects_non_string_provenance_ids(split_example):
    documents, provenance, split, _ = split_example
    provenance[1] = provenance.pop("a")
    with pytest.raises(ValueError, match="ID в provenance"):
        validate_split(documents, provenance, split)


@pytest.mark.parametrize("field,value", [
    ("family_id", []), ("family_id", {}),
    ("parent_id", []), ("parent_id", {}), ("parent_id", True),
])
def test_split_rejects_invalid_provenance_fields(split_example, field, value):
    documents, provenance, split, _ = split_example
    provenance["b"][field] = value
    with pytest.raises(ValueError, match=field):
        validate_split(documents, provenance, split)


@pytest.mark.parametrize("extra_generation", [False, True])
def test_pairs_reject_parent_cycles(split_example, raw_submission, extra_generation):
    documents, provenance, split, rows = split_example
    provenance["a"]["parent_id"] = "b"
    if extra_generation:
        documents.append(parse_submission({**raw_submission, "submission_id": "e", "student_id": "e"}))
        provenance["e"] = {"family_id": "first", "parent_id": "b"}
        provenance["a"]["parent_id"] = "e"
        split["validation"].append("e")
    with pytest.raises(ValueError, match="Цикл"):
        validate_pairs(rows, documents, provenance, split)


def test_split_accepts_multigeneration_lineage_in_any_order(split_example, raw_submission):
    documents, provenance, split, rows = split_example
    documents.append(parse_submission({**raw_submission, "submission_id": "e", "student_id": "e"}))
    provenance = {"e": {"family_id": "first", "parent_id": "b"}, **provenance}
    split["validation"].insert(0, "e")
    assert validate_split(documents, provenance, split)["e"] == "validation"
    assert len(validate_pairs(rows, documents, provenance, split)) == 2


def test_split_rejects_descendant_in_test(split_example):
    documents, provenance, _, _ = split_example
    with pytest.raises(ValueError, match="Утечка"):
        validate_split(documents, provenance, {"validation": ["a", "c", "d"], "test": ["b"]})


def test_split_rejects_omitted_and_repeated_documents(split_example):
    documents, provenance, _, _ = split_example
    for split in ({"validation": ["a", "b"], "test": ["c"]},
                  {"validation": ["a", "b"], "test": ["c", "d", "a"]}):
        with pytest.raises(ValueError):
            validate_split(documents, provenance, split)


def test_split_rejects_parent_from_another_family(split_example):
    documents, provenance, split, _ = split_example
    provenance["b"]["parent_id"] = "c"
    with pytest.raises(ValueError, match="родитель"):
        validate_split(documents, provenance, split)


@pytest.mark.parametrize("change", [
    {"right_id": "c"}, {"split": "test"}, {"derivation_relation": False},
    {"derivation_relation": "true"}, {"template_only": True}, {"explanation": ""},
])
def test_pair_labels_and_both_split_endpoints_are_checked(split_example, change):
    documents, provenance, split, rows = split_example
    rows[0].update(change)
    with pytest.raises(ValueError):
        validate_pairs(rows, documents, provenance, split)


def test_reversed_duplicate_pair_is_rejected(split_example):
    documents, provenance, split, rows = split_example
    rows.append({**rows[0], "left_id": "b", "right_id": "a"})
    with pytest.raises(ValueError, match="Повтор"):
        validate_pairs(rows, documents, provenance, split)


def test_fixed_corpus_meets_minimums_without_family_leakage():
    documents = list(iter_submissions(DATA / "submissions.jsonl"))
    provenance = json.loads((DATA / "provenance.json").read_text(encoding="utf-8"))
    split = json.loads((DATA / "split.json").read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in (DATA / "pairs.jsonl").read_text(encoding="utf-8").splitlines()]
    pairs = validate_pairs(rows, documents, provenance, split)
    assert len(documents) >= 30 and len(pairs) >= 40
    assert sum(pair.template_only for pair in pairs) >= 5
    assert len({meta["family_id"] for meta in provenance.values()}) >= 12
    for part in split:
        labels = {pair.derivation_relation for pair in pairs if pair.split == part}
        assert labels == {True, False}


def test_corpus_generation_is_deterministic(temp_dir):
    builder = runpy.run_path(str(ROOT / "scripts" / "build_lab2_corpus.py"))["build_corpus"]
    counts = builder(temp_dir)
    assert counts == {"submissions": 56, "families": 24, "pairs": 132, "template_only": 12}
    for generated in temp_dir.iterdir():
        original = DATA / generated.name
        if generated.suffix == ".json":
            assert json.loads(generated.read_text(encoding="utf-8")) == json.loads(original.read_text(encoding="utf-8"))
        else:
            assert generated.read_bytes() == original.read_bytes()
