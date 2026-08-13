NB. Price American call and put options with a Cox-Ross-Rubinstein tree.

spot =: 100
rate =: 0.05
volatility =: 0.2
maturity =: 1
steps =: 500
strikes =: 70 80 90 100 110 120 130

NB. y is one strike. Return the American call price and put price.
american_price =: 3 : 0
  dt =. maturity % steps
  up =. ^ volatility * %: dt
  down =. 1 % up
  growth =. ^ rate * dt
  probability_up =. (growth - down) % up - down
  probability_down =. 1 - probability_up
  discount =. ^ -rate * dt

  NB. At expiry, node j has j upward and steps-j downward moves.
  terminal_stock =. spot * up ^ (2 * i. 1 + steps) - steps
  call_values =. 0 >. terminal_stock - y
  put_values =. 0 >. y - terminal_stock

  NB. Work backward, comparing continuation value with immediate exercise.
  for_iteration. i. steps do.
    step =. (steps - 1) - iteration
    call_down =. }: call_values
    call_up =. }. call_values
    put_down =. }: put_values
    put_up =. }. put_values
    call_continuation =. discount * (probability_down * call_down) + probability_up * call_up
    put_continuation =. discount * (probability_down * put_down) + probability_up * put_up

    node_stock =. spot * up ^ (2 * i. 1 + step) - step
    call_exercise =. 0 >. node_stock - y
    put_exercise =. 0 >. y - node_stock
    call_values =. call_exercise >. call_continuation
    put_values =. put_exercise >. put_continuation
  end.

  call_values , put_values
)

price_strikes =: 3 : 0
  call_results =. 0 $ 0.0
  put_results =. 0 $ 0.0
  for_index. i. # y do.
    option_prices =. american_price index { y
    call_results =. call_results , 0 { option_prices
    put_results =. put_results , 1 { option_prices
  end.
  call_results ,: put_results
)

prices =: price_strikes strikes
call_prices =: 0 { prices
put_prices =: 1 { prices

smoutput 'strikes'
smoutput strikes
smoutput 'American calls'
smoutput call_prices
smoutput 'American puts'
smoutput put_prices

exit 0
