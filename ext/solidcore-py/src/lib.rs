//! EXPERIMENTAL: a thin PyO3 binding of solidcore (https://github.com/tangency-labs/solidcore),
//! enough for adapy's prototype `SolidcoreBackend` (src/ada/cad/solidcore_backend.py).
//!
//! It wraps `solidcore-ops` (the kernel's application-level modelling calls), `solidcore-tess`
//! (display meshing) and `solidcore-step` (AP242 writing). Nothing here computes geometry: each
//! function converts Python values, makes one ops call and converts the answer back. Topology is
//! handed to Python as the B-rep record's serde JSON (`Body.record_json`), so the Python side can
//! explore faces/edges/vertices without a binding per query.
//!
//! Units: solidcore works in millimetres (its tolerances are mm). The binding passes numbers
//! through unchanged; the Python backend decides the scale.

use std::cell::RefCell;

use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyTuple};

use solidcore_ops::boolean::{boolean, union_all};
use solidcore_ops::order::Order;
use solidcore_ops::sweep::{self, Curve, DraftCorner};
use solidcore_ops::{make_box, make_cone, make_cylinder, make_sphere, Body, BooleanOp, CheckLevel};
use solidcore_record::brep::Brep;
use solidcore_tess::{plan, MeshTolerance};

fn kerr(e: impl std::fmt::Display) -> PyErr {
    PyRuntimeError::new_err(format!("solidcore: {e}"))
}

/// A solidcore body. Operations that consume a body in the kernel (booleans) copy it first, so a
/// Python `Body` is never invalidated by being used as an operand.
#[pyclass(unsendable, name = "Body", module = "solidcore_py")]
struct PyBody {
    body: Body,
    // (record, order) cached on first topology query; dropped by anything that edits the body.
    record: RefCell<Option<(Brep, Order)>>,
}

impl PyBody {
    fn new(body: Body) -> Self {
        PyBody { body, record: RefCell::new(None) }
    }

    fn with_record<T>(&self, f: impl FnOnce(&Brep, &Order) -> PyResult<T>) -> PyResult<T> {
        if self.record.borrow().is_none() {
            let r = self.body.to_record().map_err(kerr)?;
            *self.record.borrow_mut() = Some(r);
        }
        let held = self.record.borrow();
        let (rec, order) = held.as_ref().expect("filled above");
        f(rec, order)
    }
}

#[pymethods]
impl PyBody {
    /// An independent copy.
    fn copy(&self) -> PyResult<PyBody> {
        Ok(PyBody::new(self.body.copy().map_err(kerr)?))
    }

    /// Transform in place by the kernel's ROW-vector 4x4 (`[x y z 1] * m`): pass the transpose
    /// of a column-vector matrix.
    fn transform(&self, m: [[f64; 4]; 4]) -> PyResult<()> {
        self.body.transform(&m).map_err(kerr)?;
        *self.record.borrow_mut() = None;
        Ok(())
    }

    /// [faces, edges, vertices]
    fn counts(&self) -> PyResult<[usize; 3]> {
        self.body.counts().map_err(kerr)
    }

    #[pyo3(signature = (relative_accuracy = 1e-9))]
    fn volume(&self, relative_accuracy: f64) -> PyResult<f64> {
        self.body.volume(relative_accuracy).map_err(kerr)
    }

    #[pyo3(signature = (relative_accuracy = 1e-9))]
    fn area(&self, relative_accuracy: f64) -> PyResult<f64> {
        self.body.area(relative_accuracy).map_err(kerr)
    }

    /// (area, volume, centroid)
    #[pyo3(signature = (relative_accuracy = 1e-9))]
    fn mass_properties(&self, relative_accuracy: f64) -> PyResult<(f64, f64, [f64; 3])> {
        let m = self.body.mass_properties(relative_accuracy).map_err(kerr)?;
        Ok((m.area, m.volume, m.centroid))
    }

    /// (lo, hi)
    fn bounds(&self) -> PyResult<([f64; 3], [f64; 3])> {
        self.body.bounds().map_err(kerr)
    }

