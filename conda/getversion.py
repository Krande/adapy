from importlib.metadata import version

vstr = f"PKG_VERSION={version('ada-py')}"
print(vstr)
with open("version.txt", "w") as f:
    f.write(vstr)
