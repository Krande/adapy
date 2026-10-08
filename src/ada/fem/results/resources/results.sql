-- adapy FEA results SQLite schema.
--
-- Written by the ODB exporter (ada/fem/formats/abaqus/results/aba_io.py, run under
-- `abaqus python`), which executes this file as-is. Read by
-- ada.fem.formats.abaqus.results.sqlite_stream (the streaming bake) and
-- ada.fem.formats.abaqus.results.read_odb.read_results_sqlite (FEAResult).
-- It is public API, and so is its location in the installed package,
-- ada/fem/results/resources/results.sql (ada.fem.results.sqlite_schema.SCHEMA_PATH): an exporter
-- in another language can embed it at build time. Any exporter that writes it gets adapy's
-- readers for free.
--
-- RULES
--   * Tables are STRICT: SQLite rejects a value of the wrong type at insert time, whoever writes
--     it. Needs SQLite >= 3.37 to write and to read.
--   * Writers insert by column name, so columns may be added; removing or renaming one, or
--     changing its type, breaks writers -- bump the version and say so.
--   * Bump `user_version` (last line) on any change. v1: untyped; v2: STRICT, same columns.
--     Readers read v0 (pre-versioning, same as v1) to the current version.
--   * Every statement is idempotent (IF NOT EXISTS), so a writer can re-open a partly written
--     file, e.g. to resume an export that crashed mid-step.
--   * No indexes here: they would slow the bulk insert. Readers add the ones they need.
--
-- IDs start at 0. ModelInstances ID 0 is the root ASSEMBLY. Field data is blob-packed: one row
-- per (instance, step, component, frame[, location, element type]) holding N little-endian
-- float32 values, in the order of Points (by ID) or ElementInfo (by ID within Type) of that
-- instance; element blobs are n_elements x NIPs.

CREATE TABLE IF NOT EXISTS metadata (project TEXT, user TEXT, filename TEXT) STRICT;

CREATE TABLE IF NOT EXISTS "ModelInstances" (ID INTEGER, Name TEXT) STRICT;

CREATE TABLE IF NOT EXISTS "Points" (InstanceID INTEGER, ID INTEGER, X REAL, Y REAL, Z REAL) STRICT;

CREATE TABLE IF NOT EXISTS "ElementConnectivity" (InstanceID INTEGER, ElemID INTEGER, PointID INTEGER, Seq INTEGER) STRICT;

CREATE TABLE IF NOT EXISTS "ElementInfo" (InstanceID INTEGER, ElemID INTEGER, Type TEXT, IntPoints INTEGER) STRICT;

CREATE TABLE IF NOT EXISTS "ElementSets" (SetID INTEGER, Name TEXT, InstanceID INTEGER, ElemID INTEGER) STRICT;

CREATE TABLE IF NOT EXISTS "PointSets" (SetID INTEGER, Name TEXT, InstanceID INTEGER, PointID INTEGER) STRICT;

-- Procedure distinguishes static / dynamic / frequency / ... within the broader DomainType.
CREATE TABLE IF NOT EXISTS "Steps" (ID INTEGER, Name TEXT, Description TEXT, DomainType TEXT, Procedure TEXT) STRICT;

-- One row per (step, frame). FieldNodes / FieldElem / HistOutput reference a frame by its REAL
-- value (time / frequency); FrameID is the frame's index in the step, Increment its solver id.
CREATE TABLE IF NOT EXISTS "Frames" (StepID INTEGER, FrameID INTEGER, Increment INTEGER, FrameValue REAL, Description TEXT) STRICT;

-- Linear-elastic + density properties of the materials sections reference.
CREATE TABLE IF NOT EXISTS "Materials" (Name TEXT, Description TEXT, Density REAL, YoungsModulus REAL, PoissonRatio REAL) STRICT;

-- SubTypeId: the ODB API's section-subtype enum (shell / beam / solid / ...).
CREATE TABLE IF NOT EXISTS "Sections" (Name TEXT, SubTypeId INTEGER, MaterialName TEXT, Thickness REAL, Profile TEXT) STRICT;

-- (instance, element set) -> section. Set membership lives in ElementSets.
CREATE TABLE IF NOT EXISTS "SectionAssignments" (InstanceID INTEGER, SetName TEXT, SectionName TEXT, Offset REAL) STRICT;

-- Section points per (instance, element type, result position), e.g. TOP / BOT of a shell layer.
-- FieldElem does not split by section point: where a field has several, the last one written
-- fills the slot.
CREATE TABLE IF NOT EXISTS "SectionPoints" (InstanceID INTEGER, ElemType TEXT, Position TEXT, PointNumber INTEGER, Description TEXT) STRICT;

-- Local coordinate systems of element sets. Kind: material / rebar / beam. Axis (1-3) + Angle
-- (deg): the optional extra rotation about that axis.
CREATE TABLE IF NOT EXISTS "MaterialOrientations" (
    InstanceID INTEGER, SetName TEXT, Kind TEXT, CsysName TEXT, CsysType TEXT,
    OriginX REAL, OriginY REAL, OriginZ REAL,
    XAxisX REAL, XAxisY REAL, XAxisZ REAL,
    YAxisX REAL, YAxisY REAL, YAxisZ REAL,
    ZAxisX REAL, ZAxisY REAL, ZAxisZ REAL,
    Axis INTEGER, Angle REAL
) STRICT;

-- One row per (surface, element face).
CREATE TABLE IF NOT EXISTS "Surfaces" (InstanceID INTEGER, Name TEXT, ElemLabel INTEGER, FaceId INTEGER) STRICT;

-- Inventory only: the read-side ODB API exposes no region / type / value for BCs and loads.
CREATE TABLE IF NOT EXISTS "BoundaryConditions" (Name TEXT) STRICT;

CREATE TABLE IF NOT EXISTS "Loads" (Name TEXT) STRICT;

-- One row per component (U1, S11, ...) or history output (EIGFREQ, ...); Description is the
-- owning field's ("Spatial displacement"), which groups components back into a field.
CREATE TABLE IF NOT EXISTS "FieldVars" (FieldID INTEGER, Name TEXT, Description TEXT) STRICT;

-- One row per history sample. PointID / ElemID are -1 where the region has no node / element.
CREATE TABLE IF NOT EXISTS "HistOutput" (
    Region TEXT, ResType TEXT, InstanceID INTEGER, ElemID INTEGER, PointID INTEGER,
    StepID INTEGER, FieldVarID INTEGER, Frame REAL, Value REAL
) STRICT;

-- IsImaginary: complex outputs (steady-state dynamics, ...) write a real (0) and an imaginary
-- (1) row. "Data", not "Values": VALUES is reserved in SQLite.
CREATE TABLE IF NOT EXISTS "FieldNodes" (
    InstanceID INTEGER, StepID INTEGER, FieldVarID INTEGER, Frame REAL, IsImaginary INTEGER, Data BLOB
) STRICT;

-- Location: the result position (INTEGRATION_POINT, ELEMENT_NODAL, CENTROID, ...). Data is
-- n_elements x NIPs, where NIPs is the integration points per element -- or, for ELEMENT_NODAL,
-- the nodes per element, in connectivity order. NIPs is per row, so readers must not take it
-- from ElementInfo.IntPoints.
CREATE TABLE IF NOT EXISTS "FieldElem" (
    InstanceID INTEGER, StepID INTEGER, FieldVarID INTEGER, Location TEXT, ElemType TEXT, NIPs INTEGER,
    Frame REAL, IsImaginary INTEGER, Data BLOB
) STRICT;

PRAGMA user_version = 2;
