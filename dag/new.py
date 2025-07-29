#!/usr/bin/env python3
"""
  generate_startup_graph.py
  – produces:
      • startup_order.json
      • dependency_dag.svg
      • dependency_dag_simple.svg (optional)
"""
import json
import pathlib
import networkx as nx
import yaml
import argparse
import sys
from networkx.drawing.nx_agraph import to_agraph

# --- Detailed Graph Functions (Existing) ---

def build_graph(data):
    # This function remains unchanged
    G = nx.DiGraph()
    for name, spec in data.get("singletons", {}).items():
        G.add_node(name, **spec, type='singleton', logical=name)
    for g_name, g_spec in data.get("shard_groups", {}).items():
        for i in range(g_spec["count"]):
            for component in g_spec["components"]:
                svc_name = component['name']
                nid = f"{g_name}-{svc_name}-{i}"
                attrs = {**component, "type": "sharded", "group": g_name, "logical": svc_name, "index": i}
                if "$INDEX" in attrs.get("cmd", ""):
                    attrs["cmd"] = attrs["cmd"].replace("$INDEX", str(i))
                G.add_node(nid, **attrs)
    return G

def add_dependencies(G):
    # This function remains unchanged
    for node, attrs in G.nodes(data=True):
        deps = attrs.get("depends_on", {})
        for dep_singleton in deps.get("singletons", []):
            if G.has_node(dep_singleton):
                G.add_edge(dep_singleton, node)
        for dep_shard_group in deps.get("shards", []):
            for target_node, target_attrs in G.nodes(data=True):
                if target_attrs.get("group") == dep_shard_group and target_attrs.get("logical") == dep_shard_group:
                    G.add_edge(target_node, node)
        if attrs.get("type") == "sharded":
            group_name = attrs["group"]
            logical_name = attrs["logical"]
            if logical_name != group_name:
                primary_component_nid = f"{group_name}-{group_name}-{attrs['index']}"
                if G.has_node(primary_component_nid):
                    G.add_edge(primary_component_nid, node)

def generate_svg(G, data, filename="dependency_dag.svg"):
    # This function remains unchanged
    palette = [("#F0F6FF", "#AECBFA"), ("#E5F8F0", "#A3E5C7"), ("#FEF5E5", "#FADCA3"), ("#F5E5F4", "#DDA3D5")]
    group_colors = {name: palette[i % len(palette)] for i, name in enumerate(data.get("shard_groups", {}).keys())}
    A = to_agraph(G)
    A.graph_attr.update(rankdir="TB", splines="ortho", nodesep="0.5", ranksep="0.8", fontname="Helvetica", fontsize=12, concentrate=True, compound=True)
    A.node_attr.update(shape="box", style="rounded,filled", fontname="Helvetica", fontsize=11, width=1.5, height=0.6)
    A.edge_attr.update(arrowsize="0.8", color="#444444")
    for n in G.nodes():
        node_obj = A.get_node(n)
        attrs = G.nodes[n]
        node_type = attrs.get("type")
        node_obj.attr['label'] = f"{attrs['logical']}-{attrs['index']}" if node_type == "sharded" else attrs['logical']
        if node_type == "singleton":
            node_obj.attr["fillcolor"] = "#EAEAFB"; node_obj.attr["penwidth"] = 1.5
        elif node_type == "sharded":
            group_name = attrs["group"]
            cluster_bg, primary_fill = group_colors.get(group_name, ("#EEEEEE", "#CCCCCC"))
            if attrs["logical"] == group_name:
                node_obj.attr["fillcolor"] = primary_fill; node_obj.attr["penwidth"] = 2.0
            else:
                node_obj.attr["fillcolor"] = "#FFFFFF"
    for g_name, g_spec in data.get("shard_groups", {}).items():
        cluster_bg, _ = group_colors.get(g_name)
        for i in range(g_spec["count"]):
            nodes_in_shard = [n for n, attrs in G.nodes(data=True) if attrs.get("group") == g_name and attrs.get("index") == i]
            A.add_subgraph(nodes_in_shard, name=f"cluster_{g_name}_{i}", label=f"{g_name}-{i}", style="filled,rounded", fillcolor=cluster_bg, color="#CCCCCC", fontname="Helvetica", fontsize=10)
    A.layout("dot"); A.draw(filename)

# --- NEW: Simple DAG Functions ---

def build_logical_graph(data):
    """Builds a simplified graph of logical components, without sharding."""
    LG = nx.DiGraph()
    
    # 1. Gather all unique logical components and their specs
    logical_components = {}
    for name, spec in data.get("singletons", {}).items():
        logical_components[name] = spec
    for g_name, g_spec in data.get("shard_groups", {}).items():
        for component in g_spec["components"]:
            comp_name = component['name']
            # Store the spec only for the first time we see a logical component
            if comp_name not in logical_components:
                logical_components[comp_name] = component
    
    # 2. Add a node for each logical component
    for name in logical_components:
        LG.add_node(name)
        
    # 3. Add edges based on dependencies
    for name, spec in logical_components.items():
        # Explicit dependencies on singletons and other shards
        deps = spec.get("depends_on", {})
        for dep in deps.get("singletons", []) + deps.get("shards", []):
            LG.add_edge(dep, name)

    # Implicit intra-shard dependencies
    for g_name, g_spec in data.get("shard_groups", {}).items():
        for component in g_spec["components"]:
            # If a component is not the primary one, it depends on the primary
            if component['name'] != g_name:
                LG.add_edge(g_name, component['name'])

    return LG

def generate_simple_svg(G, filename="dependency_dag_simple.svg"):
    """Creates a simple, left-to-right SVG of the logical graph."""
    A = to_agraph(G)
    A.graph_attr.update(rankdir="LR", fontname="Helvetica")
    A.node_attr.update(shape="box", style="rounded,filled", fillcolor="#EAEAFB", fontname="Helvetica")
    A.edge_attr.update(arrowsize="0.8")
    A.layout("dot")
    A.draw(filename)
    print(f"✓ {filename}")


# --- Main Execution ---

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate startup graph and DAG from topology.yaml.")
    parser.add_argument("--dag", action="store_true", help="Generate a simplified logical dependency graph only.")
    args = parser.parse_args()

    try:
        with open("topology.yaml", "r") as f:
            topology_data = yaml.safe_load(f)
    except FileNotFoundError:
        print("Error: topology.yaml not found.")
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"Error parsing YAML file: {e}")
        sys.exit(1)

    # --- Mode selection ---
    if args.dag:
        # If --dag is passed, run the simple mode and exit.
        logical_graph = build_logical_graph(topology_data)
        generate_simple_svg(logical_graph)
        sys.exit(0)

    # --- Default full-featured execution ---
    try:
        graph = build_graph(topology_data)
        add_dependencies(graph)

        # Write out the topological sort for the orchestrator
        startup_order = list(nx.topological_sort(graph))
        pathlib.Path("startup_order.json").write_text(json.dumps(startup_order, indent=2))

        # Generate the detailed visual DAG
        generate_svg(graph, topology_data)

        print("✓ startup_order.json  ✓ dependency_dag.svg")

    except nx.exception.NetworkXUnfeasible:
        print("🚨 Error: A dependency cycle was detected in your graph!")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")