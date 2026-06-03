# ACIS to adapy Geometry Implementation Checklist

This document tracks the implementation status of converting ACIS geometry entities to adapy's STEP-based geometry definitions.

## Test File
- **Checked-in fixture**: `files/sat_files/hullskin_face_0.sat`
- **Test Command**: `pixi run -e tests ada convert path/to/model.sat path/to/model.stp`

## Curve Conversions (ACIS → adapy)

### Basic Curves
- [x] **AcisStraightCurve** → `geo_cu.Line`
  - Status: ✅ Implemented in converter.py
  - Maps to: `ada.geom.curves.Line`
  
- [x] **AcisEllipseCurve** → `geo_cu.Circle` or `geo_cu.Ellipse`
  - Status: ✅ Implemented in converter.py
  - Maps to: `ada.geom.curves.Circle` (when radius_ratio = 1.0) or `ada.geom.curves.Ellipse`

### Spline Curves
- [x] **AcisIntcurveCurve** (B-spline) → `geo_cu.BSplineCurveWithKnots` or `geo_cu.RationalBSplineCurveWithKnots`
  - Status: ✅ Implemented in converter.py
  - Maps to: `ada.geom.curves.BSplineCurveWithKnots` or `ada.geom.curves.RationalBSplineCurveWithKnots`

### Missing Curve Types (TODO)
- [ ] **PCurve** (Parametric curve on surface)
  - Target: `geo_cu.PCurve`
  - Status: ⚠️ Class exists but no ACIS conversion implemented
  - Notes: Need to implement converter for AcisPCurve → geo_cu.PCurve

## Surface Conversions (ACIS → adapy)

### Basic Surfaces
- [x] **AcisPlaneSurface** → `geo_su.Plane`
  - Status: ✅ Implemented in converter.py
  - Maps to: `ada.geom.surfaces.Plane`

### Analytic Surfaces (TODO)
- [ ] **AcisCylinderSurface** → `geo_su.CylindricalSurface`
  - Target: New class needed in surfaces.py
  - Status: ❌ Not implemented
  - Notes: Currently returns None. Need to add CylindricalSurface class based on STEP AP242
  - STEP Reference: https://www.steptools.com/stds/stp_aim/html/t_cylindrical_surface.html
  - IFC Reference: https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/HTML/lexical/IfcCylindricalSurface.htm

- [ ] **AcisConeSurface** → `geo_su.ConicalSurface`
  - Target: New class needed in surfaces.py
  - Status: ❌ Not implemented
  - Notes: Currently returns None. Need to add ConicalSurface class based on STEP AP242
  - STEP Reference: https://www.steptools.com/stds/stp_aim/html/t_conical_surface.html
  - IFC Reference: https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/HTML/lexical/IfcSurface.htm

- [ ] **AcisSphereSurface** → `geo_su.SphericalSurface`
  - Target: New class needed in surfaces.py
  - Status: ❌ Not implemented
  - Notes: Currently returns None. Need to add SphericalSurface class based on STEP AP242
  - STEP Reference: https://www.steptools.com/stds/stp_aim/html/t_spherical_surface.html
  - IFC Reference: https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/HTML/lexical/IfcSphericalSurface.htm

- [ ] **AcisTorusSurface** → `geo_su.ToroidalSurface`
  - Target: New class needed in surfaces.py
  - Status: ❌ Not implemented
  - Notes: Currently returns None. Need to add ToroidalSurface class based on STEP AP242
  - STEP Reference: https://www.steptools.com/stds/stp_aim/html/t_toroidal_surface.html
  - IFC Reference: https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/HTML/lexical/IfcToroidalSurface.htm

### Spline Surfaces
- [x] **AcisSplineSurface** → `geo_su.BSplineSurfaceWithKnots` or `geo_su.RationalBSplineSurfaceWithKnots`
  - Status: ✅ Implemented in converter.py
  - Maps to: `ada.geom.surfaces.BSplineSurfaceWithKnots` or `ada.geom.surfaces.RationalBSplineSurfaceWithKnots`

## Solid Conversions (ACIS → adapy)

### Primitive Solids (TODO)
- [ ] **Create solid from cylindrical surface** → `geo_so.Cylinder`
  - Target: `ada.geom.solids.Cylinder`
  - Status: ⚠️ Class exists, need integration logic
  - Notes: Need to determine when to create solid vs surface

- [ ] **Create solid from conical surface** → `geo_so.Cone`
  - Target: `ada.geom.solids.Cone`
  - Status: ⚠️ Class exists, need integration logic

- [ ] **Create solid from spherical surface** → `geo_so.Sphere`
  - Target: `ada.geom.solids.Sphere`
  - Status: ⚠️ Class exists, need integration logic

## Topological Conversions

### Faces and Shells
- [x] **AcisFace** → `geo_su.FaceSurface` or `geo_su.AdvancedFace`
  - Status: ✅ Implemented in converter.py
  - Notes: Creates FaceSurface for planar surfaces, AdvancedFace for complex surfaces

