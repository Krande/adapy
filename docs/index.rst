Assembly for Design & Analysis (ADA)
==============================================================

A python library for structural analysis and design that focus on interoperability between
IFC and various Finite Element formats.

This library is still undergoing significant development so expect there to be occasional bugs and breaking changes.

Try the latest build online here

.. image:: https://mybinder.org/badge_logo.svg
 :target: https://mybinder.org/v2/gh/Krande/adapy/main


**Build, Analyze and Postprocess in Code**


.. figure:: /_static/figures/flow_parametric.png


**Steel detailing as a method**

You can create methods on any Part objects that you can customize to your liking. This enables you to input
simplified concept analysis models and output fully detailed models simply by using object oriented design
together with parametric modelling.

In the example below a method is created on a `Reinforced Floor` (basically just a plate with HP profiles underneath)
class inherited from the `Part` class to check for all penetrating piping segments and for each penetration
do the necessary detailing (which for the sake of simplicity in this example) resulted in a cylindrical cutout through
the deck plate and stringer.

With little effort you can add reinforcements to the detail and end up with a
flexible, robust and fabrication friendly parametric detail that you can re-use.


.. figure:: /_static/figures/flow_auto_penetrations.png
    :alt: Parametric penetration details
    :align: center

    Create parametric penetration details using code


**Path to Software Agnosticism**

The ambition is to enable anyone to start designing something in a tool of their choice and have that design
effortlessly converted to any FEM simulation formats and/or be importable into any relevant CAD software.

Say you want to use python to design something and run a FEM analysis in Abaqus and export to a CAD package all in one
fluent operation.


.. image:: /_static/figures/flow_fem_ifc_simple.png


The picture above shows the design being written in python using a
`Jupyter Notebook <https://jupyter.org/>`_, exported to IFC and further inspected using
`XbimXplorer <https://docs.xbim.net/downloads/xbimxplorer.html>`_ (bottom left), then simulated
in `Abaqus <https://www.3ds.com/products-services/simulia/products/abaqus/>`_ (bottom right) here represented by the
fem representation being imported into `Abaqus CAE <https://www.3ds.com/products-services/simulia/products/abaqus/abaquscae/>`_
(a pre/post-processing software).




Table of Contents
=======================================
.. toctree::
    :maxdepth: 2
    :glob:

    code



Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`