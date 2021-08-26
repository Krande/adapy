def str_fix(s):
    return [x.decode("utf-8") for x in s]


def to_safe_name(name):
    return name.replace("/", "__")


def from_safe_name(name):
    return name.replace("__", "/")
