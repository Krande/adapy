## Eigenvalue Analysis

A simple cantilevered beam is subjected to eigenvalue analysis with a total of 
{{__num_modes__}} eigenmodes requested.


### Model Description

Model object: {{__geom_specifics__}}

### Summary

This section presents a comparison between the calculated results from the 
eigenvalue analysis of a cantilever beam using the different FEA tools. 

Note! The `Sestra` and `Abaqus` analyses are not performed as part of the github actions step. The
eigenmode results are kept in cached json files and imported during the compilation of the report using __paradoc__.


{{__eig_compare_solid__}}


{{__eig_compare_shell__}}


{{__eig_compare_line__}}



**Solid Elements**

As shown in the tables above, the differences between the various FEA tools are small for 
solid elements both for 1st and 2nd order formulations.

**Shell Elements**

For 1st order shell elements it is observed an increasing difference in values based on 
the order of eigenmode. The differe
Results using 2nd order shell elements is observed to be closer for all modes.

**Line Elements**

1st order line elements in Code Aster and Abaqus differ slightly from mode #4 and higher.
Calculix does not support eigenvalue analysis using generalized U1 beam elements.
