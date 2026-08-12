NB. J -> Fortran transpiler test
NB. Feature: Horner polynomial evaluation
NB. Expected: ok = 1

horner =: 4 : 0
  c =. x
  z =. 0
  for_i. c do.
    z =. i + y * z
  end.
  z
)
NB. Coefficients are highest degree first: 2*x^3 - 3*x^2 + 4*x + 5
result =: 2 _3 4 5 horner 3
expected =: 44
ok =: result -: expected
