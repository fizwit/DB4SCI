#!/usr/bin/env python


def format_fill(justify, row, widths):
    """format a "row" of text with fixed column widths
       If one column is two wide correct the width for adjacent columns
       Asume all column data are strings
       Args:
         justify (string): 'left' or 'right'
         row (list) :string data to format
         widths (list): width for each column
       Returns:
         concatinated string from <row> based on <widths>
    """
    out = ''
    sum = 0
    actual = 0
    for i, item in enumerate(row):
       width = widths[i] if sum >= actual else widths[i] - (actual - sum)
       width = max(0, width)  # Ensure width is never negative
       if justify == 'right':
           out += f"{item:>{width}} "
       elif justify == 'left':
           out += f"{item:<{width}} "
       actual = len(out)
       sum += (widths[i] + 1)
    out += '\n'
    return out

if __name__ == '__main__':
    table_data = [['a', 'b', 'c'],
              ['aaaaaaaaaa', 'b', 'c'],
              ['a', 'bbbbbbbbbb', 'c'],
              ['a', 'bbbbbbbb', 'c'],
              ['aaaaaaaaaa', 'bbbbbbbbbb', 'c'],
              ['a', 'b', 'ccccccccccc']]
    widths = (8,8,8)
    for row in table_data:
        output = format_fill('left',row, widths)
        print(output)
    print('\nRight')
    for row in table_data:
        output = format_fill('right',row, widths)
        print(output)
