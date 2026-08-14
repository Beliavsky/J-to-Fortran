module j2f_runtime
  use, intrinsic :: iso_fortran_env, only: dp => real64
  implicit none
  private
  public :: j_addition_table_int, j_append_int_row, j_binomial
  public :: j_cartesian_square, j_compress_hcat
  public :: j_copy_int_vector, j_factorial, j_grade_up_int, j_index_of_int
  public :: j_decode_int, j_determinant_real, j_diagonal_int, j_diagonal_real
  public :: j_encode_int
  public :: j_infix_max_int, j_infix_subtract_int, j_infix_sum_int
  public :: j_inverse_real, j_iota, j_match_real
  public :: j_membership_int, j_multiplication_table_int, j_nub_int
  public :: j_mread
  public :: j_power_table_int, j_prefix_max_int, j_prefix_product_int
  public :: j_prefix_sum_int
  public :: j_polynomial_int, j_polynomial_real
  public :: j_reverse_int_vector, j_signum_int, j_sort_int_vector
  public :: j_reverse_character
  public :: j_raze_character
  public :: j_read_numeric_csv
  public :: j_select_character
  public :: j_solve_2x2_matrix_int, j_solve_2x2_vector_int
  public :: j_solve_real_vector
  public :: j_true_indices
  public :: j_write_text

contains

function j_mread(filename) result(values)
  character(len=*), intent(in) :: filename
  real(kind=dp), allocatable :: values(:,:)
  integer :: unit, io_status, row_count, column_count, row, line_columns, position
  character(len=4096) :: line
  logical :: in_field, separator
  open(newunit=unit, file=filename, status="old", action="read", iostat=io_status)
  if (io_status /= 0) error stop "cannot open numeric table"
  row_count = 0
  column_count = 0
  do
    read(unit,"(a)",iostat=io_status) line
    if (io_status < 0) exit
    if (io_status > 0) error stop "cannot read numeric table"
    if (len_trim(line) == 0) cycle
    line_columns = 0
    in_field = .false.
    do position = 1, len_trim(line)
      separator = line(position:position) == ' ' .or. &
        iachar(line(position:position)) == 9 .or. line(position:position) == ','
      if (.not. separator .and. .not. in_field) line_columns = line_columns + 1
      in_field = .not. separator
    end do
    if (column_count == 0) column_count = line_columns
    if (line_columns /= column_count) error stop "inconsistent numeric table width"
    row_count = row_count + 1
  end do
  rewind(unit)
  allocate(values(row_count, column_count))
  row = 0
  do
    read(unit,"(a)",iostat=io_status) line
    if (io_status < 0) exit
    if (io_status > 0) error stop "cannot read numeric table"
    if (len_trim(line) == 0) cycle
    row = row + 1
    read(line,*,iostat=io_status) values(row, :)
    if (io_status /= 0) error stop "invalid numeric table row"
  end do
  close(unit)
end function j_mread

pure function j_true_indices(mask) result(indices)
  logical, intent(in) :: mask(:)
  integer, allocatable :: indices(:)
  integer :: source_index, target_index
  allocate(indices(count(mask)))
  target_index = 0
  do source_index = 1, size(mask)
    if (mask(source_index)) then
      target_index = target_index + 1
      indices(target_index) = source_index - 1
    end if
  end do
end function j_true_indices

function j_write_text(text, filename, append) result(count)
  character(len=*), intent(in) :: text, filename
  logical, intent(in) :: append
  integer :: count
  integer :: io_status, output_unit
  if (append) then
    open(newunit=output_unit, file=filename, status="unknown", &
      position="append", access="stream", form="unformatted", &
      action="write", iostat=io_status)
  else
    open(newunit=output_unit, file=filename, status="replace", &
      access="stream", form="unformatted", action="write", &
      iostat=io_status)
  end if
  if (io_status /= 0) error stop "cannot open J output file"
  write(output_unit, iostat=io_status) text
  if (io_status /= 0) error stop "cannot write J output file"
  close(output_unit, iostat=io_status)
  if (io_status /= 0) error stop "cannot close J output file"
  count = len(text)
end function j_write_text

