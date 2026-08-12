NB. J -> Fortran transpiler test
NB. Feature: character vector tally
NB. Expected: ok = 1

result =: # 'abcdef'
expected =: 6
ok =: result -: expected
