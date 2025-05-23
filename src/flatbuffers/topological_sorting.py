from collections import defaultdict, deque


def topological_sort(inclusions: dict):
    """
    Performs topological sorting based on schema inclusions.

    Parameters:
        inclusions (dict): Dictionary where keys are schema names and values are sets of schemas they include.

    Returns:
        list: A list of schema files sorted in topological order based on their inclusions.
    """
    # Step 1: Build the graph and compute in-degrees
    graph = defaultdict(list)
    in_degree = defaultdict(int)

    for schema, included_schemas in inclusions.items():
        for included_schema in included_schemas:
            graph[schema].append(included_schema)
            in_degree[included_schema] += 1
        if schema not in in_degree:
            in_degree[schema] = 0

    # Step 2: Topological sort using Kahn's algorithm (BFS)
    queue = deque([schema for schema in in_degree if in_degree[schema] == 0])
    sorted_schemas = []

    while queue:
        schema = queue.popleft()
        sorted_schemas.append(schema)

        for neighbor in graph[schema]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    # If there's a cycle, sorted_schemas will have fewer elements than in_degree keys
    if len(sorted_schemas) != len(in_degree):
        print("Warning: The schema files contain circular dependencies.")
        return []

    return sorted_schemas