subroutine j_read_numeric_csv(filename, symbols, values)
  character(len=*), intent(in) :: filename
  character(len=:), allocatable, intent(out) :: symbols(:)
  real(kind=dp), allocatable, intent(out) :: values(:,:)
  character(len=8192) :: line, numeric_line
  character(len=32) :: date_field
  integer :: column, column_count, comma, input_unit, io_status
  integer :: line_length, row, row_count, start

  open(newunit=input_unit, file=filename, status="old", &
       action="read", iostat=io_status)
  if (io_status /= 0) error stop "cannot open numeric CSV file"
  read(input_unit, "(a)", iostat=io_status) line
  if (io_status /= 0) error stop "numeric CSV file has no header"
  line_length = len_trim(line)
  column_count = 0
  do column = 1, line_length
    if (line(column:column) == ",") column_count = column_count + 1
  end do
  if (column_count < 1) error stop "numeric CSV needs data columns"
  allocate(character(len=32) :: symbols(column_count))
  start = index(line(:line_length), ",") + 1
  do column = 1, column_count
    comma = index(line(start:line_length), ",")
    if (comma == 0) then
      symbols(column) = adjustl(line(start:line_length))
    else
      symbols(column) = adjustl(line(start:start + comma - 2))
      start = start + comma
    end if
  end do
  row_count = 0
  do
    read(input_unit, "(a)", iostat=io_status) line
    if (io_status < 0) exit
    if (io_status > 0) error stop "error reading numeric CSV file"
    if (len_trim(line) > 0) row_count = row_count + 1
  end do
  if (row_count < 2) error stop "numeric CSV needs two data rows"
  rewind(input_unit)
  read(input_unit, "(a)") line
  allocate(values(row_count, column_count))
  row = 0
  do
    read(input_unit, "(a)", iostat=io_status) line
    if (io_status < 0) exit
    if (io_status > 0) error stop "error reading numeric CSV file"
    if (len_trim(line) == 0) cycle
    row = row + 1
    numeric_line = line
    do column = 1, len_trim(numeric_line)
      if (numeric_line(column:column) == ",") &
        numeric_line(column:column) = " "
    end do
    read(numeric_line, *, iostat=io_status) date_field, values(row, :)
    if (io_status /= 0) error stop "invalid numeric CSV data row"
  end do
  close(input_unit)
end subroutine j_read_numeric_csv

pure function j_diagonal_int(matrix) result(values)
  integer, intent(in) :: matrix(:,:)
  integer, allocatable :: values(:)
  integer :: diagonal_index, diagonal_size

  diagonal_size = min(size(matrix, 1), size(matrix, 2))
  allocate(values(diagonal_size))
  do diagonal_index = 1, diagonal_size
    values(diagonal_index) = matrix(diagonal_index, diagonal_index)
  end do
end function j_diagonal_int

pure function j_diagonal_real(matrix) result(values)
  real(kind=dp), intent(in) :: matrix(:,:)
  real(kind=dp), allocatable :: values(:)
  integer :: diagonal_index, diagonal_size

  diagonal_size = min(size(matrix, 1), size(matrix, 2))
  allocate(values(diagonal_size))
  do diagonal_index = 1, diagonal_size
    values(diagonal_index) = matrix(diagonal_index, diagonal_index)
  end do
end function j_diagonal_real

pure function j_decode_int(base, digits) result(value)
  integer, intent(in) :: base, digits(:)
  integer :: value
  integer :: digit_index

  if (base <= 1) error stop "base decode requires base greater than one"
  if (any(digits < 0 .or. digits >= base)) error stop "invalid base digit"
  value = 0
  do digit_index = 1, size(digits)
    value = value * base + digits(digit_index)
  end do
end function j_decode_int

pure function j_encode_int(bases, value) result(digits)
  integer, intent(in) :: bases(:), value
  integer, allocatable :: digits(:)
  integer :: base_index, remaining

  if (any(bases <= 1)) error stop "base encode requires bases greater than one"
  if (value < 0) error stop "base encode requires a nonnegative value"
  allocate(digits(size(bases)))
  remaining = value
  do base_index = size(bases), 1, -1
    digits(base_index) = modulo(remaining, bases(base_index))
    remaining = remaining / bases(base_index)
  end do
end function j_encode_int

pure function j_polynomial_int(coefficients, argument) result(value)
  integer, intent(in) :: coefficients(:), argument
  integer :: value
  integer :: coefficient_index

  value = 0
  do coefficient_index = size(coefficients), 1, -1
    value = coefficients(coefficient_index) + argument * value
  end do
end function j_polynomial_int

