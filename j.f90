module j2f_runtime
  use, intrinsic :: iso_fortran_env, only: real64
  implicit none
  private
  public :: j_addition_table_int, j_append_int_row, j_binomial
  public :: j_cartesian_square, j_compress_hcat
  public :: j_copy_int_vector, j_factorial, j_grade_up_int, j_index_of_int
  public :: j_infix_subtract_int, j_infix_sum_int, j_iota, j_match_real
  public :: j_membership_int, j_multiplication_table_int, j_nub_int
  public :: j_power_table_int, j_prefix_product_int, j_prefix_sum_int
  public :: j_reverse_int_vector, j_signum_int, j_sort_int_vector
  public :: j_reverse_character
  public :: j_select_character
  public :: j_solve_2x2_matrix_int, j_solve_2x2_vector_int

contains

pure function j_addition_table_int(values) result(table_values)
  integer, intent(in) :: values(:)
  integer, allocatable :: table_values(:,:)
  integer :: row_index

  allocate(table_values(size(values), size(values)))
  do row_index = 1, size(values)
    table_values(row_index, :) = values(row_index) + values
  end do
end function j_addition_table_int

pure function j_multiplication_table_int(left, right) result(table_values)
  integer, intent(in) :: left(:), right(:)
  integer, allocatable :: table_values(:,:)
  integer :: row_index

  allocate(table_values(size(left), size(right)))
  do row_index = 1, size(left)
    table_values(row_index, :) = left(row_index) * right
  end do
end function j_multiplication_table_int

pure function j_power_table_int(bases, exponents) result(table_values)
  integer, intent(in) :: bases(:), exponents(:)
  integer, allocatable :: table_values(:,:)
  integer :: row_index

  if (any(exponents < 0)) error stop "negative integer table exponent"
  allocate(table_values(size(bases), size(exponents)))
  do row_index = 1, size(bases)
    table_values(row_index, :) = bases(row_index)**exponents
  end do
end function j_power_table_int

pure function j_prefix_sum_int(values) result(prefixes)
  integer, intent(in) :: values(:)
  integer, allocatable :: prefixes(:)
  integer :: value_index

  allocate(prefixes(size(values)))
  if (size(values) > 0) prefixes(1) = values(1)
  do value_index = 2, size(values)
    prefixes(value_index) = prefixes(value_index - 1) + values(value_index)
  end do
end function j_prefix_sum_int

pure function j_prefix_product_int(values) result(prefixes)
  integer, intent(in) :: values(:)
  integer, allocatable :: prefixes(:)
  integer :: value_index

  allocate(prefixes(size(values)))
  if (size(values) > 0) prefixes(1) = values(1)
  do value_index = 2, size(values)
    prefixes(value_index) = prefixes(value_index - 1) * values(value_index)
  end do
end function j_prefix_product_int

pure function j_infix_sum_int(values, width) result(sums)
  integer, intent(in) :: values(:), width
  integer, allocatable :: sums(:)
  integer :: window_start

  if (width <= 0 .or. width > size(values)) error stop "invalid infix width"
  allocate(sums(size(values) - width + 1))
  do window_start = 1, size(sums)
    sums(window_start) = sum(values(window_start:window_start + width - 1))
  end do
end function j_infix_sum_int

pure function j_infix_subtract_int(values, width) result(differences)
  integer, intent(in) :: values(:), width
  integer, allocatable :: differences(:)
  integer :: offset, reduced_value, window_start

  if (width <= 0 .or. width > size(values)) error stop "invalid infix width"
  allocate(differences(size(values) - width + 1))
  do window_start = 1, size(differences)
    reduced_value = values(window_start + width - 1)
    do offset = width - 2, 0, -1
      reduced_value = values(window_start + offset) - reduced_value
    end do
    differences(window_start) = reduced_value
  end do
end function j_infix_subtract_int

pure function j_nub_int(values) result(unique_values)
  integer, intent(in) :: values(:)
  integer, allocatable :: unique_values(:)
  integer, allocatable :: workspace(:)
  integer :: unique_count, value_index

  allocate(workspace(size(values)))
  unique_count = 0
  do value_index = 1, size(values)
    if (unique_count == 0 .or. &
        .not. any(workspace(1:unique_count) == values(value_index))) then
      unique_count = unique_count + 1
      workspace(unique_count) = values(value_index)
    end if
  end do
  unique_values = workspace(1:unique_count)
end function j_nub_int

