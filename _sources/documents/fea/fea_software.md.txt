# FEA Software

Here is a collection of useful links and information regarding the currently supported FEA solvers 


## Code Aster

Code Aster is distributed as a conda-forge package and can be used together with `adapy`.
However, it is currently only distributed for Linux on conda-forge.

:::{note}
As of today (26.11.2024), you can do `mamba install code-aster -c https://repo.prefix.dev/code-aster -c conda-forge` to install
Code Aster for conda on Windows.

::::{important}
However, note that the prefix.dev repository for Code Aster is only temporary until 
the necessary changes are merged into the conda-forge dependencies feedstock.
::::
:::

Work is ongoing to add native support for Windows on conda-forge. 
See [https://github.com/conda-forge/code-aster-feedstock/issues/65](https://github.com/conda-forge/code-aster-feedstock/issues/65) for more information.

More information about Code Aster can be found on

* The Code Aster homepage -> [https://www.code-aster.org](https://www.code-aster.org/spip.php?rubrique2)
* The Code Aster source code -> [Code Aster Original Source Code](https://gitlab.com/codeaster/src)
* Unofficial MSVC Windows support branch -> [Windows support branch in krande gitlab fork](https://gitlab.com/krande/src/-/tree/win-support?ref_type=heads)
* Conda-forge feedstock -> [https://github.com/conda-forge/code-aster-feedstock](https://github.com/conda-forge/code-aster-feedstock)

## Calculix

Calculix is distributed on conda-forge package and is now a dependency of `adapy`.
It is supported on all platforms.

More information [http://www.dhondt.de/](http://www.dhondt.de/)

* [Source Code](https://github.com/Dhondtguido/CalculiXSource)
* [Calculix feedstock](https://github.com/conda-forge/calculix-feedstock)
