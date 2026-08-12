NB. J -> Fortran transpiler test
NB. Feature: character indexing
NB. Expected: ok = 1

result =: 1 3 5 { 'abcdef'
expected =: 'bdf'
ok =: result -: expected
