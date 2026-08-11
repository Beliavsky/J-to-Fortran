from __future__ import annotations

from pathlib import Path

import pytest

from j2fortran.lexer import LexerError, TokenKind, tokenize


ROOT = Path(__file__).resolve().parents[1]


def significant(source: str) -> list[tuple[TokenKind, str]]:
    return [
        (token.kind, token.value)
        for token in tokenize(source)
        if token.kind not in {TokenKind.NEWLINE, TokenKind.EOF}
    ]


def test_assignment_and_primitive_spellings() -> None:
    assert significant('a =. 0 {"1 ab\n') == [
        (TokenKind.NAME, "a"),
        (TokenKind.COPULA, "=."),
        (TokenKind.NUMBER, "0"),
        (TokenKind.PRIMITIVE, "{"),
        (TokenKind.PRIMITIVE, '"'),
        (TokenKind.NUMBER, "1"),
        (TokenKind.NAME, "ab"),
    ]


def test_array_pipeline_uses_longest_primitive_spellings() -> None:
    values = [value for _, value in significant("keep # ab ,. <. %: sumsq")]
    assert values == ["keep", "#", "ab", ",.", "<.", "%:", "sumsq"]


def test_control_words_and_loop_names() -> None:
    tokens = significant("for_item. 1 + i. y do.\nif. y <: 3 do.\nend.")
    controls = [value for kind, value in tokens if kind is TokenKind.CONTROL]
    assert controls == ["for_item.", "do.", "if.", "do.", "end."]


def test_comments_end_at_newline_and_strings_can_contain_nb() -> None:
    tokens = tokenize("echo 'NB. isn''t a comment' NB. comment\necho 2")
    strings = [token for token in tokens if token.kind is TokenKind.STRING]
    names = [token.value for token in tokens if token.kind is TokenKind.NAME]

    assert strings[0].value == "NB. isn't a comment"
    assert names == ["echo", "echo"]
    assert sum(token.kind is TokenKind.NEWLINE for token in tokens) == 1


@pytest.mark.parametrize("literal", ["0", "42", "_3", "2.5", ".25", "1e3", "1e_3", "_", "_."])
def test_numeric_atoms(literal: str) -> None:
    token = tokenize(literal)[0]
    assert token.kind is TokenKind.NUMBER
    assert token.value == literal


def test_tokens_preserve_offsets_and_line_columns() -> None:
    tokens = tokenize("a =. 1\r\nb =. 2")
    second_name = next(token for token in tokens if token.value == "b")

    assert (second_name.line, second_name.column) == (2, 1)
    assert second_name.start == 8
    assert second_name.end == 9


def test_both_pythagorean_examples_lex() -> None:
    for filename in ("pythag.ijs", "pythag_array.ijs"):
        tokens = tokenize((ROOT / filename).read_text(encoding="utf-8"))
        assert tokens[-1].kind is TokenKind.EOF
        assert any(token.value == "triples" for token in tokens)


def test_unterminated_string_has_a_source_location() -> None:
    with pytest.raises(LexerError, match=r"1:6: unterminated string literal"):
        tokenize("echo 'broken")


def test_unknown_character_has_a_source_location() -> None:
    with pytest.raises(LexerError, match=r"2:1: unexpected character"):
        tokenize("1 + 2\n€")
