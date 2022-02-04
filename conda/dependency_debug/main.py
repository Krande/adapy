import json
import os
import pathlib
import re
import shutil
import subprocess


def get_packages():
    def read_it(x):
        line = x.strip()[1:].strip().replace("[all]", "")
        if " " in line:
            li1 = line.split(" ")
            return li1[0]
        return line

    reg_str = re.compile("run:(.*?)\n\n", re.DOTALL)
    with open("../meta.yaml", "r") as f:
        lines = reg_str.search(f.read()).group(1).splitlines()
        res = [read_it(line) for line in lines if read_it(line) not in ["", "python"]]
    return res


def run_debug(packages):
    tempdir = pathlib.Path("output")
    if tempdir.exists():
        shutil.rmtree(tempdir)
    os.makedirs(tempdir, exist_ok=True)
    dep_map = dict()
    for package in packages:
        cmd = f"conda search -c krande -c conda-forge {package} --info --json"
        res = subprocess.run(cmd, capture_output=True, shell=True, universal_newlines=True)
        out_str = res.stdout.replace("[0m", "").strip()
        data = json.loads(out_str)
        latest = data[package][-1]
        dep_map[package] = latest["depends"]
        with open(tempdir / f"{package}.json", "w") as f:
            json.dump(latest, f, indent=4)

    with open(tempdir / "dep_map.json", "w") as f:
        json.dump(dep_map, f, indent=4)


if __name__ == "__main__":
    run_debug(get_packages())
