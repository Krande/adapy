Configuring adapy
==========================================================

Setting paths to your FE tools in python
--------------------------------------------------------------------------

If you cannot or wont tamper with your computer environmental variables, here's a 3rd option for doing so

3. Set parameters in python by using environment variables or the ada.config.Settings class, like so:

.. code-block:: python

    import os

    os.environ["ADA_calculix_exe"] = "<absolute path to ccx.exe>"
    os.environ["ADA_abaqus_exe"] = "<absolute path to abaqus.bat>"
    os.environ["ADA_code_aster_exe"] = "<absolute path to as_run.bat>"


or

.. code-block:: python

    from ada.config import Settings

    Settings.fem_exe_paths["calculix"] = "<absolute path to ccx.exe>"
    Settings.fem_exe_paths["abaqus"] = "<absolute path to abaqus.bat>"
    Settings.fem_exe_paths["code_aster"] = "<absolute path to as_run.bat>"




Alternative Installation methods
-------------------------------------
If you have to use pip you can do:

    pip install ada-py


**Note!** Pip will not install the required conda packages. So you would also have to do


    conda install -c conda-forge ifcopenshell pythonocc-core python-gmsh

