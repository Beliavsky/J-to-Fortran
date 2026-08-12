NB. J -> Fortran transpiler test
NB. Feature: character reverse
NB. Expected: ok = 1

result =: |. 'abcdef'
expected =: 'fedcba'
ok =: result -: expected
