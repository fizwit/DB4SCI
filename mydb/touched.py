from datetime import datetime, date

def create_date_string() -> str:
    """
    Create a date string in ISO format (YYYY-MM-DD) for today's date.
    """
    return date.today().isoformat()


def days_since_touched(touched_date_string) -> int:
    """
    Calculate the number of days since the given date string.

    Args:
        touched_date_string (str): Date string in ISO format (YYYY-MM-DD)

    Returns:
        int: Number of days since the touched date (positive = days ago,
             negative = days in the future)
    """
    touched_date = datetime.fromisoformat(touched_date_string).date()
    today = date.today()
    delta = today - touched_date
    return delta.days


# Example usage
if __name__ == "__main__":
    touched = create_date_string()
    print(f"Date string: {touched}")
    # Create a dict with a touched date
    my_dict = {
        'touched': create_date_string()
    }

    print(f"Created date: {my_dict['touched']}")
    print(f"Days since touched: {days_since_touched(my_dict['touched'])}")

    # Test with an older date
    my_dict['touched'] = '2024-11-01'
    print(f"\nTesting with date: {my_dict['touched']}")
    print(f"Days since touched: {days_since_touched(my_dict['touched'])}")

