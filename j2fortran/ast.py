"""Syntax nodes for the initially supported J expression grammar."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from typing import Any, TypeAlias


@dataclass(frozen=True, slots=True)
class SourceSpan:
    start: int
    end: int
    line: int
    column: int
    end_line: int
    end_column: int


@dataclass(frozen=True, slots=True)
class NumberLiteral:
    text: str
    span: SourceSpan


@dataclass(frozen=True, slots=True)
class StringLiteral:
    value: str
    span: SourceSpan


@dataclass(frozen=True, slots=True)
class Name:
    identifier: str
    span: SourceSpan


@dataclass(frozen=True, slots=True)
class Strand:
    items: tuple[NumberLiteral, ...]
    span: SourceSpan


@dataclass(frozen=True, slots=True)
class Group:
    expression: Expression
    span: SourceSpan


@dataclass(frozen=True, slots=True)
class PrimitiveVerb:
    spelling: str
    span: SourceSpan


@dataclass(frozen=True, slots=True)
class NamedVerb:
    identifier: str
    span: SourceSpan


@dataclass(frozen=True, slots=True)
class AmendVerb:
    selector: Expression
    span: SourceSpan


@dataclass(frozen=True, slots=True)
class AdverbApplication:
    adverb: str
    operand: Verb
    span: SourceSpan


@dataclass(frozen=True, slots=True)
class RankApplication:
    operand: Verb
    rank: NumberLiteral
    span: SourceSpan


Verb: TypeAlias = AmendVerb | NamedVerb | PrimitiveVerb | AdverbApplication | RankApplication


@dataclass(frozen=True, slots=True)
class MonadicApply:
    verb: Verb
    operand: Expression
    span: SourceSpan


@dataclass(frozen=True, slots=True)
class DyadicApply:
    verb: Verb
    left: Expression
    right: Expression
    span: SourceSpan


Expression: TypeAlias = (
    NumberLiteral
    | StringLiteral
    | Name
    | Strand
    | Group
    | MonadicApply
    | DyadicApply
)


def ast_to_dict(node: Expression | Verb) -> dict[str, Any]:
    """Return a JSON-serializable representation with explicit node kinds."""

    def convert(value: Any) -> Any:
        if is_dataclass(value) and not isinstance(value, type):
            return {
                "kind": type(value).__name__,
                **{field.name: convert(getattr(value, field.name)) for field in fields(value)},
            }
        if isinstance(value, tuple):
            return [convert(item) for item in value]
        return value

    converted = convert(node)
    assert isinstance(converted, dict)
    return converted
