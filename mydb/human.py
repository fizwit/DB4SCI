import datetime
import dateutil
from dateutil import parser
import pytz
import os


def human_size(size_bytes):
    """
    Convert bytes to human-readable size format.

    Args:
        size_bytes (int): Size in bytes

    Returns:
        str: Human-readable size (e.g., "1.5 GB", "234 MB")
    """
    if size_bytes == 0:
        return "0 B"

    units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
    unit_index = 0
    size = float(size_bytes)

    while size >= 1024.0 and unit_index < len(units) - 1:
        size /= 1024.0
        unit_index += 1

    # Format with appropriate precision
    if unit_index == 0:  # Bytes
        return f"{int(size)} {units[unit_index]}"
    else:
        return f"{size:.2f} {units[unit_index]}"


def human_uptime(started):
    global day_str
    a = dateutil.parser.parse(started)
    TZ = os.getenv('TZ', 'America/Los_Angeles')
    b = datetime.datetime.now(pytz.timezone(TZ))
    delta = b - a
    if delta.days > 365:
        years = delta.days / 365
        msg = '%d years' % years
    elif delta.days > 0:
        weeks = int(delta.days / 7)
        days = delta.days % 7
        if days == 1:
            day_str = 'day'
        elif days > 1:
            day_str = 'days'
        if weeks == 1:
            msg = "1 week"
        elif weeks > 1:
            msg = "%d weeks" % weeks
        else:
            msg = ''
        if days > 0:
            if weeks > 0:
                msg += ' and '
            msg += "%d %s ago" % (days, day_str)
        else:
            msg += ' ago'
    elif delta.seconds > 1:
        if delta.seconds > 3600:
            hours = int(delta.seconds / 3600)
            minutes = int((delta.seconds - (hours * 3600)) / 60)
            if hours > 2:
                msg = "more than %s hours ago" % hours
            else:
                if minutes > 0:
                    msg = "%s hours %s minutes ago" % (hours, minutes)
                else:
                    msg = "%s hours ago" % (hours, minutes)
        elif delta.seconds > 60:
            minutes = delta.seconds / 60
            seconds = delta.seconds % 60
            if seconds != 0:
                msg = "%d minutes %d seconds ago" % (minutes, seconds)
            else:
                msg = "%d minutes ago" % (minutes)
        else:
            msg = "%d seconds ago" % delta.seconds
    else:
        msg = "about a second ago"
    return msg

if __name__ == '__main__':
    test_data = [
        '2017-12-08T20:54:55.245178807Z',
        '2025-05-17T08:10:24.956869723Z',
        '2025-09-17T18:10:24.956869723Z',
        '2025-10-15T11:10:24.956869723Z',
        '2025-11-13T18:37:24.9568Z',
    ]
    for started in test_data:
        human = human_uptime(started)
        print(f'{human} {started}')

