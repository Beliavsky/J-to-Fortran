NB. Integration test: explicit verb, control flow, rank, filter, reduction.
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
nums =: 2 + i. 99
primes =: (isprime"0 nums) # nums
result =: +/ primes
expected =: 1060
ok =: result -: expected
