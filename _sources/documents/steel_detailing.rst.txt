Steel detailing as a method
=============================

You can create methods on any Part objects that you can customize to your liking. This enables you to input
simplified concept analysis models and output fully detailed models simply by using object oriented design
together with parametric modelling.

In the example below a method is created on a `Reinforced Floor` (basically just a plate with HP profiles underneath)
class inherited from the `Part` class. The method checks for all penetrating piping segments and for each penetration
do the necessary detailing. For the sake of simplicity in this example the detailing was just a cylindrical cutout
through the deck plate and stringer.

With little effort you can add reinforcements to the detail and end up with a
flexible, robust and fabrication friendly parametric detail that you can re-use.


.. figure:: /_static/figures/flow_auto_penetrations.png
    :alt: Parametric penetration details
    :align: center

    Create parametric penetration details using code