pure function j_membership_int(queries, values) result(is_member)
  integer, intent(in) :: queries(:), values(:)
  logical, allocatable :: is_member(:)
  integer :: query_index

  allocate(is_member(size(queries)))
  do query_index = 1, size(queries)
    is_member(query_index) = any(values == queries(query_index))
  end do
end function j_membership_int

pure function j_index_of_int(values, queries) result(indices)
  integer, intent(in) :: values(:), queries(:)
  integer, allocatable :: indices(:)
  integer :: query_index, value_index

  allocate(indices(size(queries)))
  indices = size(values)
  do query_index = 1, size(queries)
    do value_index = 1, size(values)
      if (queries(query_index) == values(value_index)) then
        indices(query_index) = value_index - 1
        exit
      end if
    end do
  end do
end function j_index_of_int

pure function j_grade_up_int(values) result(indices)
  integer, intent(in) :: values(:)
  integer, allocatable :: indices(:)
  integer :: current_index, position, scan_position

  allocate(indices(size(values)))
  do position = 1, size(values)
    indices(position) = position - 1
  end do
  do position = 2, size(values)
    current_index = indices(position)
    scan_position = position - 1
    do while (scan_position >= 1)
      if (values(indices(scan_position) + 1) <= &
          values(current_index + 1)) exit
      indices(scan_position + 1) = indices(scan_position)
      scan_position = scan_position - 1
    end do
    indices(scan_position + 1) = current_index
  end do
end function j_grade_up_int

pure function j_sort_int_vector(values, descending) result(sorted_values)
  integer, intent(in) :: values(:)
  logical, intent(in) :: descending
  integer, allocatable :: sorted_values(:)
  integer :: current_value, position, scan_position

  sorted_values = values
  do position = 2, size(sorted_values)
    current_value = sorted_values(position)
    scan_position = position - 1
    do while (scan_position >= 1)
      if (descending) then
        if (sorted_values(scan_position) >= current_value) exit
      else
        if (sorted_values(scan_position) <= current_value) exit
      end if
      sorted_values(scan_position + 1) = sorted_values(scan_position)
      scan_position = scan_position - 1
    end do
    sorted_values(scan_position + 1) = current_value
  end do
end function j_sort_int_vector

pure function j_reverse_character(values) result(reversed)
  character(len=*), intent(in) :: values
  character(len=:), allocatable :: reversed
  integer :: character_index

  allocate(character(len=len(values)) :: reversed)
  do character_index = 1, len(values)
    reversed(character_index:character_index) = &
      values(len(values) - character_index + 1:len(values) - character_index + 1)
  end do
end function j_reverse_character

pure function j_select_character(values, indices) result(selected)
  character(len=*), intent(in) :: values
  integer, intent(in) :: indices(:)
  character(len=:), allocatable :: selected
  integer :: index_position

  if (any(indices < 1 .or. indices > len(values))) error stop &
    "character index out of bounds"
  allocate(character(len=size(indices)) :: selected)
  do index_position = 1, size(indices)
    selected(index_position:index_position) = &
      values(indices(index_position):indices(index_position))
  end do
end function j_select_character

pure function j_reverse_int_vector(values) result(reversed_values)
  integer, intent(in) :: values(:)
  integer, allocatable :: reversed_values(:)
  integer :: value_index

  allocate(reversed_values(size(values)))
  do value_index = 1, size(values)
    reversed_values(value_index) = values(size(values) - value_index + 1)
  end do
end function j_reverse_int_vector

pure elemental function j_factorial(n) result(value)
  integer, intent(in) :: n
  integer :: value
  integer :: factor

  if (n < 0) error stop "factorial requires a nonnegative integer"
  value = 1
  do factor = 2, n
    value = value * factor
  end do
end function j_factorial

pure elemental function j_binomial(k, n) result(value)
  integer, intent(in) :: k, n
  integer :: value
  integer :: factor, smaller_k

  if (k < 0 .or. n < 0) error stop "binomial requires nonnegative integers"
  if (k > n) then
    value = 0
    return
  end if
  smaller_k = min(k, n - k)
  value = 1
  do factor = 1, smaller_k
    value = value * (n - factor + 1) / factor
  end do
end function j_binomial

pure elemental function j_signum_int(n) result(value)
  integer, intent(in) :: n
  integer :: value

  if (n < 0) then
    value = -1
  else if (n > 0) then
    value = 1
  else
    value = 0
  end if
end function j_signum_int

