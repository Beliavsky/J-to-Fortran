NB. Simulate and fit 3-dimensional normal mixtures with equicorrelation.

n =: 50000
dimension =: 3

NB. The sample comes from a two-component equicorrelated normal mixture.
true_weight =: 0.4
true_means1 =: _1 _0.5 0
true_sigma1 =: 0.8
true_rho1 =: 0.35
true_means2 =: 2 1.5 1
true_sigma2 =: 1.2
true_rho2 =: 0.6

NB. Four independent standard normal vectors are enough for a common-factor model.
u1 =: 1e_12 >. ? n $ 0
u2 =: ? n $ 0
z0 =: (%: _2 * ^. u1) * 2 o. 2 * 1p1 * u2
u3 =: 1e_12 >. ? n $ 0
u4 =: ? n $ 0
z1 =: (%: _2 * ^. u3) * 2 o. 2 * 1p1 * u4
u5 =: 1e_12 >. ? n $ 0
u6 =: ? n $ 0
z2 =: (%: _2 * ^. u5) * 2 o. 2 * 1p1 * u6
u7 =: 1e_12 >. ? n $ 0
u8 =: ? n $ 0
z3 =: (%: _2 * ^. u7) * 2 o. 2 * 1p1 * u8

NB. A common factor gives every pair of coordinates correlation rho.
common1 =: %: true_rho1
independent1 =: %: 1 - true_rho1
xa1 =: (0 { true_means1) + true_sigma1 * (common1 * z0) + independent1 * z1
xb1 =: (1 { true_means1) + true_sigma1 * (common1 * z0) + independent1 * z2
xc1 =: (2 { true_means1) + true_sigma1 * (common1 * z0) + independent1 * z3

common2 =: %: true_rho2
independent2 =: %: 1 - true_rho2
xa2 =: (0 { true_means2) + true_sigma2 * (common2 * z0) + independent2 * z1
xb2 =: (1 { true_means2) + true_sigma2 * (common2 * z0) + independent2 * z2
xc2 =: (2 { true_means2) + true_sigma2 * (common2 * z0) + independent2 * z3

component1 =: (? n $ 0) < true_weight
xa =: (component1 * xa1) + (1 - component1) * xa2
xb =: (component1 * xb1) + (1 - component1) * xb2
xc =: (component1 * xc1) + (1 - component1) * xc2

NB. y contains three means, a common sigma, and the equicorrelation rho.
eqc_density =: 3 : 0
  mua =. 0 { y
  mub =. 1 { y
  muc =. 2 { y
  sigma =. 3 { y
  rho =. 4 { y
  za =. (xa - mua) % sigma
  zb =. (xb - mub) % sigma
  zc =. (xc - muc) % sigma
  sum_squares =. (za ^ 2) + (zb ^ 2) + zc ^ 2
  square_sum =. (za + zb + zc) ^ 2
  quadratic =. (sum_squares - (rho % 1 + 2 * rho) * square_sum) % 1 - rho
  determinant =. ((1 - rho) ^ 2) * 1 + 2 * rho
  (^ _0.5 * quadratic) % 15.7496099457 * (sigma ^ 3) * %: determinant
)

NB. One weighted M-step returns weight, three means, sigma, and rho.
component_update =: 3 : 0
  responsibility =. y
  responsibility_sum =. +/ responsibility
  weight =. responsibility_sum % n
  mua =. (+/ responsibility * xa) % responsibility_sum
  mub =. (+/ responsibility * xb) % responsibility_sum
  muc =. (+/ responsibility * xc) % responsibility_sum
  da =. xa - mua
  db =. xb - mub
  dc =. xc - muc
  diagonal =. (+/ responsibility * ((da ^ 2) + (db ^ 2) + dc ^ 2)) % 3 * responsibility_sum
  off_diagonal =. (+/ responsibility * ((da * db) + (da * dc) + db * dc)) % 3 * responsibility_sum
  sigma =. 1e_8 >. %: diagonal
  rho =. _0.49 >. 0.95 <. off_diagonal % diagonal
  weight , mua , mub , muc , sigma , rho
)

fit_two_em =: 3 : 0
  parameters =. y
  for_iteration. i. 200 do.
    weight1 =. 0 { parameters
    weight2 =. 6 { parameters
    component1_parameters =. 1 2 3 4 5 { parameters
    component2_parameters =. 7 8 9 10 11 { parameters
    density1 =. eqc_density component1_parameters
    density2 =. eqc_density component2_parameters
    weighted1 =. weight1 * density1
    weighted2 =. weight2 * density2
    total_density =. 1e_300 >. weighted1 + weighted2
    update1 =. component_update weighted1 % total_density
    update2 =. component_update weighted2 % total_density
    new_parameters =. update1 , update2
    if. 1e_8 > >./ | new_parameters - parameters do.
      parameters =. new_parameters
      break.
    end.
    parameters =. new_parameters
  end.
  parameters
)

fit_three_em =: 3 : 0
  parameters =. y
  for_iteration. i. 250 do.
    weight1 =. 0 { parameters
    weight2 =. 6 { parameters
    weight3 =. 12 { parameters
    component1_parameters =. 1 2 3 4 5 { parameters
    component2_parameters =. 7 8 9 10 11 { parameters
    component3_parameters =. 13 14 15 16 17 { parameters
    density1 =. eqc_density component1_parameters
    density2 =. eqc_density component2_parameters
    density3 =. eqc_density component3_parameters
    weighted1 =. weight1 * density1
    weighted2 =. weight2 * density2
    weighted3 =. weight3 * density3
    total_density =. 1e_300 >. weighted1 + weighted2 + weighted3
    update1 =. component_update weighted1 % total_density
    update2 =. component_update weighted2 % total_density
    update3 =. component_update weighted3 % total_density
    new_parameters =. update1 , update2 , update3
    if. 1e_8 > >./ | new_parameters - parameters do.
      parameters =. new_parameters
      break.
    end.
    parameters =. new_parameters
  end.
  parameters
)

log_likelihood_one =: 3 : 0
  component_parameters =. 1 2 3 4 5 { y
  density =. eqc_density component_parameters
  +/ ^. 1e_300 >. density
)

log_likelihood_two =: 3 : 0
  component1_parameters =. 1 2 3 4 5 { y
  component2_parameters =. 7 8 9 10 11 { y
  density1 =. (0 { y) * eqc_density component1_parameters
  density2 =. (6 { y) * eqc_density component2_parameters
  +/ ^. 1e_300 >. density1 + density2
)

log_likelihood_three =: 3 : 0
  component1_parameters =. 1 2 3 4 5 { y
  component2_parameters =. 7 8 9 10 11 { y
  component3_parameters =. 13 14 15 16 17 { y
  density1 =. (0 { y) * eqc_density component1_parameters
  density2 =. (6 { y) * eqc_density component2_parameters
  density3 =. (12 { y) * eqc_density component3_parameters
  +/ ^. 1e_300 >. density1 + density2 + density3
)

NB. The one-component M-step also supplies scale information for initialization.
one_fit =: component_update 1 + 0 * xa
global_mua =: 1 { one_fit
global_mub =: 2 { one_fit
global_muc =: 3 { one_fit
global_sigma =: 4 { one_fit

two_initial =: 0.5 , (global_mua - 0.75 * global_sigma) , (global_mub - 0.75 * global_sigma) , (global_muc - 0.75 * global_sigma) , global_sigma , 0.2 , 0.5 , (global_mua + 0.75 * global_sigma) , (global_mub + 0.75 * global_sigma) , (global_muc + 0.75 * global_sigma) , global_sigma , 0.2
two_fit =: fit_two_em two_initial

NB. Split component 2 of the two-component fit to initialize three components.
split_weight =: 0.5 * 6 { two_fit
split_sigma =: 10 { two_fit
three_initial =: (0 { two_fit) , (1 { two_fit) , (2 { two_fit) , (3 { two_fit) , (4 { two_fit) , (5 { two_fit) , split_weight , ((7 { two_fit) - 0.3 * split_sigma) , ((8 { two_fit) - 0.3 * split_sigma) , ((9 { two_fit) - 0.3 * split_sigma) , split_sigma , (11 { two_fit) , split_weight , ((7 { two_fit) + 0.3 * split_sigma) , ((8 { two_fit) + 0.3 * split_sigma) , ((9 { two_fit) + 0.3 * split_sigma) , split_sigma , 11 { two_fit
three_fit =: fit_three_em three_initial

log_likelihoods =: (log_likelihood_one one_fit) , (log_likelihood_two two_fit) , log_likelihood_three three_fit
NB. For k components: k-1 weights and k times (3 means, sigma, rho).
parameter_counts =: 5 11 17
aic =: (2 * parameter_counts) - 2 * log_likelihoods
bic =: ((^. n) * parameter_counts) - 2 * log_likelihoods
aic_components =: +/ (1 + i. 3) * aic = <./ aic
bic_components =: +/ (1 + i. 3) * bic = <./ bic

smoutput 'dimension and sample size'
smoutput dimension , n
smoutput 'number of components: 1 2 3'
smoutput 'log likelihood'
smoutput log_likelihoods
smoutput 'AIC'
smoutput aic
smoutput 'BIC'
smoutput bic
smoutput 'components chosen by AIC'
smoutput aic_components
smoutput 'components chosen by BIC'
smoutput bic_components
smoutput 'true two-component parameters'
smoutput true_weight , (1 - true_weight) , true_means1 , true_sigma1 , true_rho1 , true_means2 , true_sigma2 , true_rho2
smoutput 'fitted two-component parameters'
smoutput two_fit

exit 0