    /// The kernel's validity check: (failures, report text). level 0 basic, 1 geometry, 2 all.
    #[pyo3(signature = (level = 1))]
    fn check(&self, level: u8) -> PyResult<(usize, String)> {
        let lv = match level {
            0 => CheckLevel::Basic,
            1 => CheckLevel::Geometry,
            _ => CheckLevel::All,
        };
        let r = self.body.check(lv).map_err(kerr)?;
        Ok((r.failures, r.text))
    }

    /// None when the body is a closed solid facing out, else why not.
    fn check_solid(&self) -> PyResult<Option<String>> {
        match self.body.check_solid() {
            Ok(()) => Ok(None),
            Err(e) => Ok(Some(e.to_string())),
        }
    }

    fn is_manifold_solid(&self) -> PyResult<bool> {
        self.body.is_manifold_solid().map_err(kerr)
    }

    fn solid_count(&self) -> PyResult<usize> {
        self.body.solid_count().map_err(kerr)
    }

    /// "inside" | "outside" | "on"
    fn classify_point(&self, p: [f64; 3]) -> PyResult<&'static str> {
        use solidcore_ops::PointClass;
        Ok(match self.body.classify_point(p).map_err(kerr)? {
            PointClass::Inside => "inside",
            PointClass::Outside => "outside",
            PointClass::OnBoundary => "on",
        })
    }

    /// The B-rep record (schema 4) as JSON: regions, shells, faces, edges, vertices.
    fn record_json(&self) -> PyResult<String> {
        self.with_record(|r, _| serde_json::to_string(r).map_err(kerr))
    }

    #[pyo3(signature = (face, relative_accuracy = 1e-9))]
    fn face_area(&self, face: usize, relative_accuracy: f64) -> PyResult<f64> {
        self.with_record(|_, o| self.body.face_area(o, face, relative_accuracy).map_err(kerr))
    }

    /// (area, centroid)
    #[pyo3(signature = (face, relative_accuracy = 1e-9))]
    fn face_centroid(&self, face: usize, relative_accuracy: f64) -> PyResult<(f64, [f64; 3])> {
        self.with_record(|_, o| {
            let c = self.body.face_centroid(o, face, relative_accuracy).map_err(kerr)?;
            Ok((c.area, c.centroid))
        })
    }

    /// The outward unit normal of a planar face, None for a face that is not planar.
    fn face_plane_normal(&self, face: usize) -> PyResult<Option<[f64; 3]>> {
        self.with_record(|_, o| Ok(self.body.face_plane_normal(o, face).map_err(kerr)?.map(|n| n.value)))
    }

    #[pyo3(signature = (edge, relative_accuracy = 1e-9))]
    fn edge_length(&self, edge: usize, relative_accuracy: f64) -> PyResult<f64> {
        self.with_record(|_, o| self.body.edge_length(o, edge, relative_accuracy).map_err(kerr))
    }

    /// Display mesh: (positions xyz flat, triangle indices flat, normals flat, face id per
    /// triangle, number of faces the planner refused). `chord` <= 0 means 1/1000 of the body.
    #[pyo3(signature = (chord = -1.0, angle = None))]
    fn tessellate(
        &self,
        chord: f64,
        angle: Option<f64>,
    ) -> PyResult<(Vec<f64>, Vec<u32>, Vec<f64>, Vec<u32>, usize)> {
        let (lo, hi) = self.body.bounds().map_err(kerr)?;
        let size = ((hi[0] - lo[0]).powi(2) + (hi[1] - lo[1]).powi(2) + (hi[2] - lo[2]).powi(2)).sqrt();
        let tol = if chord > 0.0 {
            MeshTolerance { chord, angle: angle.or(Some(solidcore_tess::DISPLAY_ANGLE)) }
        } else {
            MeshTolerance::display_of(size)
        };
        self.with_record(|rec, _| {
            let (p, _stats) = plan(rec, &tol, size).map_err(|e| kerr(format!("{e:?}")))?;
            let (m, refused) = p.evaluate();
            let mut pos = Vec::with_capacity(m.positions.len() * 3);
            for v in &m.positions {
                pos.extend_from_slice(&[v.x, v.y, v.z]);
            }
            let mut nrm = Vec::with_capacity(m.normals.len() * 3);
            for v in &m.normals {
                nrm.extend_from_slice(&[v.x, v.y, v.z]);
            }
            let idx: Vec<u32> = m.triangles.iter().flatten().copied().collect();
            Ok((pos, idx, nrm, m.tri_face, refused.len()))
        })
    }

    /// The kernel's native body file (`.smb`), binary.
    fn to_smb<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyBytes>> {
        let b = self.body.to_smb(false).map_err(kerr)?;
        Ok(PyBytes::new(py, &b))
    }
}

