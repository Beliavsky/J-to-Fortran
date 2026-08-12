NB. J -> Fortran transpiler test
NB. Feature: tacit fork mean +/ % #
NB. Expected: ok = 1

mean =: +/ % #
result =: mean 2 4 6 8
expected =: 5
ok =: result -: expected
