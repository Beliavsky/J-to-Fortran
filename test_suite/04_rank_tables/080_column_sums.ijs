NB. J -> Fortran transpiler test
NB. Feature: insert over matrix items / column sums
NB. Expected: ok = 1

a =: 2 3 $ 1 2 3 4 5 6
result =: +/ a
expected =: 5 7 9
ok =: result -: expected
