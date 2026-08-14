NB. Simulate a two-component normal mixture and compare its moments with theory.

n =: 200000

NB. Mixture parameters: component 1 has probability p.
p =: 0.35
q =: 1 - p
mu1 =: _1
sd1 =: 0.8
mu2 =: 2
sd2 =: 1.3

NB. Generate two independent standard normal samples by Box-Muller.
u1 =: 1e_12 >. ? n $ 0
u2 =: ? n $ 0
z1 =: (%: _2 * ^. u1) * 2 o. 2 * 1p1 * u2

u3 =: 1e_12 >. ? n $ 0
u4 =: ? n $ 0
z2 =: (%: _2 * ^. u3) * 2 o. 2 * 1p1 * u4

NB. Select a component independently for each observation.
component1 =: (? n $ 0) < p
x1 =: mu1 + sd1 * z1
x2 =: mu2 + sd2 * z2
x =: (component1 * x1) + (1 - component1) * x2

mean =: +/ % #

NB. Empirical central moments and standardized statistics.
emp_mean =: mean x
centered =: x - emp_mean
emp_m2 =: mean centered ^ 2
emp_m3 =: mean centered ^ 3
emp_m4 =: mean centered ^ 4
emp_sd =: %: emp_m2
emp_skew =: emp_m3 % emp_sd ^ 3
emp_excess =: (emp_m4 % emp_m2 ^ 2) - 3

NB. Raw moments of the mixture.  For N(mu,sd^2),
NB. E(X^2)=mu^2+sd^2, E(X^3)=mu^3+3*mu*sd^2,
NB. and E(X^4)=mu^4+6*mu^2*sd^2+3*sd^4.
theory_mean =: (p * mu1) + q * mu2
raw2 =: (p * ((mu1 ^ 2) + sd1 ^ 2)) + q * ((mu2 ^ 2) + sd2 ^ 2)
raw3 =: (p * ((mu1 ^ 3) + 3 * mu1 * sd1 ^ 2)) + q * ((mu2 ^ 3) + 3 * mu2 * sd2 ^ 2)
raw4 =: (p * ((mu1 ^ 4) + (6 * (mu1 ^ 2) * sd1 ^ 2) + 3 * sd1 ^ 4)) + q * ((mu2 ^ 4) + (6 * (mu2 ^ 2) * sd2 ^ 2) + 3 * sd2 ^ 4)

theory_m2 =: raw2 - theory_mean ^ 2
theory_m3 =: (raw3 - 3 * theory_mean * raw2) + 2 * theory_mean ^ 3
theory_m4 =: ((raw4 - 4 * theory_mean * raw3) + 6 * (theory_mean ^ 2) * raw2) - 3 * theory_mean ^ 4
theory_sd =: %: theory_m2
theory_skew =: theory_m3 % theory_sd ^ 3
theory_excess =: (theory_m4 % theory_m2 ^ 2) - 3

smoutput 'number of simulations'
smoutput n

smoutput 'mean: empirical theoretical difference'
smoutput emp_mean, theory_mean, emp_mean - theory_mean

smoutput 'standard deviation: empirical theoretical difference'
smoutput emp_sd, theory_sd, emp_sd - theory_sd

smoutput 'skewness: empirical theoretical difference'
smoutput emp_skew, theory_skew, emp_skew - theory_skew

smoutput 'excess kurtosis: empirical theoretical difference'
smoutput emp_excess, theory_excess, emp_excess - theory_excess

exit 0