- [x] **Face with loops** → `geo_su.FaceBound` with `geo_cu.EdgeLoop`
  - Status: ✅ Implemented in converter.py
  - Notes: Currently handles single loop per face

- [ ] **Multiple loops per face** (holes/voids)
  - Status: ⚠️ TODO in converter.py
  - Notes: Need to iterate through all loops via next_loop_ref

### Bodies and Assemblies
- [ ] **AcisBody** → `Assembly` with `Part` objects
  - Status: ❌ Not implemented
  - Notes: Currently creates empty Assembly, need to properly map bodies to parts

- [ ] **AcisLump** → Organization structure
  - Status: ❌ Not implemented
  - Notes: Need to decide how to map lump hierarchy

- [ ] **AcisShell** → `geo_su.ClosedShell` or `geo_su.OpenShell`
  - Status: ❌ Not implemented
  - Notes: Need to collect all faces in shell

## Transformation Support (TODO)
- [ ] **AcisTransform** → `ada.api.transforms.Transform`
  - Status: ❌ Not implemented
  - Notes: Need to apply transformations to geometry

## Attribute Support (TODO)
- [ ] **AcisNameAttrib** → Naming of parts/shapes
  - Status: ⚠️ Partial - face names only
  - Notes: Expand to support body/part names

- [ ] **AcisRgbColorAttrib** → Color/appearance
  - Status: ❌ Not implemented
  - Notes: May need to add to Part/Shape metadata

## Implementation Priority Order

1. ✅ **Phase 1: Basic topology** (COMPLETED)
   - Plane surfaces
   - B-spline surfaces
   - Basic curves (lines, circles, ellipses, B-splines)
   - Single-loop faces

2. ✅ **Phase 2: Analytic surfaces** (COMPLETED)
   - [x] Add CylindricalSurface class to surfaces.py
   - [x] Add ConicalSurface class to surfaces.py
   - [x] Add SphericalSurface class to surfaces.py
   - [x] Add ToroidalSurface class to surfaces.py
   - [x] Implement converters for these surfaces

3. ✅ **Phase 3: Advanced topology** (COMPLETED)
   - [x] Multiple loops per face (holes)
   - [x] Shell collection and organization
   - [x] Body to geometry collection mapping

4. 🔄 **Phase 4: Integration** (CURRENT)
   - [ ] Integrate converted geometry into Assembly/Part structure
   - [ ] Create Shape objects from ClosedShell geometry
   - [ ] Handle body names and attributes properly
   - [ ] Transformation support
   - [ ] Color attributes
   - [ ] Advanced naming

5. **Phase 5: Integration and optimization**
   - [ ] Convert geometry to Part/Shape objects
   - [ ] Handle instancing/references
   - [ ] Optimize memory usage for large models

## Summary of Completed Work

### Geometry Classes Added (surfaces.py)
1. **CylindricalSurface** - STEP AP242/IFC4x3 compliant
2. **ConicalSurface** - STEP AP242/IFC4x3 compliant
3. **SphericalSurface** - STEP AP242/IFC4x3 compliant
4. **ToroidalSurface** - STEP AP242/IFC4x3 compliant

### Converter Implementations (converter.py)
1. **Surface Converters**:
   - `convert_cylinder_surface()` - Maps ACIS origin/axis/radius to CylindricalSurface
   - `convert_cone_surface()` - Calculates semi-angle from sine/cosine, creates ConicalSurface
   - `convert_sphere_surface()` - Maps ACIS center/pole/equator to SphericalSurface
   - `convert_torus_surface()` - Maps ACIS major/minor radii to ToroidalSurface

2. **Topology Converters**:
   - `convert_face_bounds()` - Enhanced to handle multiple loops (outer boundary + holes)
   - `convert_body()` - Processes lump and shell hierarchies
   - `convert_shell()` - Collects all faces into ClosedShell/OpenShell
   - `convert_all_bodies()` - Organizes geometry by body structure

### Test Results
- **Largest reference model**: ~130k entities
- **Parse Success**: ✅ All entities parsed
- **Conversion Success**: ✅ ~2.5k faces converted successfully
- **Export Success**: ✅ STEP file generated

### Coverage Summary
- ✅ **Curves**: Line, Circle, Ellipse, BSplineCurve (rational & non-rational)
- ✅ **Surfaces**: Plane, Cylinder, Cone, Sphere, Torus, BSplineSurface (rational & non-rational)
- ✅ **Topology**: Faces (single & multiple loops), Shells, Bodies
- ⚠️ **Integration**: Geometry conversion complete, need Assembly/Part integration

## Notes
- All basic curve types from ACIS are fully supported
- All analytic surfaces (cylinder, cone, sphere, torus) are now implemented
- B-spline surfaces (the most complex geometry) are fully supported
- Multiple loops per face (holes/voids) are properly handled
- Body/shell hierarchy is preserved during conversion
- Next step: Integrate converted geometry into Assembly/Part/Shape structure for full workflow support

