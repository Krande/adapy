Documentation
=============================

ADA
-----------------------------------
The main library.

.. automodule:: ada
   :members:


IFC utilities
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

`IFC4X1 <https://standards.buildingsmart.org/IFC/RELEASE/IFC4_1/FINAL/HTML/link/annex-e.htm>`_

.. automodule:: ada.core.ifc_utils
   :members:


.. automodule:: ada.core.ifc_template
   :members:


Blender wrapper and utilities
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

`Blender <https://www.blender.org/>`_

.. automodule:: ada.core.blender
   :members:


Bimserver utilities
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

`BimServer <https://github.com/opensourceBIM/BIMserver>`_

.. automodule:: ada.core.bimserver
   :members:


Other
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. automodule:: ada.core.constants
   :members:


.. automodule:: ada.core.containers
   :members:


.. automodule:: ada.core.utils
   :members:



FEM (Finite Element Method)
-----------------------------
A shared class structure for describing finite element models and analysis

Main
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. automodule:: ada.fem
   :members:


FEM Containers
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. automodule:: ada.fem.containers
   :members:


Utils
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. automodule:: ada.fem.utils
   :members:


IO
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
All code related to interoperability for Finite Element Method (FEM)

Code Aster
~~~~~~~~~~~~~~~~
`Code Aster <https://www.code-aster.org>`_ is an Open Source FEM solver

.. automodule:: ada.fem.io.code_aster
   :members:

GMSH
~~~~~~~~~~~~~~~~
`GMSH <https://gmsh.info/>`_ is a three-dimensional finite element mesh generator with built-in pre- and
post-processing facilities

.. automodule:: ada.fem.io.mesh
   :members:

Abaqus
~~~~~~~~~~~~~~~~
`Abaqus <https://www.3ds.com/products-services/simulia/products/abaqus/>`_ is a Proprietary Finite Element suite
(pre/post processor and solver)

.. automodule:: ada.fem.io.abaqus
   :members:


Calculix
~~~~~~~~~~~~~~~~
`Calculix <http://www.calculix.de/>`_ is an Open Source (GNU General Public License V2 or later) FEM solver

.. automodule:: ada.fem.io.calculix
   :members:

Sesam
~~~~~~~~~~~~~~~~
`Sesam By DnvGL <https://www.dnvgl.com/services/offshore-and-marine-structural-engineering-sesam-for-fixed-structures-1096>`_
is a proprietary finite element suite (pre/post processor and solver)


.. automodule:: ada.fem.io.sesam
   :members:


Usfos
~~~~~~~~~~~~~~~~
`USFOS <https://usfos.no/>`_ is a proprietary finite element solver



.. automodule:: ada.fem.io.usfos
   :members:


Utilities
~~~~~~~~~~~~~~~~
A collection of python utilities and different Python Wrappers for Various FEM software (Abaqus, Femap, GMSH).

.. automodule:: ada.fem.io.utils
   :members:


Materials
-----------------------------
A collection of different utilities for material properties. Future use-case would be that this library contains
a library of structural material properties that have been properly defined and accepted by all locations.


Metals
^^^^^^^^^^^^^^^^

.. automodule:: ada.materials.metals
    :members:


Polymers
^^^^^^^^^^^^^^^^

.. automodule:: ada.materials.polymers
    :members:

.. automodule:: ada.materials.polymers.models
    :members:

.. automodule:: ada.materials.polymers.utils
    :members:

Sections
-----------------------------------

.. automodule:: ada.sections
   :members:

.. automodule:: ada.sections.utils
   :members:

Base Classes
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. automodule:: ada.base
   :members:

.. automodule:: ada.base.renderer
   :members:
