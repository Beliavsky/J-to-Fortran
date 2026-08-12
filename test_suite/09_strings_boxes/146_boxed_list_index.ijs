NB. J -> Fortran transpiler test
NB. Feature: boxed list and indexing
NB. Expected: ok = 1

words =: 'one' ; 'two' ; 'three'
result =: > 1 { words
expected =: 'two'
ok =: result -: expected
