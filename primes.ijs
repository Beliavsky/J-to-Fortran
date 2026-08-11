NB. Return 1 if y is prime, 0 otherwise
isprime =: 3 : 0
  if. y < 2 do.
    0
  elseif. y = 2 do.
    1
  else.
    limit =. <. %: y
    divisors =. 2 + i. limit - 1
    -. +./ 0 = divisors | y
  end.
)

nums =: 2 + i. 19
primes =: (isprime"0 nums) # nums

echo primes
exit 0
