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

## OpenCourant

[OpenCourant](https://github.com/OpenCourant/OpenCourant) is the community fork of
OpenRadioss: an open-source **explicit dynamics** solver for impacts, drops and other
short, nonlinear events. adapy writes its starter and engine decks, runs both stages and
reads the animation results back:

```python
from ada.fem.steps import StepExplicit

a.fem.add_step(StepExplicit("impact", total_time=0.06))  # with output_interval / target_dt as needed
res = a.to_fem("impact", "opencourant", execute=True)
res.show()  # a time history: the step slider becomes a timeline
```

- **Decks:** `<name>_0000.rad` (starter) and `<name>_0001.rad` (engine). Supported so far:
  3- and 4-node shells, elastic and Johnson-Cook metals, fixed boundary conditions, initial
  velocities, and symmetric shell-to-shell contact. Anything else raises
  `NotImplementedError` rather than being dropped silently. Beams and plates can be meshed
  as shells only with `ada.fem.meshing.mesh_shell_bodies()`.
- **Results:** the run's animation states are packed into one `.radanim` file, which reads as
  an `FEAResult` and uploads to [Ada Studio](../ada_studio.md) like any other result. There
  it plays as a time history and can be exported as MP4 or GIF.
- **Solver:** adapy runs the executables of the `opencourant` conda package (linux-64), which
  must be on `PATH`. The package is not on conda-forge yet.

`examples/opencourant_cell_impact.py` is a complete run: a shell box dropped onto one
steel cell, with contact.
