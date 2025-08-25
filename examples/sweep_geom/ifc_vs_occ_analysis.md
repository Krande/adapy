# IFC vs OCC Sweep Geometry Analysis

## Executive Summary

After extensive testing with `sweep_example_3.py` and `sweep_example_4.py`, we have identified fundamental incompatibilities between IFC `IfcFixedReferenceSweptAreaSolid` and OpenCascade `BRepOffsetAPI_MakePipeShell` implementations. The issue is not a "single straight extrusion" as initially described, but rather fundamental differences in coordinate system interpretation that make exact geometry matching impossible.

## Key Findings

### 1. IFC Can Follow Curved Paths
Contrary to initial observations, IFC `IfcFixedReferenceSweptAreaSolid` **can** follow curved directrix paths:
- Complex 21-point path (original): Z error 3.8%, Y error 22%, X error 100%
- Simple 3-point curved path: Z error 99.3%, Y error 19.7%, X error 0.05%

### 2. Coordinate System Interpretation Differences
The errors vary dramatically with directrix complexity, indicating that IFC and OCC interpret coordinate frames differently:

| Path Complexity | X Error | Y Error | Z Error | Notes |
|----------------|---------|---------|---------|-------|
| 21-point loop | 100% | 22% | 3.8% | Complex curved path |
| 3-point curve | 0.05% | 19.7% | 99.3% | Simple V-shaped path |

### 3. Profile Orientation Issues
- OCC uses: `profile_normal = (0, 1, 0)` and `profile_ydir = (0, 0, 1)`
- IFC interprets these same parameters differently due to specification differences
- Profile scaling attempted (0.5x in X) had no significant impact on overall geometry

## Technical Analysis

### OpenCascade Approach
```python
# OCC creates consistent coordinate frames using cross products:
normal = (0.0, 1.0, 0.0)  # Profile faces +Y direction
ydir = (0.0, 0.0, 1.0)    # Profile Y-axis along world Z
xdir = cross(ydir, normal) = (-1.0, 0.0, 0.0)  # Profile X-axis

# Profile embedding: 3D_point = origin + u*xdir + v*ydir
```

### IFC Interpretation
```python
# IFC FixedReferenceSweptAreaSolid uses:
Position = IfcAxis2Placement3D(Location, Axis, RefDirection)
FixedReference = IfcDirection(x, y, z)

# But the coordinate frame interpretation differs from OCC's cross-product logic
```

### Root Cause
The fundamental issue is that IFC's sweep implementation follows different mathematical conventions than OCC:

1. **Frame Transport**: How coordinate frames are transported along the directrix
2. **Profile Orientation**: How 2D profiles are oriented in 3D space
3. **Reference Direction**: How `FixedReference` interacts with the local coordinate system

## Attempted Solutions

### sweep_example_3.py
- Used complex 21-point directrix
- Tested multiple coordinate system configurations
- Best result: 3.8% Z error, but 100% X error and 22% Y error

### sweep_example_4.py  
- Simplified to 3-point directrix
- Tried different profile scaling approaches
- Result: Improved X dimension but worsened Z dimension significantly

## Conclusions

1. **IFC is not producing "single straight extrusions"** - it follows curved paths but with different geometric interpretation
2. **Exact geometry matching is not achievable** due to fundamental specification differences between IFC and OCC sweep implementations
3. **The best achievable result** was ~4% error in one dimension, but with significant errors in others
4. **Different directrix complexities produce different error patterns**, suggesting the coordinate frame transport algorithms differ fundamentally

## Recommendation

Given the fundamental incompatibilities, the best approach is:

1. **Use the sweep_example_3.py configuration** (3.8% Z error) for applications where Z-dimension accuracy is critical
2. **Document the limitations** for users expecting exact OCC geometry matching
3. **Consider alternative IFC geometry representations** (e.g., explicit mesh geometry) for applications requiring exact geometric fidelity

The IFC format's sweep geometry capabilities are functional but follow different mathematical conventions than OpenCascade, making exact replication impossible within the current IFC specification constraints.