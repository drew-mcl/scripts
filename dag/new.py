#!/usr/bin/env python3
"""
  generate_startup_graph.py
  – produces:
      • startup_order.json
      • dependency_dag.svg (Correctly namespaced)
"""
import json
import pathlib
import networkx as nx
import yaml
from networkx.drawing.nx_agraph import to_agraph

def build_graph(data):
    """Builds the NetworkX graph with correctly namespaced nodes."""
    G = nx.DiGraph()

    # 1. Add singleton nodes
    for name, spec in data.get("singletons", {}).items():
        G.add_node(name, **spec, type='singleton', logical=name)

    # 2. Add sharded nodes with unique, group-prefixed names
    for g_name, g_spec in data.get("shard_groups", {}).items():
        for i in range(g_spec["count"]):
            for component in g_spec["components"]:
                svc_name = component['name']
                # Create a globally unique node ID, e.g., "sor-faxer-receiver-0"
                nid = f"{g_name}-{svc_name}-{i}"
                
                attrs = {**component, "type": "sharded", "group": g_name, "logical": svc_name, "index": i}
                if "$INDEX" in attrs.get("cmd", ""):
                    attrs["cmd"] = attrs["cmd"].replace("$INDEX", str(i))
                
                G.add_node(nid, **attrs)
    return G

def add_dependencies(G):
    """Adds edges by querying node attributes directly."""
    for node, attrs in G.nodes(data=True):
        deps = attrs.get("depends_on", {})

        # A. Dependencies on singletons (e.g., sor-sor-0 -> watchdog)
        for dep_singleton in deps.get("singletons", []):
            if G.has_node(dep_singleton):
                G.add_edge(dep_singleton, node)

        # B. Dependencies on entire shard groups (e.g., big -> sor-sor-0, sor-sor-1, ...)
        for dep_shard_group in deps.get("shards", []):
            # Find all primary components of the target group
            for target_node, target_attrs in G.nodes(data=True):
                if target_attrs.get("group") == dep_shard_group and target_attrs.get("logical") == dep_shard_group:
                    G.add_edge(target_node, node)
        
        # C. Implicit intra-shard dependencies
        # e.g., "sor-faxer-receiver-0" depends on its group's primary component, "sor-sor-0"
        if attrs.get("type") == "sharded":
            group_name = attrs["group"]
            logical_name = attrs["logical"]
            
            # A component depends on its shard's primary component unless it *is* the primary component
            if logical_name != group_name:
                primary_component_nid = f"{group_name}-{group_name}-{attrs['index']}"
                if G.has_node(primary_component_nid):
                    G.add_edge(primary_component_nid, node)

def generate_svg(G, data, filename="dependency_dag.svg"):
    """Creates a visually polished and clustered SVG of the graph."""
    # A color palette for different shard groups
    # Colors from https://coolors.co
    palette = [
        # Cluster BG,    Primary Node Fill
        ("#F0F6FF", "#AECBFA"), # Blue
        ("#E5F8F0", "#A3E5C7"), # Green
        ("#FEF5E5", "#FADCA3"), # Yellow
        ("#F5E5F4", "#DDA3D5"), # Purple
    ]

    group_colors = {
        name: palette[i % len(palette)]
        for i, name in enumerate(data.get("shard_groups", {}).keys())
    }

    A = to_agraph(G)
    A.graph_attr.update(
        rankdir="TB",         # Top-to-Bottom layout
        splines="ortho",      # Use 90-degree lines
        nodesep="0.5",        # Space between nodes on the same rank
        ranksep="0.8",        # Space between ranks (vertical)
        fontname="Helvetica",
        fontsize=12,
        concentrate=True,     # Bundle parallel edges
        compound=True,        # Allow edges to target clusters
    )
    A.node_attr.update(
        shape="box",
        style="rounded,filled",
        fontname="Helvetica",
        fontsize=11,
        width=1.5,
        height=0.6
    )
    A.edge_attr.update(arrowsize="0.8", color="#444444")

    # Style nodes based on their type and group
    for n in G.nodes():
        node_obj = A.get_node(n)
        attrs = G.nodes[n]
        node_type = attrs.get("type")

        # Set a clean label from the logical name and index
        node_obj.attr['label'] = f"{attrs['logical']}-{attrs['index']}" if node_type == "sharded" else attrs['logical']

        if node_type == "singleton":
            node_obj.attr["fillcolor"] = "#EAEAFB" # Light purple for singletons
            node_obj.attr["penwidth"] = 1.5
        elif node_type == "sharded":
            group_name = attrs["group"]
            cluster_bg, primary_fill = group_colors.get(group_name, ("#EEEEEE", "#CCCCCC"))
            
            # Make the primary component of a shard stand out
            if attrs["logical"] == group_name:
                node_obj.attr["fillcolor"] = primary_fill
                node_obj.attr["penwidth"] = 2.0 # Thicker border
            else:
                node_obj.attr["fillcolor"] = "#FFFFFF" # White for sub-components

    # Create and color visual clusters for each shard instance
    for g_name, g_spec in data.get("shard_groups", {}).items():
        cluster_bg, _ = group_colors.get(g_name)
        for i in range(g_spec["count"]):
            nodes_in_shard = [n for n, attrs in G.nodes(data=True) if attrs.get("group") == g_name and attrs.get("index") == i]
            A.add_subgraph(
                nodes_in_shard,
                name=f"cluster_{g_name}_{i}",
                label=f"{g_name}-{i}",
                style="filled,rounded",
                fillcolor=cluster_bg,
                color="#CCCCCC",
                fontname="Helvetica",
                fontsize=10,
            )

    A.layout("dot")
    A.draw(filename)

# --- Main Execution ---
if __name__ == "__main__":
    try:
        with open("topology.yaml", "r") as f:
            topology_data = yaml.safe_load(f)

        graph = build_graph(topology_data)
        add_dependencies(graph)

        # Write out the topological sort for the orchestrator
        startup_order = list(nx.topological_sort(graph))
        pathlib.Path("startup_order.json").write_text(json.dumps(startup_order, indent=2))

        # Generate the visual DAG
        generate_svg(graph, topology_data)

        print("✓ startup_order.json  ✓ dependency_dag.svg")

    except nx.exception.NetworkXUnfeasible:
        print("🚨 Error: A dependency cycle was detected in your graph!")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")