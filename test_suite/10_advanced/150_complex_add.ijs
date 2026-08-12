NB. J -> Fortran transpiler test
NB. Feature: complex arithmetic
NB. Expected: ok = 1

result =: 3j4 + 1j2
expected =: 4j6
ok =: result -: expected
