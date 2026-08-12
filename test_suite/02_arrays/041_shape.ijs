NB. J -> Fortran transpiler test
NB. Feature: shape $
NB. Expected: ok = 1

a =: 2 3 $ 0 1 2 3 4 5
result =: $ a
expected =: 2 3
ok =: result -: expected
