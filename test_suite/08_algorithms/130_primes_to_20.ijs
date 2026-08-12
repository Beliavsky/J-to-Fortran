NB. J -> Fortran transpiler test
NB. Feature: manual primality test and filter
NB. Expected: ok = 1

isprime =: 3 : 0
  if. y < 2 do.
    0
  elseif. y = 2 do.
    1
  elseif. do.
    limit =. <. %: y
    divisors =. 2 + i. limit - 1
    -. +./ 0 = divisors | y
  end.
)
nums =: 2 + i. 19
result =: (isprime"0 nums) # nums
expected =: 2 3 5 7 11 13 17 19
ok =: result -: expected