pure function j_polynomial_real(coefficients, argument) result(value)
  real(kind=dp), intent(in) :: coefficients(:), argument
  real(kind=dp) :: value
  integer :: coefficient_index
  value = 0.0_dp
  do coefficient_index = size(coefficients), 1, -1
    value = coefficients(coefficient_index) + argument * value
  end do
end function j_polynomial_real

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

pure function j_prefix_max_int(values) result(prefixes)
  integer, intent(in) :: values(:)
  integer, allocatable :: prefixes(:)
  integer :: value_index

  allocate(prefixes(size(values)))
  if (size(values) > 0) prefixes(1) = values(1)
  do value_index = 2, size(values)
    prefixes(value_index) = max(prefixes(value_index - 1), values(value_index))
  end do
end function j_prefix_max_int

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

pure function j_infix_max_int(values, width) result(maxima)
  integer, intent(in) :: values(:), width
  integer, allocatable :: maxima(:)
  integer :: window_start

  if (width <= 0 .or. width > size(values)) error stop "invalid infix width"
  allocate(maxima(size(values) - width + 1))
  do window_start = 1, size(maxima)
    maxima(window_start) = maxval(values(window_start:window_start + width - 1))
  end do
end function j_infix_max_int

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

pure function j_raze_character(values) result(razed)
  character(len=*), intent(in) :: values(:)
  character(len=:), allocatable :: razed
  integer :: item_index, target_start, value_length

  value_length = sum(len_trim(values))
  allocate(character(len=value_length) :: razed)
  target_start = 1
  do item_index = 1, size(values)
    value_length = len_trim(values(item_index))
    razed(target_start:target_start + value_length - 1) = &
      values(item_index)(:value_length)
    target_start = target_start + value_length
  end do
end function j_raze_character

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
  real(kind=dp) :: solution(2)
  real(kind=dp) :: determinant

  determinant = real(coefficients(1, 1), kind=dp) * &
    coefficients(2, 2) - real(coefficients(1, 2), kind=dp) * &
    coefficients(2, 1)
  if (determinant == 0.0_dp) error stop "singular 2 by 2 matrix"
  solution(1) = (real(coefficients(2, 2), kind=dp) * rhs(1) - &
    real(coefficients(1, 2), kind=dp) * rhs(2)) / determinant
  solution(2) = (real(coefficients(1, 1), kind=dp) * rhs(2) - &
    real(coefficients(2, 1), kind=dp) * rhs(1)) / determinant
end function j_solve_2x2_vector_int

pure function j_solve_2x2_matrix_int(rhs, coefficients) result(solution)
  integer, intent(in) :: rhs(:,:), coefficients(2,2)
  real(kind=dp), allocatable :: solution(:,:)
  real(kind=dp) :: determinant

  if (size(rhs, 1) /= 2) error stop "2 by 2 solve shape mismatch"
  determinant = real(coefficients(1, 1), kind=dp) * &
    coefficients(2, 2) - real(coefficients(1, 2), kind=dp) * &
    coefficients(2, 1)
  if (determinant == 0.0_dp) error stop "singular 2 by 2 matrix"
  allocate(solution(2, size(rhs, 2)))
  solution(1, :) = (real(coefficients(2, 2), kind=dp) * rhs(1, :) - &
    real(coefficients(1, 2), kind=dp) * rhs(2, :)) / determinant
  solution(2, :) = (real(coefficients(1, 1), kind=dp) * rhs(2, :) - &
    real(coefficients(2, 1), kind=dp) * rhs(1, :)) / determinant
end function j_solve_2x2_matrix_int

