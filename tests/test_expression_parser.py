from __future__ import annotations

import pytest

from j2fortran.ast import (
    AdverbApplication,
    AmendVerb,
    DyadicApply,
    Group,
    MonadicApply,
    Name,
    NamedVerb,
    NumberLiteral,
    PrimitiveVerb,
    RankApplication,
    Strand,
    ast_to_dict,
)
from j2fortran.expression_parser import ExpressionParseError, parse_expression, parse_verb


def primitive(node: PrimitiveVerb | AdverbApplication | RankApplication) -> str:
    if isinstance(node, PrimitiveVerb):
        return node.spelling
    return primitive(node.operand)


def test_parse_complete_reflex_verb_phrase() -> None:
    verb = parse_verb("-~")

    assert isinstance(verb, AdverbApplication)
    assert verb.adverb == "~"
    assert primitive(verb.operand) == "-"


def test_ranked_named_verb_application() -> None:
    expression = parse_expression('(isprime"0 nums) # nums')

    assert isinstance(expression, DyadicApply)
    assert isinstance(expression.left, Group)
    application = expression.left.expression
    assert isinstance(application, MonadicApply)
    assert isinstance(application.verb, RankApplication)
    assert isinstance(application.verb.operand, NamedVerb)
    assert application.verb.operand.identifier == "isprime"


def test_direct_named_monadic_verb_application() -> None:
    expression = parse_expression("square 7")

    assert isinstance(expression, MonadicApply)
    assert isinstance(expression.verb, NamedVerb)
    assert expression.verb.identifier == "square"
    assert isinstance(expression.operand, NumberLiteral)


def test_direct_named_dyadic_verb_application() -> None:
    expression = parse_expression("3 lincomb 5")

    assert isinstance(expression, DyadicApply)
    assert isinstance(expression.verb, NamedVerb)
    assert expression.verb.identifier == "lincomb"


def test_amendment_replacement_name_remains_a_noun() -> None:
    expression = parse_expression("new ((<1 2 ; 0 3)}) a")

    assert isinstance(expression, DyadicApply)
    assert isinstance(expression.left, Name)
    assert isinstance(expression.verb, AmendVerb)


def test_noun_derived_amend_verb_preserves_its_selector() -> None:
    expression = parse_expression("99 ((<1 2 ; 0 3)}) a")

    assert isinstance(expression, DyadicApply)
    assert isinstance(expression.verb, AmendVerb)
    assert isinstance(expression.verb.selector, Group)
    assert isinstance(expression.right, Name)


def test_j_evaluation_is_parsed_right_to_left() -> None:
    expression = parse_expression("1 + i. y")

    assert isinstance(expression, DyadicApply)
    assert primitive(expression.verb) == "+"
    assert isinstance(expression.left, NumberLiteral)
    assert isinstance(expression.right, MonadicApply)
    assert primitive(expression.right.verb) == "i."
    assert isinstance(expression.right.operand, Name)


def test_parentheses_override_right_to_left_grouping() -> None:
    expression = parse_expression("(a * a) + (b * b)")

    assert isinstance(expression, DyadicApply)
    assert isinstance(expression.left, Group)
    assert isinstance(expression.right, Group)
    assert isinstance(expression.left.expression, DyadicApply)
    assert primitive(expression.left.expression.verb) == "*"


def test_numeric_strand_is_one_noun() -> None:
    expression = parse_expression("0 3 $ 0")

    assert isinstance(expression, DyadicApply)
    assert isinstance(expression.left, Strand)
    assert [item.text for item in expression.left.items] == ["0", "3"]
    assert primitive(expression.verb) == "$"


def test_monadic_chain() -> None:
    expression = parse_expression("<. %: sumsq")

    assert isinstance(expression, MonadicApply)
    assert primitive(expression.verb) == "<."
    assert isinstance(expression.operand, MonadicApply)
    assert primitive(expression.operand.verb) == "%:"


def test_rank_conjunction_derives_a_verb() -> None:
    expression = parse_expression('0 {"1 ab')

    assert isinstance(expression, DyadicApply)
    assert isinstance(expression.verb, RankApplication)
    assert primitive(expression.verb) == "{"
    assert expression.verb.rank.text == "1"


def test_insert_adverb_can_then_receive_rank() -> None:
    expression = parse_expression('+/"1 matrix')

    assert isinstance(expression, MonadicApply)
    assert isinstance(expression.verb, RankApplication)
    assert isinstance(expression.verb.operand, AdverbApplication)
    assert expression.verb.operand.adverb == "/"
    assert primitive(expression.verb) == "+"


@pytest.mark.parametrize("source", ["/:~ 3 1 2", "\\:~ 3 1 2"])
def test_reflex_derives_a_sort_verb(source: str) -> None:
    expression = parse_expression(source)

    assert isinstance(expression, MonadicApply)
    assert isinstance(expression.verb, AdverbApplication)
    assert expression.verb.adverb == "~"
    assert primitive(expression.verb) in {"/:", "\\:"}


def test_insert_then_scan_builds_nested_adverbs() -> None:
    expression = parse_expression("+/\\ 1 2 3")

    assert isinstance(expression, MonadicApply)
    assert isinstance(expression.verb, AdverbApplication)
    assert expression.verb.adverb == "\\"
    assert isinstance(expression.verb.operand, AdverbApplication)
    assert expression.verb.operand.adverb == "/"


def test_compression_and_laminate_follow_right_to_left_order() -> None:
    expression = parse_expression("keep # ab ,. c")

    assert isinstance(expression, DyadicApply)
    assert primitive(expression.verb) == "#"
    assert isinstance(expression.right, DyadicApply)
    assert primitive(expression.right.verb) == ",."


def test_ast_serialization_preserves_nested_node_kinds() -> None:
    serialized = ast_to_dict(parse_expression("1 + i. y"))

    assert serialized["kind"] == "DyadicApply"
    assert serialized["verb"]["kind"] == "PrimitiveVerb"
    assert serialized["right"]["kind"] == "MonadicApply"
    assert serialized["right"]["operand"]["kind"] == "Name"


@pytest.mark.parametrize(
    "source",
    [
        "(a * a) + (b * b)",
        "(a < b) *. (sumsq = c * c) *. (c <: y)",
        "> , { 2 # < 1 + i. y",
        "result , a , b , c",
    ],
)
def test_example_expressions_parse(source: str) -> None:
    parse_expression(source)


def test_missing_right_argument_is_diagnostic() -> None:
    with pytest.raises(ExpressionParseError, match="dyadic verb '\\+' has no right argument"):
        parse_expression("1 +")


def test_unclosed_parenthesis_is_diagnostic() -> None:
    with pytest.raises(ExpressionParseError, match="unclosed parenthesized expression"):
        parse_expression("(1 + 2")