/// Curves come from Python as tuples:
///   ("line", start, end)
///   ("arc", center, axis, reference, radius, start_angle, end_angle)   (radians, CCW about axis)
///   ("nurbs", degree, [[x, y, z, w], ...], knots)
fn curve(obj: &Bound<'_, PyAny>) -> PyResult<Curve> {
    let t = obj.downcast::<PyTuple>()?;
    let kind: String = t.get_item(0)?.extract()?;
    Ok(match kind.as_str() {
        "line" => Curve::Line { start: t.get_item(1)?.extract()?, end: t.get_item(2)?.extract()? },
        "arc" => Curve::Arc {
            center: t.get_item(1)?.extract()?,
            axis: t.get_item(2)?.extract()?,
            reference: t.get_item(3)?.extract()?,
            radius: t.get_item(4)?.extract()?,
            start: t.get_item(5)?.extract()?,
            end: t.get_item(6)?.extract()?,
        },
        "nurbs" => Curve::Nurbs {
            degree: t.get_item(1)?.extract()?,
            points: t.get_item(2)?.extract()?,
            knots: t.get_item(3)?.extract()?,
        },
        other => return Err(PyValueError::new_err(format!("unknown curve kind {other:?}"))),
    })
}

fn curves(objs: &Bound<'_, PyAny>) -> PyResult<Vec<Curve>> {
    objs.try_iter()?.map(|o| curve(&o?)).collect()
}

#[pyfunction(name = "make_box")]
fn py_make_box(origin: [f64; 3], size: [f64; 3]) -> PyResult<PyBody> {
    Ok(PyBody::new(make_box(origin, size).map_err(kerr)?))
}

#[pyfunction(name = "make_cylinder")]
fn py_make_cylinder(origin: [f64; 3], radius: f64, height: f64) -> PyResult<PyBody> {
    Ok(PyBody::new(make_cylinder(origin, radius, height).map_err(kerr)?))
}

#[pyfunction(name = "make_cone")]
fn py_make_cone(origin: [f64; 3], base_radius: f64, top_radius: f64, height: f64) -> PyResult<PyBody> {
    Ok(PyBody::new(make_cone(origin, base_radius, top_radius, height).map_err(kerr)?))
}

#[pyfunction(name = "make_sphere")]
fn py_make_sphere(center: [f64; 3], radius: f64) -> PyResult<PyBody> {
    Ok(PyBody::new(make_sphere(center, radius).map_err(kerr)?))
}

/// The planar closed profile extruded `distance` along `direction`, optionally drafted.
#[pyfunction]
#[pyo3(signature = (profile, direction, distance, draft = 0.0, cap = true))]
fn extrude(profile: &Bound<'_, PyAny>, direction: [f64; 3], distance: f64, draft: f64, cap: bool) -> PyResult<PyBody> {
    let p = curves(profile)?;
    Ok(PyBody::new(sweep::extrude(&p, direction, distance, draft, DraftCorner::Curvature, cap).map_err(kerr)?))
}

#[pyfunction]
#[pyo3(signature = (profile, origin, axis, angle, cap = true))]
fn revolve(profile: &Bound<'_, PyAny>, origin: [f64; 3], axis: [f64; 3], angle: f64, cap: bool) -> PyResult<PyBody> {
    let p = curves(profile)?;
    Ok(PyBody::new(sweep::revolve(&p, origin, axis, angle, cap).map_err(kerr)?))
}