pure function j_solve_real_vector(rhs, coefficients) result(solution)
  real(kind=dp), intent(in) :: rhs(:), coefficients(:,:)
  real(kind=dp), allocatable :: solution(:)
  real(kind=dp), allocatable :: work(:,:), work_rhs(:), row_buffer(:)
  real(kind=dp) :: factor, scalar_buffer
  integer :: column, row, pivot_row, system_size

  system_size = size(rhs)
  if (size(coefficients, 1) /= system_size .or. &
      size(coefficients, 2) /= system_size) &
    error stop "linear solve shape mismatch"
  work = coefficients
  work_rhs = rhs
  allocate(solution(system_size), row_buffer(system_size))
  do column = 1, system_size
    pivot_row = column - 1 + &
      maxloc(abs(work(column:system_size, column)), dim=1)
    if (abs(work(pivot_row, column)) <= tiny(1.0_dp)) &
      error stop "singular matrix"
    if (pivot_row /= column) then
      row_buffer = work(column, :)
      work(column, :) = work(pivot_row, :)
      work(pivot_row, :) = row_buffer
      scalar_buffer = work_rhs(column)
      work_rhs(column) = work_rhs(pivot_row)
      work_rhs(pivot_row) = scalar_buffer
    end if
    do row = column + 1, system_size
      factor = work(row, column) / work(column, column)
      work(row, column:system_size) = work(row, column:system_size) - &
        factor * work(column, column:system_size)
      work_rhs(row) = work_rhs(row) - factor * work_rhs(column)
    end do
  end do
  do row = system_size, 1, -1
    solution(row) = work_rhs(row)
    if (row < system_size) solution(row) = solution(row) - &
      dot_product(work(row, row + 1:system_size), &
                  solution(row + 1:system_size))
    solution(row) = solution(row) / work(row, row)
  end do
end function j_solve_real_vector

pure function j_inverse_real(matrix) result(inverse)
  real(kind=dp), intent(in) :: matrix(:,:)
  real(kind=dp), allocatable :: inverse(:,:)
  real(kind=dp), allocatable :: work(:,:), row_buffer(:)
  real(kind=dp) :: factor, pivot
  integer :: column, matrix_size, pivot_row, row

  matrix_size = size(matrix, 1)
  if (size(matrix, 2) /= matrix_size) &
    error stop "matrix inverse requires a square matrix"
  work = matrix
  allocate(inverse(matrix_size, matrix_size), row_buffer(matrix_size))
  inverse = 0.0_dp
  do row = 1, matrix_size
    inverse(row, row) = 1.0_dp
  end do
  do column = 1, matrix_size
    pivot_row = column - 1 + &
      maxloc(abs(work(column:matrix_size, column)), dim=1)
    pivot = work(pivot_row, column)
    if (abs(pivot) <= tiny(1.0_dp)) error stop "singular matrix"
    if (pivot_row /= column) then
      row_buffer = work(column, :)
      work(column, :) = work(pivot_row, :)
      work(pivot_row, :) = row_buffer
      row_buffer = inverse(column, :)
      inverse(column, :) = inverse(pivot_row, :)
      inverse(pivot_row, :) = row_buffer
    end if
    pivot = work(column, column)
    work(column, :) = work(column, :) / pivot
    inverse(column, :) = inverse(column, :) / pivot
    do row = 1, matrix_size
      if (row == column) cycle
      factor = work(row, column)
      work(row, :) = work(row, :) - factor * work(column, :)
      inverse(row, :) = inverse(row, :) - factor * inverse(column, :)
    end do
  end do
end function j_inverse_real

pure function j_determinant_real(matrix) result(determinant)
  real(kind=dp), intent(in) :: matrix(:,:)
  real(kind=dp) :: determinant
  real(kind=dp), allocatable :: work(:,:), row_buffer(:)
  real(kind=dp) :: factor
  integer :: column, matrix_size, pivot_row, row, sign_factor

  matrix_size = size(matrix, 1)
  if (size(matrix, 2) /= matrix_size) &
    error stop "determinant requires a square matrix"
  work = matrix
  allocate(row_buffer(matrix_size))
  sign_factor = 1
  do column = 1, matrix_size
    pivot_row = column - 1 + &
      maxloc(abs(work(column:matrix_size, column)), dim=1)
    if (abs(work(pivot_row, column)) <= tiny(1.0_dp)) then
      determinant = 0.0_dp
      return
    end if
    if (pivot_row /= column) then
      row_buffer = work(column, :)
      work(column, :) = work(pivot_row, :)
      work(pivot_row, :) = row_buffer
      sign_factor = -sign_factor
    end if
    do row = column + 1, matrix_size
      factor = work(row, column) / work(column, column)
      work(row, column:matrix_size) = &
        work(row, column:matrix_size) - &
        factor * work(column, column:matrix_size)
    end do
  end do
  determinant = real(sign_factor, kind=dp)
  do column = 1, matrix_size
    determinant = determinant * work(column, column)
  end do
end function j_determinant_real

pure elemental function j_match_real(left, right) result(matches)
  real(kind=dp), intent(in) :: left, right
  logical :: matches

  matches = abs(left - right) <= &
    2.0_dp**(-44) * max(abs(left), abs(right))
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
