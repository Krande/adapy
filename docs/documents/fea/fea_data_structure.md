# FEA Data Structure

Here are some thoughts regarding a generalized data structure for FEA.

```{note}
I am writing this document primarily to organize my thoughts.

Consequently, this document will be randomly updated and for the most part appear chaotic and inconsistent.
```

There are <span style="text-decoration: underline">a lot</span> of different FEA formats floating around. The common 
practice is that each FEA solver uses its own custom format for input and results. 

But there are certain things that are present in all of them. 

* Points 
  * 1 integer: `ID`
  * 3 floats: coordinates `X`, `Y`, `Z`
* Elements
  * 1 integer: `ID`
  * 1 string: `element type`
  * N number of integers: point ID's making up the element

Some resources on the topic:

* <https://gitlab.kitware.com/xdmf/xdmf/-/issues/29>
* <https://kitware.github.io/vtk-examples/site/VTKFileFormats/>