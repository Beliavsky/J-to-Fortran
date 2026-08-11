"""Right-to-left parser for the first numeric subset of J expressions."""

from __future__ import annotations

from collections.abc import Sequence

from .ast import (
    AdverbApplication,
    DyadicApply,
    Expression,
    Group,
    MonadicApply,
    Name,
    NamedVerb,
    NumberLiteral,
    PrimitiveVerb,
    RankApplication,
    SourceSpan,
    Strand,
    StringLiteral,
    Verb,
)
from .lexer import Token, TokenKind, tokenize


class ExpressionParseError(ValueError):
    def __init__(self, message: str, token: Token):
        super().__init__(f"{token.line}:{token.column}: {message}")
        self.message = message
        self.token = token


_ADVERBS = {"/", "/.", "\\", "\\."}


def _token_span(token: Token) -> SourceSpan:
    return SourceSpan(
        token.start,
        token.end,
        token.line,
        token.column,
        token.end_line,
        token.end_column,
    )


def _cover(first: SourceSpan, last: SourceSpan) -> SourceSpan:
    return SourceSpan(
        first.start,
        last.end,
        first.line,
        first.column,
        last.end_line,
        last.end_column,
    )


class ExpressionParser:
    def __init__(self, tokens: Sequence[Token]):
        self.tokens = tuple(
            token for token in tokens if token.kind not in {TokenKind.NEWLINE, TokenKind.EOF}
        )
        self.index = 0

    def parse(self) -> Expression:
        if not self.tokens:
            raise ValueError("cannot parse an empty J expression")
        expression = self._expression()
        if self.index != len(self.tokens):
            token = self.tokens[self.index]
            raise ExpressionParseError(f"unexpected token {token.value!r}", token)
        return expression

    def _expression(self) -> Expression:
        if self._starts_verb():
            verb = self._verb()
            if self.index >= len(self.tokens):
                token = self.tokens[-1]
                raise ExpressionParseError(
                    f"monadic verb {self._verb_name(verb)!r} has no argument", token
                )
            operand = self._expression()
            return MonadicApply(verb, operand, _cover(verb.span, operand.span))

        left = self._noun()
        if self._starts_verb():
            verb = self._verb()
            if self.index >= len(self.tokens):
                token = self.tokens[-1]
                raise ExpressionParseError(
                    f"dyadic verb {self._verb_name(verb)!r} has no right argument", token
                )
            right = self._expression()
            return DyadicApply(verb, left, right, _cover(left.span, right.span))
        return left

    def _noun(self) -> Expression:
        token = self._peek()
        if token.kind is TokenKind.NUMBER:
            items: list[NumberLiteral] = []
            while self.index < len(self.tokens) and self._peek().kind is TokenKind.NUMBER:
                number = self._take()
                items.append(NumberLiteral(number.value, _token_span(number)))
            if len(items) == 1:
                return items[0]
            return Strand(tuple(items), _cover(items[0].span, items[-1].span))
        if token.kind is TokenKind.STRING:
            self._take()
            return StringLiteral(token.value, _token_span(token))
        if token.kind is TokenKind.NAME:
            self._take()
            return Name(token.value, _token_span(token))
        if token.kind is TokenKind.LPAREN:
            opening = self._take()
            expression = self._expression()
            if self.index >= len(self.tokens) or self._peek().kind is not TokenKind.RPAREN:
                raise ExpressionParseError("unclosed parenthesized expression", opening)
            closing = self._take()
            return Group(expression, _cover(_token_span(opening), _token_span(closing)))
        raise ExpressionParseError(f"expected a noun, got {token.value!r}", token)

    def _verb(self) -> Verb:
        token = self._peek()
        if token.kind is TokenKind.NAME:
            self._take()
            verb: Verb = NamedVerb(token.value, _token_span(token))
        elif token.kind is TokenKind.PRIMITIVE and token.value not in _ADVERBS | {'"'}:
            self._take()
            verb = PrimitiveVerb(token.value, _token_span(token))
        else:
            raise ExpressionParseError(f"expected a verb, got {token.value!r}", token)

        while self.index < len(self.tokens):
            modifier = self._peek()
            if modifier.kind is not TokenKind.PRIMITIVE:
                break
            if modifier.value in _ADVERBS:
                self._take()
                verb = AdverbApplication(
                    modifier.value,
                    verb,
                    _cover(verb.span, _token_span(modifier)),
                )
                continue
            if modifier.value == '"':
                self._take()
                if self.index >= len(self.tokens) or self._peek().kind is not TokenKind.NUMBER:
                    raise ExpressionParseError("rank conjunction requires a numeric rank", modifier)
                rank_token = self._take()
                rank = NumberLiteral(rank_token.value, _token_span(rank_token))
                verb = RankApplication(verb, rank, _cover(verb.span, rank.span))
                continue
            break
        return verb

    def _starts_verb(self) -> bool:
        if self.index >= len(self.tokens):
            return False
        token = self._peek()
        if token.kind is TokenKind.PRIMITIVE:
            return token.value not in _ADVERBS | {'"'}
        return (
            token.kind is TokenKind.NAME
            and self.index + 1 < len(self.tokens)
            and self.tokens[self.index + 1].kind is TokenKind.PRIMITIVE
            and self.tokens[self.index + 1].value == '"'
        )

    def _peek(self) -> Token:
        return self.tokens[self.index]

    def _take(self) -> Token:
        token = self.tokens[self.index]
        self.index += 1
        return token

    @staticmethod
    def _verb_name(verb: Verb) -> str:
        if isinstance(verb, PrimitiveVerb):
            return verb.spelling
        if isinstance(verb, NamedVerb):
            return verb.identifier
        if isinstance(verb, AdverbApplication):
            return ExpressionParser._verb_name(verb.operand) + verb.adverb
        return ExpressionParser._verb_name(verb.operand) + '"' + verb.rank.text


def parse_expression(source: str) -> Expression:
    return ExpressionParser(tokenize(source)).parse()
