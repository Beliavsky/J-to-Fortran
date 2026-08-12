NB. J -> Fortran transpiler test
NB. Feature: Fibonacci loop
NB. Expected: ok = 1

fib =: 3 : 0
  a =. 0
  b =. 1
  n =. y
  while. n > 0 do.
    t =. a + b
    a =. b
    b =. t
    n =. n - 1
  end.
  a
)
result =: fib 10
expected =: 55
ok =: result -: expected
