from __future__ import annotations

import logging
import os
import pathlib
import pickle
import shutil
import subprocess

_script_dir = pathlib.Path(__file__).parent.resolve().absolute()


ABA_IO = _script_dir / "aba_io.py"


def get_odb_data(odb_path, overwrite=False, use_aba_version=None):
    odb_path = pathlib.Path(odb_path)
    pickle_path = odb_path.with_suffix(".pckle")

    if pickle_path.exists() is False or overwrite is True:
        aba_ver = "abaqus" if use_aba_version is None else use_aba_version
        aba_exe_path = pathlib.Path(shutil.which(aba_ver))

        odb_path = pathlib.Path(odb_path)

        if os.path.isfile(pickle_path):
            os.remove(pickle_path)

        print(f'Extracting ODB data from "{odb_path.name}" using Abaqus/Python')

        backup_odb = odb_path.parent / f"{odb_path.stem}_backup.odb"
        if backup_odb.exists() is False:
            print(f'Copying a backup of the odb file to "{backup_odb}" in case python corrupts the odb file')
            shutil.copy(odb_path, backup_odb)

        res = subprocess.run([aba_exe_path, "python", ABA_IO, odb_path], cwd=ABA_IO.parent, capture_output=True)
        logging.info(str(res.stdout, encoding="utf-8"))
        stderr = str(res.stderr, encoding="utf-8")
        if stderr != "":
            logging.error(stderr)

    with open(pickle_path, "rb") as f:
        data = pickle.load(f)

    return data


def read_odb_pckle_file(pickle_path: str | pathlib.Path):
    """Todo: Find a different long-term storage container for abaqus files given that pickle files are not suited"""
    with open(pickle_path, "rb") as f:
        data = pickle.load(f)
    _ = data
    print("sd")
