NB. J -> Fortran transpiler test
NB. Feature: iterative factorial
NB. Expected: ok = 1

fact =: 3 : 0
  p =. 1
  n =. y
  while. n > 1 do.
    p =. p * n
    n =. n - 1
  end.
  p
)
result =: fact 6
expected =: 720
ok =: result -: expected
