NB. J -> Fortran transpiler test
NB. Feature: manual vector dot product
NB. Expected: ok = 1

dot =: 4 : 0
  +/ x * y
)
result =: 1 2 3 dot 4 5 6
expected =: 32
ok =: result -: expected
