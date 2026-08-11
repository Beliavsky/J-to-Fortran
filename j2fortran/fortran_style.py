"""Fortran naming and procedure-style policy for generated code.

The conservative reserved-name set and elemental eligibility rules are adapted
from the sibling C-to-Fortran and R-to-Fortran scanners/postprocessors.  They
are kept small and dependency-free here so style is enforced during emission.
"""

from __future__ import annotations

import re
from collections.abc import Iterable


# Fortran does not reserve every word in every context. Generated code avoids
# construct/declaration words anyway because doing so is clearer to readers and
# less error-prone for downstream source tools. This policy is adapted from
# c2f/fortran_scan.py and R-to-Fortran/fortran_scan.py.
FORTRAN_KEYWORDS = {
    "abstract", "allocatable", "allocate", "associate", "asynchronous",
    "backspace", "bind", "block", "blockdata", "call", "case", "character",
    "class", "close", "codimension", "common", "complex", "concurrent",
    "contains", "contiguous", "continue", "critical", "cycle", "data",
    "deallocate", "deferred", "dimension", "do", "double", "else", "elseif",
    "elsewhere", "elemental", "end", "endassociate", "endblock", "endcritical",
    "enddo", "endenum", "endfile", "endforall", "endfunction", "endif",
    "endinterface", "endmodule", "endprocedure", "endprogram", "endselect",
    "endsubmodule", "endsubroutine", "endteam", "endtype", "endwhere", "entry",
    "enum", "enumerator", "equivalence", "error", "event", "exit", "extends",
    "external", "final", "flush", "forall", "format", "function", "generic",
    "goto", "if", "implicit", "import", "impure", "in", "include", "inout",
    "inquire", "integer", "intent", "interface", "intrinsic", "lock", "logical",
    "memory", "module", "namelist", "none", "non_overridable", "nopass",
    "nullify", "only", "open", "operator", "optional", "out", "parameter",
    "pass", "pause", "pointer", "precision", "print", "private", "procedure",
    "program", "protected", "public", "pure", "rank", "read", "real",
    "recursive", "result", "return", "rewind", "save", "select", "selectcase",
    "selectrank", "selecttype", "sequence", "stop", "submodule", "subroutine",
    "sync", "target", "team", "then", "type", "unlock", "use", "value",
    "volatile", "wait", "where", "while", "write",
}

FORTRAN_COMMON_INTRINSICS = {
    "abs", "count", "dot_product", "floor", "int", "lbound", "matmul", "max",
    "maxval", "merge", "min", "minval", "mod", "norm2", "pack", "product",
    "random_number", "random_seed", "real", "reshape", "shape", "size", "spread", "sqrt",
    "sum", "transpose", "ubound",
}

# MASK is an intrinsic keyword argument rather than a reserved language word,
# but avoiding it was explicitly requested for generated identifiers.
FORTRAN_AVOIDED_IDENTIFIERS = FORTRAN_KEYWORDS | FORTRAN_COMMON_INTRINSICS | {"mask"}


def safe_fortran_identifier(name: str) -> str:
    """Return a readable Fortran identifier that avoids policy collisions."""

    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", name).lower()
    if not cleaned or not cleaned[0].isalpha():
        cleaned = "j_" + cleaned
    if cleaned in FORTRAN_AVOIDED_IDENTIFIERS:
        cleaned += "_j"
    return cleaned


def procedure_prefix(
    dummy_ranks: Iterable[int],
    *,
    result_rank: int | None,
    is_pure: bool = True,
) -> str:
    """Choose PURE ELEMENTAL only when all dummies and any result are scalar.

    This follows the conservative promotion checks in the sibling
    ``promote_pure_scalar_subroutines_to_elemental`` implementations.
    ``result_rank=None`` denotes a subroutine.
    """

    if not is_pure:
        return ""
    all_scalar = all(rank == 0 for rank in dummy_ranks)
    scalar_result = result_rank in {None, 0}
    return "pure elemental" if all_scalar and scalar_result else "pure"


def combine_declarations(
    declarations: Iterable[tuple[str, str]],
) -> list[str]:
    """Combine entities whose complete declaration specifications match."""

    grouped: dict[str, list[str]] = {}
    for specification, entity in declarations:
        grouped.setdefault(specification, []).append(entity)
    return [f"{specification} :: {', '.join(entities)}" for specification, entities in grouped.items()]


def combine_adjacent_row_extension_assignments(lines: Iterable[str]) -> list[str]:
    """Combine a leading row-section assignment and its following scalar.

    The rewrite is deliberately structural and conservative. For example::

        out(i, 1:n) = row
        out(i, n + 1) = scalar

    becomes::

        out(i, :) = [row, scalar]

    This follows the adjacent-statement coalescing style used by the sibling
    Fortran postprocessors, but targets the array-extension idiom emitted here.
    """

    source = list(lines)
    combined: list[str] = []
    first_re = re.compile(
        r"^(?P<indent>\s*)(?P<array>[a-z][a-z0-9_]*)\("
        r"(?P<row>[^,]+),\s*1\s*:\s*(?P<extent>.+)\)\s*=\s*(?P<head>.+)$",
        re.IGNORECASE,
    )
    second_re = re.compile(
        r"^(?P<indent>\s*)(?P<array>[a-z][a-z0-9_]*)\("
        r"(?P<row>[^,]+),\s*(?P<index>.+)\)\s*=\s*(?P<tail>.+)$",
        re.IGNORECASE,
    )

    def normalized(text: str) -> str:
        return re.sub(r"\s+", "", text).lower()

    index = 0
    while index < len(source):
        if index + 1 >= len(source):
            combined.append(source[index])
            break
        first = first_re.match(source[index])
        second = second_re.match(source[index + 1])
        if (
            first is None
            or second is None
            or first.group("indent") != second.group("indent")
            or first.group("array").lower() != second.group("array").lower()
            or normalized(first.group("row")) != normalized(second.group("row"))
            or normalized(second.group("index"))
            != normalized(first.group("extent")) + "+1"
        ):
            combined.append(source[index])
            index += 1
            continue
        combined.append(
            f"{first.group('indent')}{first.group('array')}({first.group('row').strip()}, :) = "
            f"[{first.group('head').strip()}, {second.group('tail').strip()}]"
        )
        index += 2
    return combined
