NB. J -> Fortran transpiler test
NB. Feature: explicit monadic verb 3 : 0
NB. Expected: ok = 1

square =: 3 : 0
  y * y
)
result =: square 7
expected =: 49
ok =: result -: expected
