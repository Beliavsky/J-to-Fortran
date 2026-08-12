NB. J -> Fortran transpiler test
NB. Feature: reverse |.
NB. Expected: ok = 1

result =: |. 1 2 3 4 5
expected =: 5 4 3 2 1
ok =: result -: expected
