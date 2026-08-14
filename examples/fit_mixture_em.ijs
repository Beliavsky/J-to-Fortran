NB. Fit a two-normal mixture with EM, initialized by matching moments 1 through 5.

n =: 200000

NB. Parameters used to generate the sample.
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

NB. Select a component independently for each observation.
component1 =: (? n $ 0) < true_weight
x1 =: true_mu1 + true_sd1 * z1
x2 =: true_mu2 + true_sd2 * z2
x =: (component1 * x1) + (1 - component1) * x2

mean =: +/ % #
sample_moments =: (mean x) , (mean x ^ 2) , (mean x ^ 3) , (mean x ^ 4) , mean x ^ 5
moment_scale =: 1 >. | sample_moments

NB. Moment matching uses alpha, mu1, log(sd1), mu2, log(sd2).
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

NB. Damped Gauss-Newton supplies the initial estimate for EM.
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

NB. EM parameters are weight, mu1, sd1, mu2, sd2.
fit_mixture_em =: 3 : 0
  parameters =. y
  for_iteration. i. 200 do.
    weight =. 0 { parameters
    mu1 =. 1 { parameters
    sd1 =. 2 { parameters
    mu2 =. 3 { parameters
    sd2 =. 4 { parameters

    density1 =. (^ _0.5 * ((x - mu1) % sd1) ^ 2) % sd1
    density2 =. (^ _0.5 * ((x - mu2) % sd2) ^ 2) % sd2
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

NB. Convert the moment fit to EM's natural parameterization.
moment_initial =: _0.4 _0.7 _0.1 1.7 0.1
moment_internal =: fit_mixture_moments moment_initial
em_initial =: (1 % 1 + ^ -0 { moment_internal) , (1 { moment_internal) , (^ 2 { moment_internal) , (3 { moment_internal) , ^ 4 { moment_internal
em_fitted =: fit_mixture_em em_initial

fitted_weight =: 0 { em_fitted
fitted_mu1 =: 1 { em_fitted
fitted_sd1 =: 2 { em_fitted
fitted_mu2 =: 3 { em_fitted
fitted_sd2 =: 4 { em_fitted

smoutput 'sample size'
smoutput n
smoutput 'parameter: EM fitted theoretical difference'

smoutput 'component 1 weight'
smoutput fitted_weight, true_weight, fitted_weight - true_weight

smoutput 'component 1 mean'
smoutput fitted_mu1, true_mu1, fitted_mu1 - true_mu1

smoutput 'component 1 standard deviation'
smoutput fitted_sd1, true_sd1, fitted_sd1 - true_sd1

smoutput 'component 2 mean'
smoutput fitted_mu2, true_mu2, fitted_mu2 - true_mu2

smoutput 'component 2 standard deviation'
smoutput fitted_sd2, true_sd2, fitted_sd2 - true_sd2

smoutput 'moment-matching initial estimate'
smoutput em_initial

smoutput 'EM estimate'
smoutput em_fitted

exit 0
