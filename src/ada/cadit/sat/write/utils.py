class IDGenerator:
    def __init__(self, start_id: int = 0):
        self.current_id = start_id

    def next_id(self) -> int:
        id_val = self.current_id
        self.current_id += 1
        return id_val
