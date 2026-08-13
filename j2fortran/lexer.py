"""A source-position-preserving lexer for J sentences.

The lexer recognizes more spelling forms than the transpiler currently lowers.
That lets later parser stages distinguish valid-but-unsupported J from malformed
input and report useful source locations.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
import re


class TokenKind(Enum):
    NAME = auto()
    NUMBER = auto()
    STRING = auto()
    PRIMITIVE = auto()
    COPULA = auto()
    CONTROL = auto()
    LPAREN = auto()
    RPAREN = auto()
    NEWLINE = auto()
    EOF = auto()


@dataclass(frozen=True, slots=True)
class Token:
    kind: TokenKind
    value: str
    line: int
    column: int
    end_line: int
    end_column: int
    start: int
    end: int


class LexerError(ValueError):
    def __init__(self, message: str, *, line: int, column: int):
        super().__init__(f"{line}:{column}: {message}")
        self.message = message
        self.line = line
        self.column = column


_NUMBER_RE = re.compile(
    r"(?:"
    r"_?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE]_?\d+)?"
    r"(?:(?:j_?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE]_?\d+)?)|(?:r_?\d+)|(?:p_?\d+))?"
    r"|_\."
    r"|_"
    r")"
)
_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")

_CONTROL_WORDS = {
    "assert.",
    "break.",
    "case.",
    "catch.",
    "catchd.",
    "catcht.",
    "continue.",
    "do.",
    "else.",
    "elseif.",
    "end.",
    "fcase.",
    "for.",
    "if.",
    "return.",
    "select.",
    "throw.",
    "try.",
    "while.",
    "whilst.",
}

_ALPHABETIC_PRIMITIVES = {
    "a.",
    "a:",
    "C.",
    "d.",
    "D.",
    "D:",
    "E.",
    "e.",
    "i.",
    "i:",
    "L.",
    "L:",
    "o.",
    "p.",
    "p:",
    "q:",
    "s:",
    "t.",
    "t:",
    "u:",
    "x:",
}

# Every ASCII primitive begins with one of these glyphs.  A following dot or
# colon is part of the same primitive spelling (for example +., <., i., {., ,:).
_PRIMITIVE_INITIALS = frozenset("=<>+*-:%^$~|.,;#!/\\[]{}\"?@&`")


class Lexer:
    def __init__(self, source: str):
        self.source = source
        self.length = len(source)
        self.index = 0
        self.line = 1
        self.column = 1

    def tokenize(self) -> tuple[Token, ...]:
        tokens: list[Token] = []
        while self.index < self.length:
            character = self.source[self.index]
            if character in " \t\f\v":
                self._advance_text(character)
                continue
            if character in "\r\n":
                tokens.append(self._newline())
                continue
            if self.source.startswith("NB.", self.index):
                self._comment()
                continue
            if character == "'":
                tokens.append(self._string())
                continue
            if character == "(":
                tokens.append(self._single(TokenKind.LPAREN))
                continue
            if character == ")":
                tokens.append(self._single(TokenKind.RPAREN))
                continue
            number = _NUMBER_RE.match(self.source, self.index)
            if number is not None:
                tokens.append(self._matched(TokenKind.NUMBER, number.group(0)))
                continue
            name = _NAME_RE.match(self.source, self.index)
            if name is not None:
                tokens.append(self._name_or_control(name.group(0)))
                continue
            if character in _PRIMITIVE_INITIALS:
                tokens.append(self._primitive())
                continue
            raise LexerError(
                f"unexpected character {character!r}", line=self.line, column=self.column
            )
        tokens.append(
            Token(
                TokenKind.EOF,
                "",
                self.line,
                self.column,
                self.line,
                self.column,
                self.index,
                self.index,
            )
        )
        return tuple(tokens)

    def _single(self, kind: TokenKind) -> Token:
        character = self.source[self.index]
        return self._matched(kind, character)

    def _matched(self, kind: TokenKind, value: str) -> Token:
        start = self.index
        line = self.line
        column = self.column
        self._advance_text(value)
        return Token(
            kind,
            value,
            line,
            column,
            self.line,
            self.column,
            start,
            self.index,
        )

    def _name_or_control(self, value: str) -> Token:
        candidate = value
        suffix_at = self.index + len(value)
        if suffix_at < self.length and self.source[suffix_at] in ".:":
            suffixed = value + self.source[suffix_at]
            if suffixed in _CONTROL_WORDS or (
                suffixed.endswith(".") and value.startswith("for_")
            ):
                candidate = suffixed
            elif suffixed in _ALPHABETIC_PRIMITIVES:
                return self._matched(TokenKind.PRIMITIVE, suffixed)
        kind = TokenKind.CONTROL if candidate in _CONTROL_WORDS or candidate.startswith("for_") else TokenKind.NAME
        return self._matched(kind, candidate)

    def _primitive(self) -> Token:
        start = self.index
        first = self.source[start]
        if self.source.startswith("=:", start) or self.source.startswith("=.", start):
            return self._matched(TokenKind.COPULA, self.source[start : start + 2])
        if self.source.startswith("{{", start) or self.source.startswith("}}", start):
            return self._matched(TokenKind.PRIMITIVE, self.source[start : start + 2])
        end = start + 1
        if end < self.length and self.source[end] in ".:":
            end += 1
        value = self.source[start:end]
        if first == "=" and len(value) == 2:
            return self._matched(TokenKind.COPULA, value)
        return self._matched(TokenKind.PRIMITIVE, value)

    def _string(self) -> Token:
        start = self.index
        line = self.line
        column = self.column
        self._advance_text("'")
        value_parts: list[str] = []
        while self.index < self.length:
            character = self.source[self.index]
            if character in "\r\n":
                raise LexerError("unterminated string literal", line=line, column=column)
            if character == "'":
                if self.index + 1 < self.length and self.source[self.index + 1] == "'":
                    value_parts.append("'")
                    self._advance_text("''")
                    continue
                self._advance_text("'")
                return Token(
                    TokenKind.STRING,
                    "".join(value_parts),
                    line,
                    column,
                    self.line,
                    self.column,
                    start,
                    self.index,
                )
            value_parts.append(character)
            self._advance_text(character)
        raise LexerError("unterminated string literal", line=line, column=column)

    def _comment(self) -> None:
        while self.index < self.length and self.source[self.index] not in "\r\n":
            self._advance_text(self.source[self.index])

    def _newline(self) -> Token:
        start = self.index
        line = self.line
        column = self.column
        if self.source.startswith("\r\n", self.index):
            self.index += 2
        else:
            self.index += 1
        self.line += 1
        self.column = 1
        return Token(
            TokenKind.NEWLINE,
            "\n",
            line,
            column,
            self.line,
            self.column,
            start,
            self.index,
        )

    def _advance_text(self, text: str) -> None:
        self.index += len(text)
        self.column += len(text)


def tokenize(source: str) -> tuple[Token, ...]:
    return Lexer(source).tokenize()
