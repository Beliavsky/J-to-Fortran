module j2f_runtime
  implicit none
  private
  public :: j_append_int_row, j_cartesian_square, j_compress_hcat
  public :: j_copy_int_vector, j_iota

contains

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
