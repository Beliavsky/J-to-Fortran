NB. J -> Fortran transpiler test
NB. Feature: Euclidean algorithm and dyadic explicit verb
NB. Expected: ok = 1

gcd =: 4 : 0
  a =. x
  b =. y
  while. b ~: 0 do.
    t =. b
    b =. b | a
    a =. t
  end.
  a
)
result =: 84 gcd 30
expected =: 6
ok =: result -: expected
