NB. Fit one, two, and three normal components and compare AIC and BIC.

n =: 200000

NB. Parameters used to generate a two-component sample.
true_weight =: 0.35
true_mu1 =: _1
true_sd1 =: 0.8
true_mu2 =: 2
true_sd2 =: 1.3

NB. Generate two independent standard normal samples by Box-Muller.
u1 =: 1e_12 >. ? n $ 0
u2 =: ? n $ 0
z1 =: (%: _2 * ^. u1) * 2 o. 2 * 1p1 * u2

u3 =: 1e_12 >. ? n $ 0
u4 =: ? n $ 0
z2 =: (%: _2 * ^. u3) * 2 o. 2 * 1p1 * u4

component1 =: (? n $ 0) < true_weight
x1 =: true_mu1 + true_sd1 * z1
x2 =: true_mu2 + true_sd2 * z2
x =: (component1 * x1) + (1 - component1) * x2

mean =: +/ % #
sample_moments =: (mean x) , (mean x ^ 2) , (mean x ^ 3) , (mean x ^ 4) , mean x ^ 5
moment_scale =: 1 >. | sample_moments
normal_constant =: %: 2 * 1p1

NB. Moment matching initializes the two-component EM fit.
mixture_moments =: 3 : 0
  alpha =. 0 { y
  mu1 =. 1 { y
  sd1 =. ^ 2 { y
  mu2 =. 3 { y
  sd2 =. ^ 4 { y
  weight =. 1 % 1 + ^ -alpha
  other_weight =. 1 - weight

  component1_moments =. mu1 , ((mu1 ^ 2) + sd1 ^ 2) , ((mu1 ^ 3) + 3 * mu1 * sd1 ^ 2) , ((mu1 ^ 4) + (6 * (mu1 ^ 2) * sd1 ^ 2) + 3 * sd1 ^ 4) , ((mu1 ^ 5) + (10 * (mu1 ^ 3) * sd1 ^ 2) + 15 * mu1 * sd1 ^ 4)
  component2_moments =. mu2 , ((mu2 ^ 2) + sd2 ^ 2) , ((mu2 ^ 3) + 3 * mu2 * sd2 ^ 2) , ((mu2 ^ 4) + (6 * (mu2 ^ 2) * sd2 ^ 2) + 3 * sd2 ^ 4) , ((mu2 ^ 5) + (10 * (mu2 ^ 3) * sd2 ^ 2) + 15 * mu2 * sd2 ^ 4)

  (weight * component1_moments) + other_weight * component2_moments
)

moment_jacobian =: 3 : 0
  step_size =. 1e_5
  base =. mixture_moments y
  jacobian =. 5 5 $ 0.0
  for_parameter. i. 5 do.
    shifted =. y + step_size * parameter = i. 5
    column =. ((mixture_moments shifted) - base) % step_size * moment_scale
    jacobian =. jacobian + column */ (parameter = i. 5)
  end.
  jacobian
)

fit_mixture_moments =: 3 : 0
  parameters =. y
  identity =. = i. 5
  damping =. 0.01
  for_iteration. i. 100 do.
    residual =. (sample_moments - mixture_moments parameters) % moment_scale
    jacobian =. moment_jacobian parameters
    normal_matrix =. ((|: jacobian) (+/ . *) jacobian) + damping * identity
    normal_rhs =. (|: jacobian) (+/ . *) residual
    update =. normal_rhs %. normal_matrix
    parameters =. parameters + update
    if. 1e_9 > >./ | update do.
      break.
    end.
  end.
  parameters
)

NB. Natural parameters are weight, mu1, sd1, mu2, sd2.
fit_two_em =: 3 : 0
  parameters =. y
  for_iteration. i. 200 do.
    weight =. 0 { parameters
    mu1 =. 1 { parameters
    sd1 =. 2 { parameters
    mu2 =. 3 { parameters
    sd2 =. 4 { parameters

    density1 =. (^ _0.5 * ((x - mu1) % sd1) ^ 2) % normal_constant * sd1
    density2 =. (^ _0.5 * ((x - mu2) % sd2) ^ 2) % normal_constant * sd2
    weighted1 =. weight * density1
    weighted2 =. (1 - weight) * density2
    responsibility =. weighted1 % 1e_300 >. weighted1 + weighted2
    other_responsibility =. 1 - responsibility

    responsibility_sum =. +/ responsibility
    other_sum =. +/ other_responsibility
    new_weight =. mean responsibility
    new_mu1 =. (+/ responsibility * x) % responsibility_sum
    new_mu2 =. (+/ other_responsibility * x) % other_sum
    new_sd1 =. 1e_8 >. %: ((+/ responsibility * (x - new_mu1) ^ 2) % responsibility_sum)
    new_sd2 =. 1e_8 >. %: ((+/ other_responsibility * (x - new_mu2) ^ 2) % other_sum)

    new_parameters =. new_weight , new_mu1 , new_sd1 , new_mu2 , new_sd2
    if. 1e_9 > >./ | new_parameters - parameters do.
      parameters =. new_parameters
      break.
    end.
    parameters =. new_parameters
  end.
  parameters
)

NB. Three-component parameters are weights, means, then standard deviations.
fit_three_em =: 3 : 0
  parameters =. y
  for_iteration. i. 300 do.
    weight1 =. 0 { parameters
    weight2 =. 1 { parameters
    weight3 =. 2 { parameters
    mu1 =. 3 { parameters
    mu2 =. 4 { parameters
    mu3 =. 5 { parameters
    sd1 =. 6 { parameters
    sd2 =. 7 { parameters
    sd3 =. 8 { parameters

    density1 =. (^ _0.5 * ((x - mu1) % sd1) ^ 2) % normal_constant * sd1
    density2 =. (^ _0.5 * ((x - mu2) % sd2) ^ 2) % normal_constant * sd2
    density3 =. (^ _0.5 * ((x - mu3) % sd3) ^ 2) % normal_constant * sd3
    weighted1 =. weight1 * density1
    weighted2 =. weight2 * density2
    weighted3 =. weight3 * density3
    total_density =. 1e_300 >. weighted1 + weighted2 + weighted3
    responsibility1 =. weighted1 % total_density
    responsibility2 =. weighted2 % total_density
    responsibility3 =. weighted3 % total_density

    sum1 =. +/ responsibility1
    sum2 =. +/ responsibility2
    sum3 =. +/ responsibility3
    new_weight1 =. mean responsibility1
    new_weight2 =. mean responsibility2
    new_weight3 =. mean responsibility3
    new_mu1 =. (+/ responsibility1 * x) % sum1
    new_mu2 =. (+/ responsibility2 * x) % sum2
    new_mu3 =. (+/ responsibility3 * x) % sum3
    new_sd1 =. 1e_8 >. %: ((+/ responsibility1 * (x - new_mu1) ^ 2) % sum1)
    new_sd2 =. 1e_8 >. %: ((+/ responsibility2 * (x - new_mu2) ^ 2) % sum2)
    new_sd3 =. 1e_8 >. %: ((+/ responsibility3 * (x - new_mu3) ^ 2) % sum3)

    new_parameters =. new_weight1 , new_weight2 , new_weight3 , new_mu1 , new_mu2 , new_mu3 , new_sd1 , new_sd2 , new_sd3
    if. 1e_9 > >./ | new_parameters - parameters do.
      parameters =. new_parameters
      break.
    end.
    parameters =. new_parameters
  end.
  parameters
)

log_likelihood_one =: 3 : 0
  mu =. 0 { y
  sd =. 1 { y
  density =. (^ _0.5 * ((x - mu) % sd) ^ 2) % normal_constant * sd
  +/ ^. 1e_300 >. density
)

log_likelihood_two =: 3 : 0
  weight =. 0 { y
  mu1 =. 1 { y
  sd1 =. 2 { y
  mu2 =. 3 { y
  sd2 =. 4 { y
  density1 =. (^ _0.5 * ((x - mu1) % sd1) ^ 2) % normal_constant * sd1
  density2 =. (^ _0.5 * ((x - mu2) % sd2) ^ 2) % normal_constant * sd2
  +/ ^. 1e_300 >. (weight * density1) + (1 - weight) * density2
)

log_likelihood_three =: 3 : 0
  weight1 =. 0 { y
  weight2 =. 1 { y
  weight3 =. 2 { y
  mu1 =. 3 { y
  mu2 =. 4 { y
  mu3 =. 5 { y
  sd1 =. 6 { y
  sd2 =. 7 { y
  sd3 =. 8 { y
  density1 =. (^ _0.5 * ((x - mu1) % sd1) ^ 2) % normal_constant * sd1
  density2 =. (^ _0.5 * ((x - mu2) % sd2) ^ 2) % normal_constant * sd2
  density3 =. (^ _0.5 * ((x - mu3) % sd3) ^ 2) % normal_constant * sd3
  +/ ^. 1e_300 >. (weight1 * density1) + (weight2 * density2) + weight3 * density3
)

NB. Fit one component directly and two components from matched moments.
one_mu =: mean x
one_sd =: %: mean (x - one_mu) ^ 2
one_fit =: one_mu , one_sd

moment_initial =: _0.4 _0.7 _0.1 1.7 0.1
moment_internal =: fit_mixture_moments moment_initial
two_initial =: (1 % 1 + ^ -0 { moment_internal) , (1 { moment_internal) , (^ 2 { moment_internal) , (3 { moment_internal) , ^ 4 { moment_internal
two_fit =: fit_two_em two_initial

NB. Initialize three components by splitting component 2 of the two-component fit.
split_weight =: 0.5 * 1 - 0 { two_fit
split_mu =: 3 { two_fit
split_sd =: 4 { two_fit
three_initial =: (0 { two_fit) , split_weight , split_weight , (1 { two_fit) , (split_mu - 0.5 * split_sd) , (split_mu + 0.5 * split_sd) , (2 { two_fit) , split_sd , split_sd
three_fit =: fit_three_em three_initial

log_likelihoods =: (log_likelihood_one one_fit) , (log_likelihood_two two_fit) , log_likelihood_three three_fit
parameter_counts =: 2 5 8
aic =: (2 * parameter_counts) - 2 * log_likelihoods
bic =: ((^. n) * parameter_counts) - 2 * log_likelihoods
aic_components =: +/ (1 + i. 3) * aic = <./ aic
bic_components =: +/ (1 + i. 3) * bic = <./ bic

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
smoutput 'two-component estimate: weight mu1 sd1 mu2 sd2'
smoutput two_fit
smoutput 'three-component estimate: weights means standard deviations'
smoutput three_fit

exit 0
