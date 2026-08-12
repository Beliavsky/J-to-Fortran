NB. J -> Fortran transpiler test
NB. Feature: mean centering
NB. Expected: ok = 1

mean =: +/ % #
x =: 2 4 6 8
result =: x - mean x
expected =: _3 _1 1 3
ok =: result -: expected