/// A skin through the sections (each a closed list of curves); degree 1 is ruled.
#[pyfunction]
#[pyo3(signature = (sections, degree = 1, cap = true))]
fn loft(sections: &Bound<'_, PyAny>, degree: u32, cap: bool) -> PyResult<PyBody> {
    let s: Vec<Vec<Curve>> = sections.try_iter()?.map(|o| curves(&o?)).collect::<PyResult<_>>()?;
    Ok(PyBody::new(sweep::loft(&s, degree, cap).map_err(kerr)?))
}

/// A sheet of planar faces bounded by the closed loops.
#[pyfunction]
fn planar_faces(loops: &Bound<'_, PyAny>) -> PyResult<PyBody> {
    Ok(PyBody::new(sweep::planar_faces(&curves(loops)?).map_err(kerr)?))
}

/// "union" | "subtract" | "intersect". Operands are copied. None for an empty result.
#[pyfunction(name = "boolean")]
fn py_boolean(a: &PyBody, b: &PyBody, op: &str) -> PyResult<Option<PyBody>> {
    let op = match op {
        "union" => BooleanOp::Union,
        "subtract" => BooleanOp::Subtract,
        "intersect" => BooleanOp::Intersect,
        o => return Err(PyValueError::new_err(format!("unknown boolean op {o:?}"))),
    };
    let r = boolean(a.body.copy().map_err(kerr)?, b.body.copy().map_err(kerr)?, op).map_err(kerr)?;
    Ok(r.map(PyBody::new))
}

#[pyfunction(name = "union_all")]
fn py_union_all(bodies: Vec<PyRef<'_, PyBody>>) -> PyResult<PyBody> {
    let copies = bodies.iter().map(|b| b.body.copy().map_err(kerr)).collect::<PyResult<Vec<_>>>()?;
    Ok(PyBody::new(union_all(copies).map_err(kerr)?))
}

/// AP242 Part 21 text of the named bodies (millimetres).
#[pyfunction]
#[pyo3(signature = (bodies, file_name = String::new()))]
fn write_step(bodies: Vec<(String, PyRef<'_, PyBody>)>, file_name: String) -> PyResult<String> {
    let named: Vec<(&str, &Body)> = bodies.iter().map(|(n, b)| (n.as_str(), &b.body)).collect();
    let header = solidcore_step::write::product::Header {
        file_name,
        preprocessor_version: "solidcore-py (adapy prototype)".into(),
        ..Default::default()
    };
    solidcore_step::write::product::solidcore_bodies(&named, &header).map_err(|e| kerr(format!("{e:?}")))
}

#[pyfunction]
fn from_smb(data: Vec<u8>) -> PyResult<PyBody> {
    Ok(PyBody::new(solidcore_ops::Body::from_smb(data, false).map_err(kerr)?))
}

#[pymodule]
fn solidcore_py(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyBody>()?;
    m.add_function(wrap_pyfunction!(py_make_box, m)?)?;
    m.add_function(wrap_pyfunction!(py_make_cylinder, m)?)?;
    m.add_function(wrap_pyfunction!(py_make_cone, m)?)?;
    m.add_function(wrap_pyfunction!(py_make_sphere, m)?)?;
    m.add_function(wrap_pyfunction!(extrude, m)?)?;
    m.add_function(wrap_pyfunction!(revolve, m)?)?;
    m.add_function(wrap_pyfunction!(loft, m)?)?;
    m.add_function(wrap_pyfunction!(planar_faces, m)?)?;
    m.add_function(wrap_pyfunction!(py_boolean, m)?)?;
    m.add_function(wrap_pyfunction!(py_union_all, m)?)?;
    m.add_function(wrap_pyfunction!(write_step, m)?)?;
    m.add_function(wrap_pyfunction!(from_smb, m)?)?;
    Ok(())
}
