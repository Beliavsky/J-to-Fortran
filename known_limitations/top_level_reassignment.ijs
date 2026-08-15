NB. Repro: top-level reassignment of a global noun is not supported.
NB.
NB. Currently fails with:
NB.   xj2f.py: error: N: top-level reassignment of 'x' is not supported
NB. raised in _lower_top_assignments (xj2f.py) the moment the same name
NB. is assigned at top level a second time.
NB.
NB. Source files that hit this: c:\j\public_domain\github\jcompiler\
NB. jlang_programs\ctest_global_assgmts_refs_integer.ijs,
NB. ctest_is_verb_globalassgmts.ijs, ctest_ch1_learningjbook.ijs,
NB. ctest_ch2_learningjbook.ijs (the latter two also change x's shape
NB. across reassignments: scalar -> vector -> matrix -> rank 3).
NB.
NB. WHY THIS IS NOT A SMALL PATCH:
NB. Top-level emission is currently two-phase: _lower_top_assignments
NB. collects every top-level Assign (one LoweredTopAssignment per name)
NB. and ALL of them are written out first; only afterward does the
NB. separate echo/display pass run. That is safe today only because
NB. each name is written exactly once, so *when* the assignment happens
NB. relative to reads never matters.
NB.
NB. The line below is the trap: x is read (via `x - 1`) BETWEEN its two
NB. writes. A naive fix that appends the second write as another
NB. "update" on x's existing LoweredTopAssignment record would still put
NB. both writes in the assignment phase, before the echo phase runs --
NB. so `x - 1` would see x = 0, not x = 100, and print -1 instead of 99.
NB. Verified by hand: see the "order2.ijs" experiment described in the
NB. 2026-08-15 session (x and y both got hoisted before any echo,
NB. regardless of source order).
NB.
NB. A correct fix needs one real sequential pass over program.items
NB. (assignments and echoes interleaved in source order), not a
NB. collect-then-emit split -- and print-only/parameter-inlining/
NB. dependency-ordering logic that currently assumes one definition per
NB. name needs re-auditing under that model.

x =: 100
x - 1       NB. must print 99 (uses the FIRST value of x)
x =: 0
x           NB. must print 0 (uses the SECOND value of x)
