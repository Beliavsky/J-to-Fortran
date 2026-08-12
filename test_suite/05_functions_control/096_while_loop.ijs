NB. J -> Fortran transpiler test
NB. Feature: while. loop
NB. Expected: ok = 1

sumto =: 3 : 0
  n =. y
  s =. 0
  while. n > 0 do.
    s =. s + n
    n =. n - 1
  end.
  s
)
result =: sumto 10
expected =: 55
ok =: result -: expected
