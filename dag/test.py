import yaml, networkx as nx, json, pathlib, sys
from networkx.drawing.nx_agraph import to_agraph

edges = yaml.safe_load(open("services.yaml"))
placement = {}
if "--with-placement" in sys.argv[1:]:
    placement = yaml.safe_load(open("placement-prod.yaml")).get("placements", {})

G = nx.DiGraph()
logical2concrete = {}

def add_nodes(name, meta):
    reps = meta.get("replicas", 1)
    for i in range(reps):
        n = f"{name}-{i}" if reps > 1 else name
        node_meta = {**meta, **placement.get(n, {}), "logical": name}
        node_meta["cmd"] = meta["cmd"].replace("$INDEX", str(i))
        G.add_node(n, **node_meta)
        logical2concrete.setdefault(name, []).append(n)

for svc, meta in edges["services"].items():
    add_nodes(svc, meta)

for node, data in G.nodes(data=True):
    for dep in edges["services"][data["logical"]].get("depends_on", []):
        for concrete in logical2concrete[dep]:
            G.add_edge(concrete, node)

# artefacts
pathlib.Path("startup_order.json").write_text(
    json.dumps(list(nx.topological_sort(G)), indent=2)
)
to_agraph(G).layout("dot").draw("dependency_dag.svg")