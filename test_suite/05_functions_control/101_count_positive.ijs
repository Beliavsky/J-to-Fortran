NB. J -> Fortran transpiler test
NB. Feature: vector comparison inside explicit verb
NB. Expected: ok = 1

countpos =: 3 : 0
  +/ y > 0
)
result =: countpos _3 5 0 2 _1 8
expected =: 3
ok =: result -: expected
