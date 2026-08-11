from __future__ import annotations

from pathlib import Path

import pytest

import xj2f


ROOT = Path(__file__).resolve().parents[1]


def test_primes_conditional_has_structured_branches() -> None:
    path = ROOT / "primes.ijs"
    program = xj2f.parse_j_source(path, path.read_text(encoding="utf-8"))
    verb = next(item for item in program.items if isinstance(item, xj2f.VerbDefinition))
    conditional = verb.body[0]

    assert isinstance(conditional, xj2f.IfStatement)
    assert conditional.condition == "y < 2"
    assert [branch.condition for branch in conditional.elseif_branches] == ["y = 2"]
    assert conditional.else_body is not None
    assert len(conditional.body) == 1
    assert len(conditional.elseif_branches[0].body) == 1
    assert len(conditional.else_body) == 3


def test_expression_report_contains_all_conditional_branches() -> None:
    path = ROOT / "primes.ijs"
    program = xj2f.parse_j_source(path, path.read_text(encoding="utf-8"))
    report = xj2f.expression_ast_report(program)
    conditional = report["verbs"][0]["body"][0]

    assert conditional["role"] == "if"
    assert conditional["elseif"][0]["ast"]["kind"] == "DyadicApply"
    assert len(conditional["else_body"]) == 3


def test_nested_conditionals_parse() -> None:
    source = """f =: 3 : 0
  if. y > 0 do.
    if. y = 1 do.
      10
    else.
      20
    end.
  else.
    0
  end.
)
"""
    program = xj2f.parse_j_source(Path("nested.ijs"), source)
    verb = program.items[0]
    assert isinstance(verb, xj2f.VerbDefinition)
    outer = verb.body[0]
    assert isinstance(outer, xj2f.IfStatement)
    assert isinstance(outer.body[0], xj2f.IfStatement)
    assert outer.else_body is not None


def test_stray_else_is_rejected_at_its_source_line() -> None:
    source = "f =: 3 : 0\n  else.\n    0\n  end.\n)\n"

    with pytest.raises(xj2f.ParseError, match=r"2: unexpected conditional branch"):
        xj2f.parse_j_source(Path("broken.ijs"), source)
