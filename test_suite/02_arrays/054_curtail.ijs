NB. J -> Fortran transpiler test
NB. Feature: curtail }:
NB. Expected: ok = 1

a =: 10 20 30 40
result =: }: a
expected =: 10 20 30
ok =: result -: expected
