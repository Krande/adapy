import pathlib
import sqlite3

_RESULTS_SCHEMA_PATH = pathlib.Path(__file__).parent / "resources/results.sql"


class SQLiteFEAStore:
    def __init__(self, db_file, clean_tables=False):
        if isinstance(db_file, str):
            db_file = pathlib.Path(db_file)
        clean_start = False
        if not db_file.exists():
            clean_start = True

        self.db_file = db_file
        self.conn = sqlite3.connect(db_file)
        if clean_start:
            self._init_db()
        else:
            if clean_tables:
                # clear all tables
                self.conn.executescript("DELETE FROM HistOutput;")
                self.conn.executescript("DELETE FROM FieldElem;")
                self.conn.executescript("DELETE FROM FieldNodes;")
                self.conn.executescript("DELETE FROM FieldVars;")
                self.conn.executescript("DELETE FROM ModelInstances;")
                self.conn.executescript("DELETE FROM Steps;")
                self.conn.executescript("DELETE FROM FieldVars;")
                self.conn.executescript("DELETE FROM Points;")
                self.conn.executescript("DELETE FROM ElementConnectivity;")
                self.conn.executescript("DELETE FROM ElementInfo;")
                self.conn.executescript("DELETE FROM PointSets;")
                self.conn.executescript("DELETE FROM ElementSets;")

        self.cursor = self.conn.cursor()

    def __del__(self):
        self.conn.close()

    def _init_db(self):
        with open(_RESULTS_SCHEMA_PATH, "r") as f:
            schema = f.read()
        self.conn.executescript(schema)

    def insert_table(self, table_name: str, data: list[tuple]):
        if not data:
            print("No data to insert")
            return

        num_columns = len(data[0])
        placeholders = ", ".join(["?" for _ in range(num_columns)])
        sql_query = f"INSERT INTO {table_name} VALUES ({placeholders})"

        self.cursor.executemany(sql_query, data)
        self.conn.commit()

    def get_steps(self):
        query = """SELECT * FROM Steps"""
        self.cursor.execute(query)
        results = self.cursor.fetchall()
        return results

    def get_field_vars(self):
        query = """SELECT * FROM FieldVars"""
        self.cursor.execute(query)
        results = self.cursor.fetchall()
        return results

    def get_history_data(
        self, field_var=None, step_id=None, instance_id=None, point_id=None, elem_id=None, return_df=False
    ):
        base_query = """SELECT mi.Name,
                       ho.ResType,
                       ho.Region,
                       ho.PointID,
                       ho.ElemID,
                       st.Name,
                       fv.Name,
                       ho.Frame,
                       ho.Value
                    FROM FieldVars as fv
                         INNER JOIN HistOutput ho ON fv.FieldID = ho.FieldVarID
                         INNER JOIN ModelInstances as mi on ho.InstanceID = mi.ID
                         INNER JOIN Steps as st on ho.StepID = st.ID
                
                    """
        params = []

        add_queries = []
        if field_var is not None:
            add_queries += ["fv.Name == ?"]
            params = [field_var]

        if step_id is not None:
            add_queries += ["ho.StepID = ?"]
            params.append(step_id)

        if instance_id is not None:
            add_queries += ["ho.InstanceID = ?"]
            params.append(instance_id)

        if point_id is not None:
            add_queries += ["ho.PointID = ?"]
            params.append(point_id)

        if elem_id is not None:
            add_queries += ["ho.ElemID = ?"]
            params.append(elem_id)

        if len(add_queries) > 0:
            base_query += "WHERE " + add_queries[0]
            if len(add_queries) > 1:
                extra_queries = " AND".join([f" AND {x}" for x in add_queries[1:]])
                base_query += extra_queries

        self.cursor.execute(base_query, params)
        results = self.cursor.fetchall()
        if return_df:
            import pandas as pd

            columns = [
                "Name",
                "Restype",
                "Region",
                "PointID",
                "ElemID",
                "StepName",
                "FieldVarName",
                "Frame",
                "Value",
            ]
            df = pd.DataFrame(results, columns=columns)
            return df
        return results

    def get_field_elem_data(self, name, step_id=None, instance_id=None, elem_id=None, int_point=None):
        """This returns a join from the FieldVars table and the FieldElem tables."""
        base_query = """SELECT mi.Name,
                             fe.ElemID,
                            st.Name,
                            fv.Name,
                            fe.IntPt,
                            fe.Frame,
                            fe.Value
                            FROM FieldVars as fv
                              INNER JOIN FieldElem fe ON fv.FieldID = fe.FieldVarID
                              INNER JOIN ModelInstances as mi on fe.InstanceID = mi.ID
                              INNER JOIN Steps as st on fe.StepID = st.ID
    
                            WHERE fv.Name = ?"""

        params = [name]

        if step_id is not None:
            base_query += " AND fe.StepID = ?"
            params.append(step_id)

        if instance_id is not None:
            base_query += " AND fe.InstanceID = ?"
            params.append(instance_id)

        if elem_id is not None:
            base_query += " AND fe.ElemID = ?"
            params.append(elem_id)

        if int_point is not None:
            base_query += " AND fe.IntPt = ?"
            params.append(int_point)

        self.cursor.execute(base_query, params)
        results = self.cursor.fetchall()
        return results

    def get_field_nodal_data(self, name, step_id=None, instance_id=None, point_id=None):
        """This returns a join from the FieldVars table and the FieldNodes tables."""
        base_query = """SELECT mi.Name,
                           fn.PointID,
                           st.Name,
                           fv.Name,
                           fn.Frame,
                           fn.Value
                        FROM FieldVars as fv
                             INNER JOIN FieldNodes fn ON fv.FieldID = fn.FieldVarID
                             INNER JOIN ModelInstances as mi on fn.InstanceID = mi.ID
                             INNER JOIN Steps as st on fn.StepID = st.ID

                        WHERE fv.Name = ?"""

        params = [name]

        if step_id is not None:
            base_query += " AND fn.StepID = ?"
            params.append(step_id)

        if instance_id is not None:
            base_query += " AND fn.InstanceID = ?"
            params.append(instance_id)

        if point_id is not None:
            base_query += " AND fn.PointID = ?"
            params.append(point_id)

        self.cursor.execute(base_query, params)
        results = self.cursor.fetchall()
        return results

    def __repr__(self):
        return f"SQLiteFEAStore({self.db_file})"
