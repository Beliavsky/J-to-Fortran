NB. List Pythagorean triples with hypotenuse <= y

triples =: 3 : 0
  result =. 0 3 $ 0

  for_c. 1 + i. y do.
    for_b. 1 + i. c - 1 do.
      for_a. 1 + i. b - 1 do.
        if. ((a * a) + (b * b)) = c * c do.
          result =. result , a , b , c
        end.
      end.
    end.
  end.

  result
)

echo triples 30

exit 0
