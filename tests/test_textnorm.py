import pytest

from cutroom.perception.textnorm import int_to_words, normalize, word_error_rate


@pytest.mark.parametrize("n,words", [
    (0, "zero"), (7, "seven"), (19, "nineteen"), (20, "twenty"), (42, "forty two"),
    (100, "one hundred"), (220, "two hundred twenty"), (400, "four hundred"),
    (1000, "one thousand"), (7_000_000, "seven million"), (2015, "two thousand fifteen"),
])
def test_int_to_words(n, words):
    assert int_to_words(n) == words


def test_normalize_numbers_and_punctuation():
    assert normalize("Mix 400 grams, then bake at 220 degrees!") == \
        "mix four hundred grams then bake at two hundred twenty degrees"
    assert normalize("almost 7 million people") == "almost seven million people"
    assert normalize("It's the 3rd time, 50% off, $20") == "it's the third time fifty percent off twenty dollars"
    assert normalize("1,000 people") == "one thousand people"


def test_wer_ignores_formatting():
    ref = "First, mix four hundred grams of flour with warm water."
    assert word_error_rate(ref, "first mix 400 grams of flour with warm water") == 0.0


def test_wer_counts_errors():
    assert word_error_rate("a b c d", "a x c") == pytest.approx(0.5)   # 1 sub + 1 del
    assert word_error_rate("", "") == 0.0
    assert word_error_rate("", "x") == 1.0


def test_and_in_hundreds_is_a_real_word_difference():
    # Spoken "two hundred and twenty" vs written "220": the 'and' counts as one insertion.
    assert word_error_rate("two hundred and twenty", "220") == pytest.approx(0.25)
