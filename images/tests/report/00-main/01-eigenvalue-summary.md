# Summary

Below is a summary of the performed analysis

## Eigenvalue Analysis

A simple cantilevered beam is subjected to eigenvalue analysis with a total of 
{{__num_modes__}} eigenmodes requested.


### Model Description

Model object: {{__geom_specifics__}}

### Summary

This section presents the resulting comparison between the calculated results from the 
eigenvalue analysis in the different FEA tools.


{{__eig_compare_solid__}}


{{__eig_compare_shell__}}


{{__eig_compare_line__}}



**Solid Elements**

As shown in the tables above, the differences between the various FEA tools are small for 
solid elements both for 1st and 2nd order formulations.

**Shell Elements**

For 1st order shell elements it is observed an increasing difference in values based on 
the order of eigenmode.
Results using 2nd order shell elements is observed to be closer for all modes.

**Line Elements**

1st order line elements in Code Aster and Abaqus differ slightly from mode #4 and higher.
Calculix does not support eigenvalue analysis using generalized U1 beam elements.
