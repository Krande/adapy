# Build mesh recipes, not meshes

A key motivation behind the creation of adapy was to be able to find a way to be able to control the entire
design and analyze pipeline related to structural engineering.

```text
model -> mesh -> simulate -> postprocess (repeat)
```

A recipe is code: it remeshes the next revision of the model the same way. adapy meshes with
[gmsh](https://gmsh.info).

## Mesh overrides

`obj.mesh_override` holds meshing rules for one object: a function `(gs, obj, geom_repr) -> None`.

- **When:** after all objects are added and partitioned against each other, before meshing.
  `gs.options.Mesh_MeshSizeMax` is the size the mesh is made at.
- **What:** rules on the object's entities, `gs.model_map[obj].entities`. Register sizes with
  `gs.add_size_field`. To set transfinite or recombine yourself, call `gs.skip_default_meshing(obj)`.
- **Not:** it never meshes. The session meshes all objects at once, so an overridden object conforms
  to its neighbours.

Two overrides, used in the examples below:

```python
--8<-- "examples/mesh_overrides.py:overrides"
```

`ada.fem.meshing.overrides` also has configurable `refine_near_booleans(...)` and `element_size(...)`.

Each example below shows the default mesh (left) and the mesh with its overrides (right).

## Plate with cutouts

```python
--8<-- "examples/mesh_overrides.py:plate"
```

<div class="ada-before-after" style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
<iframe class="ada-viewer" src="_static/viewer-figures/mesh_plate_before.html" loading="lazy" style="width:100%;height:340px;border:none;" title="Before: default plate mesh"></iframe>
<iframe class="ada-viewer" src="_static/viewer-figures/mesh_plate_after.html" loading="lazy" style="width:100%;height:340px;border:none;" title="After: refined around the holes"></iframe>
</div>

## Beam with a web hole

```python
--8<-- "examples/mesh_overrides.py:beam"
```

<div class="ada-before-after" style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
<iframe class="ada-viewer" src="_static/viewer-figures/mesh_beam_before.html" loading="lazy" style="width:100%;height:340px;border:none;" title="Before: default beam shell mesh"></iframe>
<iframe class="ada-viewer" src="_static/viewer-figures/mesh_beam_after.html" loading="lazy" style="width:100%;height:340px;border:none;" title="After: refined around the web hole"></iframe>
</div>

## Girder through a deck

The deck is split along the girder's web, so the two share every node on that line.

```python
--8<-- "examples/mesh_overrides.py:part"
```

<div class="ada-before-after" style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
<iframe class="ada-viewer" src="_static/viewer-figures/mesh_part_before.html" loading="lazy" style="width:100%;height:340px;border:none;" title="Before: default deck and girder mesh"></iframe>
<iframe class="ada-viewer" src="_static/viewer-figures/mesh_part_after.html" loading="lazy" style="width:100%;height:340px;border:none;" title="After: refined hole, finer girder, conforming deck"></iframe>
</div>
