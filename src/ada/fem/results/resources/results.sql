create table ElementConnectivity
(
    InstanceID INTEGER,
    ElemID     INTEGER,
    PointID    INTEGER,
    Seq        INTEGER
);

create table ElementInfo
(
    InstanceID INTEGER,
    ElemID     INTEGER,
    Type       TEXT,
    IntPoints  INTEGER
);

create table ElementSets
(
    SetID      INTEGER,
    Name       TEXT,
    InstanceID INTEGER,
    ElemID     INTEGER
);

create table FieldElem
(
    InstanceID INTEGER,
    ElemID     INTEGER,
    StepID     INTEGER,
    Location   TEXT,
    IntPt      INTEGER,
    FieldVarID INTEGER,
    Frame      REAL,
    Value      REAL
);

create table FieldNodes
(
    InstanceID INTEGER,
    PointID    INTEGER,
    StepID     INTEGER,
    FieldVarID INTEGER,
    Frame      REAL,
    Value      REAL
);

create table FieldVars
(
    FieldID     INTEGER,
    Name        TEXT,
    Description TEXT
);

create table HistOutput
(
    Region     TEXT,
    ResType    TEXT,
    InstanceID INTEGER,
    ElemID     INTEGER,
    PointID    INTEGER,
    StepID     INTEGER,
    FieldVarID INTEGER,
    Frame      REAL,
    Value      REAL
);

create table ModelInstances
(
    ID   INTEGER,
    Name TEXT
);

create table PointSets
(
    SetID      INTEGER,
    Name       TEXT,
    InstanceID INTEGER,
    PointID    INTEGER
);

create table Points
(
    InstanceID INTEGER,
    ID         INTEGER,
    X          REAL,
    Y          REAL,
    Z          REAL
);

create table Steps
(
    ID          INTEGER,
    Name        TEXT,
    Description TEXT,
    DomainType  TEXT
);

create table metadata
(
    project  TEXT,
    user     TEXT,
    filename TEXT
);