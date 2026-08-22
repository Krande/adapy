Path to Software Agnosticism
==========================================================

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