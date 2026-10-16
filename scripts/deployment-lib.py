"""Selected immutable lock graph; no deployment policy or private source lookup."""
import json


def selected_lock(lock, names):
    nodes, root = lock["nodes"], lock["root"]
    def resolve(edge, seen=()):
        if isinstance(edge, str):
            if edge not in nodes:
                raise ValueError("Missing lock node")
            return edge
        key = tuple(edge)
        if key in seen:
            raise ValueError("Cyclic follows")
        current = root
        for segment in edge:
            current = resolve(nodes[current]["inputs"][segment], (*seen, key))
        return current
    roots = {name: resolve(nodes[root]["inputs"][name]) for name in names}
    result = {"root": {"inputs": roots}}
    def visit(node):
        if node in result:
            return
        value = json.loads(json.dumps(nodes[node]))
        edges = {name: resolve(edge) for name, edge in value.get("inputs", {}).items()}
        value["inputs"] = {
            name: ([next(alias for alias, target in roots.items() if target == edges[name])]
                   if isinstance(edge, list) and edges[name] in roots.values() else edges[name])
            for name, edge in value.get("inputs", {}).items()
        }
        result[node] = value
        for target in edges.values():
            visit(target)
    for node in roots.values():
        visit(node)
    return {"version": 7, "root": "root", "nodes": result}