pure function j_solve_2x2_vector_int(rhs, coefficients) result(solution)
  integer, intent(in) :: rhs(2), coefficients(2,2)
  real(kind=real64) :: solution(2)
  real(kind=real64) :: determinant

  determinant = real(coefficients(1, 1), kind=real64) * &
    coefficients(2, 2) - real(coefficients(1, 2), kind=real64) * &
    coefficients(2, 1)
  if (determinant == 0.0_real64) error stop "singular 2 by 2 matrix"
  solution(1) = (real(coefficients(2, 2), kind=real64) * rhs(1) - &
    real(coefficients(1, 2), kind=real64) * rhs(2)) / determinant
  solution(2) = (real(coefficients(1, 1), kind=real64) * rhs(2) - &
    real(coefficients(2, 1), kind=real64) * rhs(1)) / determinant
end function j_solve_2x2_vector_int

pure function j_solve_2x2_matrix_int(rhs, coefficients) result(solution)
  integer, intent(in) :: rhs(:,:), coefficients(2,2)
  real(kind=real64), allocatable :: solution(:,:)
  real(kind=real64) :: determinant

  if (size(rhs, 1) /= 2) error stop "2 by 2 solve shape mismatch"
  determinant = real(coefficients(1, 1), kind=real64) * &
    coefficients(2, 2) - real(coefficients(1, 2), kind=real64) * &
    coefficients(2, 1)
  if (determinant == 0.0_real64) error stop "singular 2 by 2 matrix"
  allocate(solution(2, size(rhs, 2)))
  solution(1, :) = (real(coefficients(2, 2), kind=real64) * rhs(1, :) - &
    real(coefficients(1, 2), kind=real64) * rhs(2, :)) / determinant
  solution(2, :) = (real(coefficients(1, 1), kind=real64) * rhs(2, :) - &
    real(coefficients(2, 1), kind=real64) * rhs(1, :)) / determinant
end function j_solve_2x2_matrix_int

pure elemental function j_match_real(left, right) result(matches)
  real(kind=real64), intent(in) :: left, right
  logical :: matches

  matches = abs(left - right) <= &
    2.0_real64**(-44) * max(abs(left), abs(right))
end function j_match_real

pure function j_iota(n) result(values)
  integer, intent(in) :: n
  integer, allocatable :: values(:)
  integer :: value_index

  if (n < 0) error stop "negative J iota bound"
  allocate(values(n))
  do value_index = 1, n
    values(value_index) = value_index - 1
  end do
end function j_iota

pure function j_copy_int_vector(values, counts) result(copied)
  integer, intent(in) :: values(:), counts(:)
  integer, allocatable :: copied(:)
  integer :: source_index, target_index, repetition

  if (size(values) /= size(counts)) error stop &
    "J copy shape mismatch"
  if (any(counts < 0)) error stop "negative J copy count"
  allocate(copied(sum(counts)))
  target_index = 0
  do source_index = 1, size(values)
    do repetition = 1, counts(source_index)
      target_index = target_index + 1
      copied(target_index) = values(source_index)
    end do
  end do
end function j_copy_int_vector

pure subroutine j_append_int_row(matrix, row)
  integer, allocatable, intent(inout) :: matrix(:,:)
  integer, intent(in) :: row(:)
  integer, allocatable :: grown(:,:)
  integer :: old_rows

  if (size(matrix, 2) /= size(row)) error stop &
    "J row append shape mismatch"
  old_rows = size(matrix, 1)
  allocate(grown(old_rows + 1, size(matrix, 2)))
  if (old_rows > 0) grown(1:old_rows, :) = matrix
  grown(old_rows + 1, :) = row
  call move_alloc(grown, matrix)
end subroutine j_append_int_row

pure function j_cartesian_square(n) result(values)
  integer, intent(in) :: n
  integer, allocatable :: values(:,:)
  integer :: a, b, row

  if (n < 0) error stop "negative J iota bound"
  allocate(values(n * n, 2))
  row = 0
  do a = 1, n
    do b = 1, n
      row = row + 1
      values(row, :) = [a, b]
    end do
  end do
end function j_cartesian_square

pure function j_compress_hcat(matrix, column, row_selector) result(values)
  integer, intent(in) :: matrix(:,:), column(:)
  logical, intent(in) :: row_selector(:)
  integer, allocatable :: values(:,:)
  integer :: source_row, target_row

  if (size(matrix, 1) /= size(column) .or. &
      size(column) /= size(row_selector)) error stop &
    "J compress shape mismatch"
  allocate(values(count(row_selector), size(matrix, 2) + 1))
  target_row = 0
  do source_row = 1, size(row_selector)
    if (row_selector(source_row)) then
      target_row = target_row + 1
      values(target_row, :) = [matrix(source_row, :), column(source_row)]
    end if
  end do
end function j_compress_hcat

end module j2f_runtime
