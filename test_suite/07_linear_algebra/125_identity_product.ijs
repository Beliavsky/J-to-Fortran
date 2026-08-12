NB. J -> Fortran transpiler test
NB. Feature: matrix identity via matrix divide
NB. Expected: ok = 1

a =: 2 2 $ 3 4 2 3
i =: a %. a
result =: a (+/ . *) i
expected =: a
ok =: result -: expected
