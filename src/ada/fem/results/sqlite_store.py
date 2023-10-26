import sqlite3


class SQLiteFEAStore:
    def __init__(self, db_file):
        self.db_file = db_file
        self.conn = sqlite3.connect(db_file)
        self.cursor = self.conn.cursor()

    def __del__(self):
        self.conn.close()

    def get_history_data(self, name, step_id=None, instance_id=None):
        base_query = """SELECT mi.Name,
                           ho.ResType,
                           PointID,
                           st.Name,
                           fv.Name,
                           Frame,
                           Value
                        FROM FieldVars as fv
                             INNER JOIN HistOutput ho ON fv.FieldID = ho.FieldVarID
                             INNER JOIN ModelInstances as mi on ho.InstanceID = mi.ID
                             INNER JOIN Steps as st on ho.StepID = st.ID
                    
                        WHERE fv.Name == ?"""
        params = [name]

        if step_id is not None:
            base_query += " AND fn.StepID = ?"
            params.append(step_id)

        if instance_id is not None:
            base_query += " AND fn.InstanceID = ?"
            params.append(instance_id)

        self.cursor.execute(base_query, params)
        results = self.cursor.fetchall()
        return results

    def get_field_data(self, name, step_id=None, instance_id=None):
        """This returns a join from the FieldVars table and the FieldNodes and FieldElem tables."""
        base_query = """SELECT mi.Name,
                           PointID,
                           st.Name,
                           fv.Name,
                           Frame,
                           Value
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

        self.cursor.execute(base_query, params)
        results = self.cursor.fetchall()
        return results
