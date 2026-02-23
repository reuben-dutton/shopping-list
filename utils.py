"""
Utility functions for formatting numbers in recipes.
"""
from fractions import Fraction


# Unicode fraction characters for common recipe measurements
UNICODE_FRACTIONS = {
    (1, 4): '¼',
    (1, 2): '½',
    (3, 4): '¾',
    (1, 3): '⅓',
    (2, 3): '⅔',
    # (1, 5): '⅕',
    # (2, 5): '⅖',
    # (3, 5): '⅗',
    # (4, 5): '⅘',
    # (1, 6): '⅙',
    # (5, 6): '⅚',
    # (1, 8): '⅛',
    # (3, 8): '⅜',
    # (5, 8): '⅝',
    # (7, 8): '⅞',
}


def float_to_string(value: float) -> str:
    """
    Convert a float to an appropriate string representation.
    """

    # Handle zero
    if value == 0:
        return "0"
    
    # Check if it's a whole number
    if value == int(value):
        return str(int(value))
    
    # Convert to Fraction with limited denominator (common in recipes)
    frac = Fraction(value).limit_denominator(8)
    
    # Extract whole and fractional parts
    whole = frac.numerator // frac.denominator
    remainder_num = frac.numerator % frac.denominator
    
    # Build result string
    result = ""
    if whole > 0:
        result = str(whole)
    
    # Add fractional part if it exists
    if remainder_num > 0:
        fraction_tuple = (remainder_num, frac.denominator)
        if fraction_tuple in UNICODE_FRACTIONS:
            result += UNICODE_FRACTIONS[fraction_tuple]
        else:
            # # Fallback to slash notation
            # if result:
            #     result += f" {remainder_num}/{frac.denominator}"
            # else:
            #     result += f"{remainder_num}/{frac.denominator}"
            result = str(value)
    
    return result

# def float_to_string(value: float) -> str:
#     if int(value) == value:
#         return str(int(value))
#     return str(value)