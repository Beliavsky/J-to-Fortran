NB. Simulate and fit 3-dimensional normal mixtures with general covariance matrices.

n =: 50000
dimension =: 3

NB. The sample comes from a two-component normal mixture.
true_weight =: 0.4
true_means1 =: _1 _0.5 0
true_means2 =: 2 1.5 1

NB. Lower triangular Cholesky factors guarantee positive definite covariances.
l11_1 =: 0.8
l21_1 =: 0.2
l22_1 =: 0.7
l31_1 =: _0.1
l32_1 =: 0.25
l33_1 =: 0.6

l11_2 =: 1.1
l21_2 =: 0.4
l22_2 =: 0.9
l31_2 =: 0.2
l32_2 =: _0.15
l33_2 =: 0.8

NB. Covariance order is s11, s22, s33, s12, s13, s23.
true_covariance1 =: (l11_1 ^ 2) , ((l21_1 ^ 2) + l22_1 ^ 2) , ((l31_1 ^ 2) + (l32_1 ^ 2) + l33_1 ^ 2) , (l11_1 * l21_1) , (l11_1 * l31_1) , (l21_1 * l31_1) + l22_1 * l32_1
true_covariance2 =: (l11_2 ^ 2) , ((l21_2 ^ 2) + l22_2 ^ 2) , ((l31_2 ^ 2) + (l32_2 ^ 2) + l33_2 ^ 2) , (l11_2 * l21_2) , (l11_2 * l31_2) , (l21_2 * l31_2) + l22_2 * l32_2

NB. Generate three independent standard normal vectors by Box-Muller.
u1 =: 1e_12 >. ? n $ 0
u2 =: ? n $ 0
z1 =: (%: _2 * ^. u1) * 2 o. 2 * 1p1 * u2
u3 =: 1e_12 >. ? n $ 0
u4 =: ? n $ 0
z2 =: (%: _2 * ^. u3) * 2 o. 2 * 1p1 * u4
u5 =: 1e_12 >. ? n $ 0
u6 =: ? n $ 0
z3 =: (%: _2 * ^. u5) * 2 o. 2 * 1p1 * u6

