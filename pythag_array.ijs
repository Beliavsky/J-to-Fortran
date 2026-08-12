  triples =: 3 : 0
    ab =. > , { 2 # < 1 + i. y
    a =. 0 {"1 ab
    b =. 1 {"1 ab

    sumsq =. (a * a) + (b * b)
    c =. <. %: sumsq

    keep =. (a < b) *. (sumsq = c * c) *. (c <: y)
    keep # ab ,. c
  )

  echo triples 30
  exit 0
