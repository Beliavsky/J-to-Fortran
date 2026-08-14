NB. Matrix-oriented general-covariance mixture fitting, exercised in 4 dimensions.

n =: 20000
dimension =: 4
component_size =: 1 + dimension + dimension * dimension

NB. Generate a two-component mixture from positive-diagonal Cholesky factors.
true_weight =: 0.4
true_mean1 =: _1 _0.5 0 0.5
true_mean2 =: 2 1.5 1 0.5
true_cholesky1 =: 4 4 $ 0.8 0 0 0  0.2 0.7 0 0  _0.1 0.25 0.6 0  0.1 _0.05 0.2 0.65
true_cholesky2 =: 4 4 $ 1.1 0 0 0  0.4 0.9 0 0  0.2 _0.15 0.8 0  _0.1 0.2 0.3 0.75
true_covariance1 =: true_cholesky1 (+/ . *) |: true_cholesky1
true_covariance2 =: true_cholesky2 (+/ . *) |: true_cholesky2

NB. Box-Muller produces an n by dimension matrix of independent normals.
u1 =: 1e_12 >. ? (n, dimension) $ 0
u2 =: ? (n, dimension) $ 0
z =: (%: _2 * ^. u1) * 2 o. 2 * 1p1 * u2
sample1 =: true_mean1 +"1 z (+/ . *) |: true_cholesky1
sample2 =: true_mean2 +"1 z (+/ . *) |: true_cholesky2
component1 =: (? n $ 0) < true_weight
observations =: (component1 *"0 1 sample1) + (1 - component1) *"0 1 sample2

NB. y is weight, a dimension-vector mean, then a flattened covariance matrix.
mv_density =: 3 : 0
  mean =. (1 + i. dimension) { y
  covariance =. (dimension, dimension) $ (1 + dimension + i. dimension * dimension) { y
  centered =. observations -"1 mean
  inverse =. %. covariance
  quadratic =. +/"1 centered * centered (+/ . *) inverse
  determinant =. 1e_300 >. -/ . * covariance
  normalizer =. ((2 * 1p1) ^ (0.5 * dimension)) * %: determinant
  1e_300 >. (^ _0.5 * quadratic) % normalizer
)

NB. The ridge makes the weighted covariance strictly positive definite.
component_update =: 3 : 0
  weights =. y
  weight_sum =. +/ weights
  weight =. weight_sum % n
  mean =. (weights (+/ . *) observations) % weight_sum
  centered =. observations -"1 mean
  weighted_centered =. weights *"0 1 centered
  covariance =. ((|: centered) (+/ . *) weighted_centered) % weight_sum
  ridge =. 1e_8 * 1 >. (+/ (<0 1) |: covariance) % dimension
  covariance =. covariance + ridge * = i. dimension
  weight , mean , , covariance
)

fit_two_em =: 3 : 0
  parameters =. y
  first =. i. component_size
  second =. component_size + i. component_size
  for_iteration. i. 200 do.
    parameters1 =. first { parameters
    parameters2 =. second { parameters
    weighted1 =. (0 { parameters1) * mv_density parameters1
    weighted2 =. (0 { parameters2) * mv_density parameters2
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
  first =. i. component_size
  second =. component_size + i. component_size
  third =. (2 * component_size) + i. component_size
  for_iteration. i. 250 do.
    parameters1 =. first { parameters
    parameters2 =. second { parameters
    parameters3 =. third { parameters
    weighted1 =. (0 { parameters1) * mv_density parameters1
    weighted2 =. (0 { parameters2) * mv_density parameters2
    weighted3 =. (0 { parameters3) * mv_density parameters3
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
  +/ ^. mv_density y
)

log_likelihood_two =: 3 : 0
  first =. i. component_size
  second =. component_size + i. component_size
  parameters1 =. first { y
  parameters2 =. second { y
  density1 =. (0 { parameters1) * mv_density parameters1
  density2 =. (0 { parameters2) * mv_density parameters2
  +/ ^. 1e_300 >. density1 + density2
)

log_likelihood_three =: 3 : 0
  first =. i. component_size
  second =. component_size + i. component_size
  third =. (2 * component_size) + i. component_size
  parameters1 =. first { y
  parameters2 =. second { y
  parameters3 =. third { y
  density1 =. (0 { parameters1) * mv_density parameters1
  density2 =. (0 { parameters2) * mv_density parameters2
  density3 =. (0 { parameters3) * mv_density parameters3
  +/ ^. 1e_300 >. density1 + density2 + density3
)

NB. Fit one component, then initialize two separated components.
one_fit =: component_update 1 + 0 * component1
global_mean =: (1 + i. dimension) { one_fit
initial_covariance =: 0.8 * = i. dimension
direction =: _1.5 _1 _0.5 0
parameters1 =: 0.4 , (global_mean + direction) , , initial_covariance
parameters2 =: 0.6 , (global_mean - 0.6666666667 * direction) , , initial_covariance
two_fit =: fit_two_em parameters1 , parameters2

NB. Split the second fitted component to initialize three components.
first =: i. component_size
second =: component_size + i. component_size
fitted1 =: first { two_fit
fitted2 =: second { two_fit
split_weight =: 0.5 * 0 { fitted2
split_direction =: 0.2 _0.2 0.2 _0.2
split1 =: split_weight , (((1 + i. dimension) { fitted2) + split_direction) , (1 + dimension + i. dimension * dimension) { fitted2
split2 =: split_weight , (((1 + i. dimension) { fitted2) - split_direction) , (1 + dimension + i. dimension * dimension) { fitted2
three_fit =: fit_three_em fitted1 , split1 , split2

log_likelihoods =: (log_likelihood_one one_fit) , (log_likelihood_two two_fit) , log_likelihood_three three_fit
parameters_per_component =: dimension + (dimension * (dimension + 1)) % 2
parameter_counts =: (0 1 2) + (1 2 3) * parameters_per_component
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
smoutput 'true means'
smoutput true_mean1
smoutput true_mean2
smoutput 'fitted two-component parameter blocks'
smoutput fitted1
smoutput fitted2

exit 0
