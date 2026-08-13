NB. Price European options analytically and by risk-neutral Monte Carlo.

spot =: 100
rate =: 0.05
volatility =: 0.2
maturity =: 1
strikes =: 70 80 90 100 110 120 130
n =: 500000

NB. Standard normal CDF using the Abramowitz-Stegun approximation.
normal_cdf =: 3 : 0
  t =. 1 % 1 + 0.2316419 * | y
  density =. (^ _0.5 * y ^ 2) % %: 2 * 1p1
  polynomial =. t * (0.319381530 + t * (_0.356563782 + t * (1.781477937 + t * (_1.821255978 + t * 1.330274429))))
  tail =. density * polynomial
  ((y >: 0) * (1 - tail)) + (y < 0) * tail
)

NB. Return a two-row matrix: call prices followed by put prices.
black_scholes =: 3 : 0
  discount =. ^ -rate * maturity
  vol_sqrt_t =. volatility * %: maturity
  d1 =. ((^. spot % y) + (rate + 0.5 * volatility ^ 2) * maturity) % vol_sqrt_t
  d2 =. d1 - vol_sqrt_t
  call =. (spot * normal_cdf d1) - y * discount * normal_cdf d2
  put =. (y * discount * normal_cdf -d2) - spot * normal_cdf -d1
  call ,: put
)

analytic =: black_scholes strikes
analytic_call =: 0 { analytic
analytic_put =: 1 { analytic

NB. Simulate the terminal stock price under the risk-neutral distribution.
u1 =: 1e_12 >. ? n $ 0
u2 =: ? n $ 0
z =: (%: _2 * ^. u1) * 2 o. 2 * 1p1 * u2
terminal =: spot * ^ ((rate - 0.5 * volatility ^ 2) * maturity) + volatility * (%: maturity) * z

NB. Each column contains payoffs for one strike, using common random draws.
discount =: ^ -rate * maturity
differences =: terminal -/ strikes
mc_call =: discount * (+/ 0 >. differences) % n
mc_put =: discount * (+/ 0 >. -differences) % n

results =: ((((strikes ,. analytic_call) ,. mc_call) ,. analytic_put) ,. mc_put)
smoutput 'strike, analytic call, Monte Carlo call, analytic put, Monte Carlo put'
smoutput results

exit 0
