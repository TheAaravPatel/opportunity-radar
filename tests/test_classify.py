from radar.classify import FieldClassifier, compile_phrases
from radar.models import Item


def test_whole_word_matching():
    pattern = compile_phrases(["ai", "ml"])
    assert pattern.search("AI Research Intern")
    assert not pattern.search("Maintenance Intern")       # "ai" inside "maintenance"
    assert not pattern.search("HTML developer")           # "ml" inside "html"


def test_source_fields_plus_keywords(cfg):
    classifier = FieldClassifier(cfg.keywords)
    item = Item(source_item_id="1", title="Bioinformatics Software Intern", organization="X", fields=["cs"])
    fields, method = classifier.classify(item)
    assert fields == ["biotech", "cs"]
    assert method == "source"


def test_keywords_only(cfg):
    classifier = FieldClassifier(cfg.keywords)
    fields, method = classifier.classify(Item(source_item_id="1", title="Economics Research Intern", organization="X"))
    assert fields == ["econ"] and method == "keyword"


def test_unplaced(cfg):
    classifier = FieldClassifier(cfg.keywords)
    fields, method = classifier.classify(Item(source_item_id="1", title="Summer Intern", organization="X"))
    assert fields == [] and method == "unplaced"
