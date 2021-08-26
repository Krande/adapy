# coding=utf-8
import os
import pathlib
import shutil
import subprocess


class Blender:
    """
    A class wrapping around Blender


    Assuming you have added blender exe dir to system path, this will automatically pick it up. Otherwise, pass it as
    a variable

    :param work_dir:
    :param project_name:
    :param exe_path:
    """

    __exe_path = shutil.which("blender")

    def __init__(self, work_dir, project_name, exe_path=None):

        if self.__exe_path is None and exe_path is None:
            raise Exception(
                'Blender installation is not found. Either add it to system path env or pass the "exe_path"'
            )

        self.work_dir = work_dir
        self._project_name = project_name
        self.exe_path = self.__exe_path if exe_path is None else pathlib.Path(exe_path)

    def run(self, script_path=None, run_silent=True):
        """
        Start Blender in the currently defined project_path


        """

        os.makedirs(self.work_dir, exist_ok=True)

        args = [str(self.exe_path)]
        if run_silent:
            args += ["-b"]

        if script_path is not None:
            script_path = pathlib.Path(script_path).resolve().absolute()

            if script_path.is_file() is False:
                raise FileNotFoundError("Script not found")
            args += ["--python", str(script_path)]

        cmdstring = tuple(args)

        subprocess.run(cmdstring, stdin=subprocess.PIPE, cwd=self.work_dir, shell=True)

    @property
    def scripts_dir(self):
        return self.work_dir / "scripts"

    @property
    def assets_dir(self):
        return self.work_dir / "assets"

    @property
    def work_dir(self):
        return self._work_dir

    @work_dir.setter
    def work_dir(self, value):
        self._work_dir = pathlib.Path(value).resolve().absolute()

    @property
    def exe_path(self):
        return self._exe_path

    @exe_path.setter
    def exe_path(self, value):
        self._exe_path = pathlib.Path(value).resolve().absolute()

    @property
    def project_name(self):
        return self._project_name
