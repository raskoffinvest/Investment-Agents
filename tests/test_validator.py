import pytest

from invagents.agents.tank import validate_item
from invagents.validator.rules import find_forbidden

SOURCE_TITLE = "Nasdaq falls 4.5% in worst week since June"
SOURCE_TEXT = "The Nasdaq Composite fell 4.5% this week. The index dropped to 18 from 46."


def ok_item(**kw):
    item = {
        "id": "n1",
        "relevance": 8,
        "title_tr": "Nasdaq haftayı %4,5 düşüşle kapattı",
        "summary_tr": "Nasdaq Composite bu hafta %4,5 geriledi. Endeks 46'dan 18'e indi.",
    }
    item.update(kw)
    return item


def test_valid_item_passes():
    assert validate_item(ok_item(), SOURCE_TITLE, SOURCE_TEXT, 320) == []


@pytest.mark.parametrize(
    "word",
    ["beklenebilir", "olabilir", "işaret ediyor", "sert", "güçlü", "endişe verici", "muhtemelen"],
)
def test_owner_forbidden_words_rejected(word):
    item = ok_item(summary_tr=f"Nasdaq bu hafta %4,5 geriledi ve bu {word} bir durum.")
    errs = validate_item(item, SOURCE_TITLE, SOURCE_TEXT, 320)
    assert any(e.startswith("forbidden wording") for e in errs), errs


@pytest.mark.parametrize(
    "text",
    [
        "Yatırımcılar hisse satmalı.",
        "Bu bir alım fırsatı.",
        "Analist hedef fiyatı 30 dolar.",
        "Uzmanlar tavsiye ediyor.",
        "Endeks yükselecek.",
        "Analysts say buy the dip.",
    ],
)
def test_advice_and_prediction_rejected(text):
    assert find_forbidden(text), text


@pytest.mark.parametrize(
    "text",
    [
        "Fed kararı 22 Ekim'de açıklanacak.",
        "Endeks bir haftada 46'dan 18'e indi.",
        "Bankaların bilançoları beklentilerin üzerinde geldi.",
        "Sertifika programı başladı.",
    ],
)
def test_factual_sentences_allowed(text):
    assert find_forbidden(text) == []


def test_cut_off_summary_rejected():
    item = ok_item(summary_tr="Nasdaq bu hafta %4,5 geriledi ve endeks 46'dan")
    errs = validate_item(item, SOURCE_TITLE, SOURCE_TEXT, 320)
    assert "summary_tr does not end with a complete sentence" in errs


def test_invented_number_rejected():
    item = ok_item(summary_tr="Nasdaq bu hafta %4,5 geriledi, 2008'den beri en kötü hafta.")
    errs = validate_item(item, SOURCE_TITLE, SOURCE_TEXT, 320)
    assert any("numbers not in the source item: 2008" in e for e in errs)


def test_too_long_and_too_many_sentences_rejected():
    long_summary = "Nasdaq geriledi. Endeks indi. Bankalar yükseldi."
    errs = validate_item(ok_item(summary_tr=long_summary), SOURCE_TITLE, SOURCE_TEXT, 30)
    assert any("longer than 30" in e for e in errs)
    assert any("more than 2 sentences" in e for e in errs)


def test_bad_relevance_and_links_rejected():
    item = ok_item(relevance=11, title_tr="Bkz. https://x.com")
    errs = validate_item(item, SOURCE_TITLE, SOURCE_TEXT, 320)
    assert "relevance must be an integer 0-10" in errs
    assert "no HTML or links in title_tr/summary_tr" in errs
