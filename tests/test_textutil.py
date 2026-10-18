from decimal import Decimal

from invagents.textutil import (
    first_sentences,
    is_terminated,
    number_values,
    numbers_not_in,
    split_sentences,
    tr_lower,
)


def test_tr_lower_handles_dotted_and_dotless_i():
    assert tr_lower("İSTANBUL IŞIK") == "istanbul ışık"


def test_split_sentences_keeps_abbreviations_together():
    text = "U.S. stocks fell on Friday. The Nasdaq lost 2.1% and Inc. results lagged. Done!"
    assert split_sentences(text) == [
        "U.S. stocks fell on Friday.",
        "The Nasdaq lost 2.1% and Inc. results lagged.",
        "Done!",
    ]


def test_first_sentences_never_cuts_mid_sentence():
    text = "First sentence is short. " + "Second sentence " * 30 + "ends here."
    out = first_sentences(text, max_chars=60)
    assert out == "First sentence is short."
    assert is_terminated(out)


def test_first_sentences_returns_empty_instead_of_cutting():
    assert first_sentences("word " * 100 + "end.", max_chars=50) == ""
    assert first_sentences("No terminal punctuation here", max_chars=200) == ""


def test_is_terminated():
    assert is_terminated("Bitti.")
    assert is_terminated('Dedi ki "tamam."')
    assert not is_terminated("Endeks bir haftada 48'den 18'e")


def test_number_values_us_and_tr_notation():
    assert Decimal("1696.2") in number_values("1.696,20")
    assert Decimal("1696.2") in number_values("1,696.20")
    assert number_values("4,5") == {Decimal("4.5")}
    assert Decimal("4.5") in number_values("4.5")


def test_numbers_not_in_accepts_reformatted_numbers():
    source = "The Nasdaq fell 4.5% to 27,044.49; index at 18 from 46."
    out = "Nasdaq %4,5 düşüşle 27.044,49 puana indi; endeks 46'dan 18'e."
    assert numbers_not_in(out, source) == []


def test_numbers_not_in_flags_invented_numbers():
    source = "The Nasdaq fell 4.5%."
    assert numbers_not_in("Nasdaq %4,5 düştü, 2026'nın en kötü haftası; 3 gün sürdü.", source) == [
        "2026",
        "3",
    ]
