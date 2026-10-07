# FEA Software

Here is a collection of useful links and information regarding the currently supported FEA solvers 


## Code Aster

Code Aster is distributed as a conda-forge package and can be used together with `adapy`.
It is available for Linux and, since version 18.1.7, for Windows (sequential MSVC builds).
Add it to the same [pixi](https://pixi.sh) project as `adapy`, so the analyses adapy starts
find the solver in that environment:

```bash
pixi add ada-py "code-aster>=18.1.7"
```

To have Code Aster on its own, outside any project:

```bash
pixi global install "code-aster>=18.1.7"
```

More information about Code Aster can be found on

* The Code Aster homepage -> [https://www.code-aster.org](https://www.code-aster.org/spip.php?rubrique2)
* The Code Aster source code -> [Code Aster Original Source Code](https://gitlab.com/codeaster/src)
* Conda-forge feedstock -> [https://github.com/conda-forge/code-aster-feedstock](https://github.com/conda-forge/code-aster-feedstock)

## Calculix

Calculix is distributed on conda-forge package and is now a dependency of `adapy`.
It is supported on all platforms.

More information [http://www.dhondt.de/](http://www.dhondt.de/)

* [Source Code](https://github.com/Dhondtguido/CalculiXSource)
* [Calculix feedstock](https://github.com/conda-forge/calculix-feedstock)
