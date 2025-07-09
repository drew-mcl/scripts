#!/usr/bin/env python3
"""
Generates a startup order and a simple DAG from a flat topology file.
"""
import json
import pathlib
import networkx as nx
import yaml
from networkx.drawing.nx_agraph import to_agraph

# 1. Load the simplified YAML data
try:
    with open("topology.yaml", "r") as f:
        data = yaml.safe_load(f)
except FileNotFoundError:
    print("Error: topology.yaml not found.")
    exit(1)

# 2. Build the graph from the flat structure
G = nx.DiGraph()

# Add all services as nodes to the graph
for service_name, spec in data.items():
    G.add_node(service_name, **spec)

# Add edges based on the 'depends_on' list
for service_name, spec in data.items():
    for dependency in spec.get("depends_on", []):
        if G.has_node(dependency):
            G.add_edge(dependency, service_name)
        else:
            print(f"Warning: Dependency '{dependency}' for service '{service_name}' not found.")

# 3. Generate the startup_order.json file
try:
    startup_order = list(nx.topological_sort(G))
    pathlib.Path("startup_order.json").write_text(
        json.dumps(startup_order, indent=2)
    )
except nx.exception.NetworkXUnfeasible as e:
    print(f"🚨 Error: A dependency cycle was detected in your graph! {e}")
    exit(1)

# 4. Generate the visual dependency_dag.svg
A = to_agraph(G)
A.graph_attr.update(
    rankdir="TB",         # Top-to-Bottom layout
    splines="ortho",      # Clean, right-angled lines
    nodesep="0.6",
    ranksep="0.5"
)
A.node_attr.update(
    shape="box",
    style="rounded,filled",
    fillcolor="#EAEAFB",
    fontname="Helvetica"
)
A.edge_attr.update(arrowsize="0.8")
A.layout("dot")
A.draw("dependency_dag.svg")

print("✓ startup_order.json  ✓ dependency_dag.svg")