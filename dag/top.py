#!/usr/bin/env python3
"""
  generate_startup_graph.py
  – produces:
      • startup_order.json
      • dependency_dag.svg
"""
import json, pathlib, networkx as nx, yaml
from networkx.drawing.nx_agraph import to_agraph

# Use the improved YAML file from the previous example
data = yaml.safe_load(open("topology.yaml"))

G                   = nx.DiGraph()
logical2concrete    = {}   # Maps logical names to concrete node IDs: {'sor': ['sor-0', …]}

# ─────────────────────────────────────────────────────────────────────────────
# 1) Explode every shard-group and add nodes
# ─────────────────────────────────────────────────────────────────────────────
def add_node(logical, idx, spec):
    """Adds a single node to the graph with all its attributes."""
    nid   = f"{logical}-{idx}" if idx is not None else logical
    attrs = {**spec, "logical": logical}
    
    if idx is not None:
        attrs["index"] = idx
        # Replace $INDEX placeholder in the command
        if "$INDEX" in attrs.get("cmd", ""):
            attrs["cmd"] = attrs["cmd"].replace("$INDEX", str(idx))
            
    G.add_node(nid, **attrs)
    logical2concrete.setdefault(logical, []).append(nid)

# Process shard groups
for g_name, g_spec in data.get("shard_groups", {}).items():
    for i in range(g_spec["count"]):
        # CORRECTED: Iterate over the list of components directly
        for component_spec in g_spec["components"]:
            svc_name = component_spec['name']
            # Pass the full spec dictionary to the add_node function
            add_node(svc_name, i, {**component_spec, "group": g_name})

# Process singletons
for svc, spec in data.get("singletons", {}).items():
    add_node(svc, None, spec)

# ─────────────────────────────────────────────────────────────────────────────
# 2) Add edges based on 'depends_on'
# ─────────────────────────────────────────────────────────────────────────────
for node, attrs in G.nodes(data=True):
    deps = attrs.get("depends_on", {})

    # Dependencies on singletons
    for dep in deps.get("singletons", []):
        for target in logical2concrete.get(dep, []):
            G.add_edge(target, node)

    # Dependencies on entire shard groups
    for dep_group_name in deps.get("shards", []):
        # The dependency is on the main component of the shard group (e.g., 'sor' for the 'sor' group)
        for target in logical2concrete.get(dep_group_name, []):
            G.add_edge(target, node)
    
    # Implicit dependency: Sub-components depend on the primary component within the same shard
    # e.g., 'faxer-receiver-0' depends on 'sor-0'
    if "group" in attrs:
        group_name = attrs["group"]
        idx = attrs["index"]
        primary_component_node = f"{group_name}-{idx}"
        # Ensure it's not the primary component depending on itself
        if node != primary_component_node and G.has_node(primary_component_node):
             G.add_edge(primary_component_node, node)


# ─────────────────────────────────────────────────────────────────────────────
# 3) Write artifacts
# ─────────────────────────────────────────────────────────────────────────────
try:
    # Generate the topological sort for the orchestrator
    startup_order = list(nx.topological_sort(G))
    pathlib.Path("startup_order.json").write_text(json.dumps(startup_order, indent=2))

    # Generate the visual DAG with clustering
    A = to_agraph(G)
    A.graph_attr.update(rankdir="TB", splines="ortho", nodesep=0.3)
    A.node_attr.update(shape="box", style="rounded,filled", fillcolor="#E8E8E8")
    A.edge_attr.update(arrowsize="0.7")

    # Create visual clusters for each shard instance
    for g_name, g_spec in data.get("shard_groups", {}).items():
        for i in range(g_spec["count"]):
            nodes_in_shard = [f"{comp['name']}-{i}" for comp in g_spec["components"]]
            A.add_subgraph(
                nodes_in_shard,
                name=f"cluster_{g_name}_{i}",
                label=f"{g_name}-{i}",
                style="filled",
                fillcolor="#F5F5F5",
            )

    A.layout("dot")
    A.draw("dependency_dag.svg")

    print("✓ startup_order.json  ✓ dependency_dag.svg")

except nx.exception.NetworkXUnfeasible as e:
    print(f"🚨 Error: A dependency cycle was detected in your graph! {e}")
except Exception as e:
    print(f"An unexpected error occurred: {e}")