NB. Transform independent normals by each component's Cholesky factor.
xa1 =: (0 { true_means1) + l11_1 * z1
xb1 =: (1 { true_means1) + (l21_1 * z1) + l22_1 * z2
xc1 =: (2 { true_means1) + (l31_1 * z1) + (l32_1 * z2) + l33_1 * z3
xa2 =: (0 { true_means2) + l11_2 * z1
xb2 =: (1 { true_means2) + (l21_2 * z1) + l22_2 * z2
xc2 =: (2 { true_means2) + (l31_2 * z1) + (l32_2 * z2) + l33_2 * z3

component1 =: (? n $ 0) < true_weight
xa =: (component1 * xa1) + (1 - component1) * xa2
xb =: (component1 * xb1) + (1 - component1) * xb2
xc =: (component1 * xc1) + (1 - component1) * xc2

NB. Sylvester's criterion for a symmetric 3 by 3 covariance matrix.
covariance_is_pd =: 3 : 0
  s11 =. 0 { y
  s22 =. 1 { y
  s33 =. 2 { y
  s12 =. 3 { y
  s13 =. 4 { y
  s23 =. 5 { y
  leading2 =. (s11 * s22) - s12 ^ 2
  positive_terms =. (s11 * s22 * s33) + 2 * s12 * s13 * s23
  negative_terms =. (s11 * s23 ^ 2) + (s22 * s13 ^ 2) + s33 * s12 ^ 2
  determinant =. positive_terms - negative_terms
  (s11 > 0) *. (leading2 > 0) *. determinant > 0
)

NB. y contains three means followed by six unique covariance entries.
mv_density =: 3 : 0
  mua =. 0 { y
  mub =. 1 { y
  muc =. 2 { y
  s11 =. 3 { y
  s22 =. 4 { y
  s33 =. 5 { y
  s12 =. 6 { y
  s13 =. 7 { y
  s23 =. 8 { y
  da =. xa - mua
  db =. xb - mub
  dc =. xc - muc

  c11 =. (s22 * s33) - s23 ^ 2
  c22 =. (s11 * s33) - s13 ^ 2
  c33 =. (s11 * s22) - s12 ^ 2
  c12 =. (s13 * s23) - s12 * s33
  c13 =. (s12 * s23) - s13 * s22
  c23 =. (s12 * s13) - s11 * s23
  positive_terms =. (s11 * s22 * s33) + 2 * s12 * s13 * s23
  negative_terms =. (s11 * s23 ^ 2) + (s22 * s13 ^ 2) + s33 * s12 ^ 2
  determinant =. 1e_300 >. positive_terms - negative_terms
  quadratic =. ((c11 * da ^ 2) + (c22 * db ^ 2) + (c33 * dc ^ 2) + (2 * c12 * da * db) + (2 * c13 * da * dc) + 2 * c23 * db * dc) % determinant
  1e_300 >. (^ _0.5 * quadratic) % 15.7496099457 * %: determinant
)

NB. A weighted M-step returns weight, means, and a regularized covariance.
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
  s11 =. (+/ responsibility * da ^ 2) % responsibility_sum
  s22 =. (+/ responsibility * db ^ 2) % responsibility_sum
  s33 =. (+/ responsibility * dc ^ 2) % responsibility_sum
  s12 =. (+/ responsibility * da * db) % responsibility_sum
  s13 =. (+/ responsibility * da * dc) % responsibility_sum
  s23 =. (+/ responsibility * db * dc) % responsibility_sum
  ridge =. 1e_8 * 1 >. (s11 + s22 + s33) % 3
  s11 =. s11 + ridge
  s22 =. s22 + ridge
  s33 =. s33 + ridge
  weight , mua , mub , muc , s11 , s22 , s33 , s12 , s13 , s23
)

fit_two_em =: 3 : 0
  parameters =. y
  for_iteration. i. 200 do.
    weight1 =. 0 { parameters
    weight2 =. 10 { parameters
    component1_parameters =. 1 2 3 4 5 6 7 8 9 { parameters
    component2_parameters =. 11 12 13 14 15 16 17 18 19 { parameters
    density1 =. mv_density component1_parameters
    density2 =. mv_density component2_parameters
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
    weight2 =. 10 { parameters
    weight3 =. 20 { parameters
    component1_parameters =. 1 2 3 4 5 6 7 8 9 { parameters
    component2_parameters =. 11 12 13 14 15 16 17 18 19 { parameters
    component3_parameters =. 21 22 23 24 25 26 27 28 29 { parameters
    density1 =. mv_density component1_parameters
    density2 =. mv_density component2_parameters
    density3 =. mv_density component3_parameters
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
  component_parameters =. 1 2 3 4 5 6 7 8 9 { y
  density =. mv_density component_parameters
  +/ ^. density
)

log_likelihood_two =: 3 : 0
  component1_parameters =. 1 2 3 4 5 6 7 8 9 { y
  component2_parameters =. 11 12 13 14 15 16 17 18 19 { y
  density1 =. (0 { y) * mv_density component1_parameters
  density2 =. (10 { y) * mv_density component2_parameters
  +/ ^. 1e_300 >. density1 + density2
)

log_likelihood_three =: 3 : 0
  component1_parameters =. 1 2 3 4 5 6 7 8 9 { y
  component2_parameters =. 11 12 13 14 15 16 17 18 19 { y
  component3_parameters =. 21 22 23 24 25 26 27 28 29 { y
  density1 =. (0 { y) * mv_density component1_parameters
  density2 =. (10 { y) * mv_density component2_parameters
  density3 =. (20 { y) * mv_density component3_parameters
  +/ ^. 1e_300 >. density1 + density2 + density3
)

NB. Start with the empirical mean and covariance, then split along all coordinates.
one_fit =: component_update 1 + 0 * xa
global_mua =: 1 { one_fit
global_mub =: 2 { one_fit
global_muc =: 3 { one_fit
initial_covariance =: 0.7 0.7 0.7 0 0 0

two_initial =: 0.4 , (global_mua - 1.8) , (global_mub - 1.2) , (global_muc - 0.6) , initial_covariance , 0.6 , (global_mua + 1.2) , (global_mub + 0.8) , (global_muc + 0.4) , initial_covariance
two_fit =: fit_two_em two_initial

NB. Split component 2 of the fitted two-component model.
split_weight =: 0.5 * 10 { two_fit
split_scale =: %: ((14 { two_fit) + (15 { two_fit) + 16 { two_fit) % 3
split_covariance =: 14 15 16 17 18 19 { two_fit
three_initial =: (0 1 2 3 4 5 6 7 8 9 { two_fit) , split_weight , ((11 { two_fit) - 0.3 * split_scale) , ((12 { two_fit) - 0.3 * split_scale) , ((13 { two_fit) - 0.3 * split_scale) , split_covariance , split_weight , ((11 { two_fit) + 0.3 * split_scale) , ((12 { two_fit) + 0.3 * split_scale) , ((13 { two_fit) + 0.3 * split_scale) , split_covariance
three_fit =: fit_three_em three_initial

log_likelihoods =: (log_likelihood_one one_fit) , (log_likelihood_two two_fit) , log_likelihood_three three_fit
NB. For k components: k-1 weights and k times (3 means and 6 covariances).
parameter_counts =: 9 19 29
aic =: (2 * parameter_counts) - 2 * log_likelihoods
bic =: ((^. n) * parameter_counts) - 2 * log_likelihoods
aic_components =: +/ (1 + i. 3) * aic = <./ aic
bic_components =: +/ (1 + i. 3) * bic = <./ bic

true_pd =: (covariance_is_pd true_covariance1) , covariance_is_pd true_covariance2
fitted_covariance1 =: 4 5 6 7 8 9 { two_fit
fitted_covariance2 =: 14 15 16 17 18 19 { two_fit
fitted_pd =: (covariance_is_pd fitted_covariance1) , covariance_is_pd fitted_covariance2

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
smoutput 'true covariance matrices are positive definite'
smoutput true_pd
smoutput 'fitted covariance matrices are positive definite'
smoutput fitted_pd
smoutput 'true component 1: mean then covariance entries'
smoutput true_means1 , true_covariance1
smoutput 'fitted component 1: weight mean then covariance entries'
smoutput 0 1 2 3 4 5 6 7 8 9 { two_fit
smoutput 'true component 2: mean then covariance entries'
smoutput true_means2 , true_covariance2
smoutput 'fitted component 2: weight mean then covariance entries'
smoutput 10 11 12 13 14 15 16 17 18 19 { two_fit

exit 0
