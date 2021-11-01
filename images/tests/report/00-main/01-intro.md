# Open Source FEA using adapy 

This report describes the main verification work of the simulation results produced by the
Open Source Finite Element (FE) solvers [Code Aster](https://www.code-aster.org/spip.php?rubrique2) 
and [Calculix](http://www.dhondt.de/).

The motivation behind producing this report is to increase confidence in the conversion of FEM models in _adapy_ 
and also the results from the various FEA solvers.

## Introduction

The results from _Code Aster_ and _Calculix_ and tools will be compared with proprietary FEA solvers such as;


* [Abaqus](https://www.3ds.com/products-services/simulia/products/abaqus/)
* [Sestra (part of DNV's Sesam suite)](https://www.dnv.com/services/linear-structural-analysis-sestra-2276)


The simulations are pre- and post processed using [adapy](https://github.com/Krande/adapy) and this report is 
automatically generated using [paradoc](https://github.com/Krande/paradoc) as 
part of a ci/cd pipeline within the _adapy_ repositories. 

For each new addition of code affecting the handling of 
FEM in _adapy_ a new series of analysis is performed whereas the results are gathered in this auto-generated document.

Please note! This report will not go into detail into the specifics of why the results are different 
between different element representations. This report will only investigate
and compare the results from different FEA software.

## FEA Solvers

* Calculix v{{__ccx_version__}}
* Code Aster v{{__ca_version__}}
* Abaqus v{{__aba_version__}}
* Sestra v{{__ses_version__}}


## Python packages
The following python packages were instrumental in the creation of this document and the FEA results herein. 

### Adapy

The intention behind _adapy_ is to make it easier to work with finite element models 
and BIM models.

### Paradoc

_paradoc_ was created to simplify the generation of reports by creating the structure of the document and text 
in markdown with a string substitution scheme that lets you easily pass in tables, functions and finally be 
able to produce production ready documents in Microsoft Word. 
