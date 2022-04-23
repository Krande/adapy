import datetime
import os


def get_now():
    d = datetime.datetime.now(datetime.timezone.utc).astimezone()
    date_in_str = "%a, %d %b %Y %H:%M:%S %Z %z"
    now_str = d.strftime(date_in_str)
    return now_str


def interpret_now(date_string):
    return datetime.datetime.strptime(date_string, "%a, %d %b %Y %H:%M:%S %Z %z")


def create_date_str(include_minutes=False):
    """
    Function for building a formatted timestamp string

    :param include_minutes: Extends the timing format to include minutes
    :return: formatted string <year>_<month>_<day> + optionally (_<hour>_<minute>)
    """
    du = datetime.datetime.now()
    dyear = str(du.year)
    dyear2 = "" + dyear[2] + "" + dyear[3]
    dmonth = "{:02d}".format(du.month)
    dday = "{:02d}".format(du.day)
    day_format = "" + str(dyear2) + "" + str(dmonth) + "" + str(dday)
    if include_minutes:
        day_format += "_" + str(du.hour) + ":" + str(du.minute)
    return day_format


def get_file_time_local(file_path):
    from datetime import datetime, timezone

    utc_time = datetime.fromtimestamp(os.path.getmtime(file_path), timezone.utc)
    return utc_time.astimezone()


def get_time_stamp_now():
    import pytz

    utc = pytz.UTC
    return utc.localize(datetime.datetime.utcnow())


def datetime_to_str(obj):
    return obj.isoformat()


def get_last_file_modified(file_dir, file_ext):
    from .file_system import get_list_of_files

    last_date = None
    for f in get_list_of_files(file_dir, file_ext):
        curr_date = get_file_time_local(f)
        if last_date is None:
            last_date = curr_date
        elif curr_date > last_date:
            last_date = curr_date
    return last_date


def datetime_from_str(obj_str):
    import dateutil.parser

    return dateutil.parser.parse(obj_str)
