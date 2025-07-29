// FILE: go.mod
// This file defines the module's path and its dependencies.
module yourcorp/topology

go 1.22

require gopkg.in/yaml.v3 v3.0.1

// END FILE: go.mod

// ------------------------------------------------------------------

// FILE: types.go
// This file contains the Go structs that map directly to the input YAML schema.
package topology

// YAMLTopology is the top-level structure for unmarshaling the topology.yaml file.
type YAMLTopology struct {
	Version int                      `yaml:"version"`
	Shards  map[string]int           `yaml:"shards"`
	Apps    map[string]AppDefinition `yaml:"apps"`
}

// AppDefinition defines the properties of a single logical application.
type AppDefinition struct {
	DependsOn  []string `yaml:"depends_on"`
	SameHostAs string   `yaml:"same_host_as"`
}

// END FILE: types.go

// ------------------------------------------------------------------

// FILE: graph.go
// This file defines the primary output data structures: Graph and Node.
// It also includes the DOT language renderer for visualization.
package topology

import (
	"bytes"
	"fmt"
	"sort"
	"strings"
)

// Graph represents the fully expanded and validated dependency graph.
// It is the primary output of the parsing process.
type Graph struct {
	// Nodes is a map of all concrete nodes in the topology, keyed by their
	// unique ID (e.g., "sor-03").
	Nodes map[string]*Node
}

// Node represents a single, concrete instance of an application shard.
// For example, if 'sor' has 8 shards, there will be 8 'sor' nodes
// (sor-0, sor-1, ..., sor-7).
type Node struct {
	ID        string // Unique identifier, e.g., "sor-03"
	BaseApp   string // The logical app name from YAML, e.g., "sor"
	Shard     int    // The zero-based shard index.
	DependsOn []*Node
	SameHostAs *Node
}

// DOT generates a Graphviz DOT language representation of the graph.
// This output can be used by tools like `dot` to render visual diagrams.
// The output is deterministic.
func (g *Graph) DOT() (string, error) {
	var b bytes.Buffer

	b.WriteString("digraph G {\n")
	b.WriteString("  rankdir=TB;\n")
	b.WriteString("  node [shape=box, style=rounded];\n")

	// Sort node keys for deterministic output
	nodeKeys := make([]string, 0, len(g.Nodes))
	for k := range g.Nodes {
		nodeKeys = append(nodeKeys, k)
	}
	sort.Strings(nodeKeys)

	// Define nodes
	for _, key := range nodeKeys {
		node := g.Nodes[key]
		b.WriteString(fmt.Sprintf("  \"%s\";\n", node.ID))
	}

	b.WriteString("\n")

	// Define edges
	for _, key := range nodeKeys {
		node := g.Nodes[key]

		// DependsOn edges (solid black)
		for _, dep := range node.DependsOn {
			b.WriteString(fmt.Sprintf("  \"%s\" -> \"%s\";\n", node.ID, dep.ID))
		}

		// SameHostAs edges (dashed, no direction, custom color)
		if node.SameHostAs != nil {
			// To avoid drawing edges twice, only draw from the "smaller" string ID
			if strings.Compare(node.ID, node.SameHostAs.ID) < 0 {
				b.WriteString(fmt.Sprintf("  \"%s\" -> \"%s\" [dir=none, style=dashed, color=blue, constraint=false];\n", node.ID, node.SameHostAs.ID))
			}
		}
	}

	b.WriteString("}\n")
	return b.String(), nil
}

// END FILE: graph.go

// ------------------------------------------------------------------

// FILE: parser.go
// This file contains the core logic for parsing, expanding, validating,
// and building the topology graph.
package topology

import (
	"bytes"
	"fmt"
	"sort"
	"strings"

	"gopkg.in/yaml.v3"
)

// ParseYAML takes a byte slice of a YAML topology file and returns a fully
// validated and expanded Graph object. It performs all validation steps,
// including schema checks, reference integrity, and cycle detection.
func ParseYAML(data []byte) (*Graph, error) {
	// 1. Unmarshal YAML with strict checking for unknown fields.
	var topology YAMLTopology
	decoder := yaml.NewDecoder(bytes.NewReader(data))
	decoder.KnownFields(true)
	if err := decoder.Decode(&topology); err != nil {
		return nil, fmt.Errorf("yaml schema validation failed: %w", err)
	}

	// 2. Initial validation and node expansion.
	graph := &Graph{Nodes: make(map[string]*Node)}
	appShardCounts := make(map[string]int)

	// First, create all concrete nodes based on shard counts.
	for appName := range topology.Apps {
		shardCount, ok := topology.Shards[appName]
		if !ok {
			shardCount = 1 // Default to 1 shard if not specified.
		}
		appShardCounts[appName] = shardCount

		for i := 0; i < shardCount; i++ {
			nodeID := getNodeID(appName, i, shardCount)
			if _, exists := graph.Nodes[nodeID]; exists {
				// This should not happen if app names are unique.
				return nil, fmt.Errorf("internal error: duplicate node ID generated: %s", nodeID)
			}
			graph.Nodes[nodeID] = &Node{
				ID:      nodeID,
				BaseApp: appName,
				Shard:   i,
			}
		}
	}

	// 3. Link edges and perform reference/consistency validation.
	for appName, appDef := range topology.Apps {
		// Validate same_host_as rules.
		if appDef.SameHostAs != "" {
			if appDef.SameHostAs == appName {
				return nil, fmt.Errorf("validation failed: app '%s' cannot have same_host_as itself", appName)
			}
			targetApp := appDef.SameHostAs
			if _, ok := topology.Apps[targetApp]; !ok {
				return nil, fmt.Errorf("validation failed: same_host_as target '%s' for app '%s' does not exist", targetApp, appName)
			}
			if appShardCounts[appName] != appShardCounts[targetApp] {
				return nil, fmt.Errorf("validation failed: same_host_as requires matching shard counts, but '%s' (%d) and '%s' (%d) differ", appName, appShardCounts[appName], targetApp, appShardCounts[targetApp])
			}
		}

		// Link edges for each concrete node of the app.
		for i := 0; i < appShardCounts[appName]; i++ {
			nodeID := getNodeID(appName, i, appShardCounts[appName])
			node := graph.Nodes[nodeID]

			// Link same_host_as edge.
			if appDef.SameHostAs != "" {
				targetID := getNodeID(appDef.SameHostAs, i, appShardCounts[appDef.SameHostAs])
				node.SameHostAs = graph.Nodes[targetID]
			}

			// Link depends_on edges.
			for _, depName := range appDef.DependsOn {
				if _, ok := topology.Apps[depName]; !ok {
					return nil, fmt.Errorf("validation failed: depends_on target '%s' for app '%s' does not exist", depName, appName)
				}
				depShardCount := appShardCounts[depName]

				// A sharded app can depend on another app with the same shard count (1-to-1)
				// or on a singleton app (N-to-1). Other combinations are ambiguous.
				if depShardCount != 1 && depShardCount != appShardCounts[appName] {
					return nil, fmt.Errorf("validation failed: ambiguous dependency from '%s' (%d shards) to '%s' (%d shards)", appName, appShardCounts[appName], depName, depShardCount)
				}

				depShardIndex := i
				if depShardCount == 1 {
					depShardIndex = 0 // All shards depend on the single instance.
				}

				depNodeID := getNodeID(depName, depShardIndex, depShardCount)
				depNode, ok := graph.Nodes[depNodeID]
				if !ok {
					return nil, fmt.Errorf("internal error: dependency node '%s' not found", depNodeID)
				}
				node.DependsOn = append(node.DependsOn, depNode)
			}
		}
	}

	// 4. Detect dependency cycles.
	if cyclePath, ok := detectCycle(graph); ok {
		return nil, fmt.Errorf("validation failed: dependency cycle detected: %s", strings.Join(cyclePath, " -> "))
	}

	return graph, nil
}

// getNodeID is a helper to consistently generate node IDs.
func getNodeID(appName string, shardIndex, shardCount int) string {
	if shardCount == 1 {
		return appName
	}
	return fmt.Sprintf("%s-%02d", appName, shardIndex)
}

// detectCycle performs a DFS-based cycle detection on the graph's dependency edges.
func detectCycle(g *Graph) ([]string, bool) {
	// Sort keys for deterministic traversal.
	nodeKeys := make([]string, 0, len(g.Nodes))
	for k := range g.Nodes {
		nodeKeys = append(nodeKeys, k)
	}
	sort.Strings(nodeKeys)

	visiting := make(map[string]bool) // Nodes currently in the recursion stack (path).
	visited := make(map[string]bool)  // Nodes that have been fully explored.

	for _, key := range nodeKeys {
		if !visited[key] {
			path, hasCycle := dfsVisit(g.Nodes[key], visiting, visited)
			if hasCycle {
				// Reverse path for intuitive reading (A -> B -> C -> A)
				for i, j := 0, len(path)-1; i < j; i, j = i+1, j-1 {
					path[i], path[j] = path[j], path[i]
				}
				return path, true
			}
		}
	}
	return nil, false
}

func dfsVisit(node *Node, visiting, visited map[string]bool) ([]string, bool) {
	visiting[node.ID] = true

	// Sort dependencies for deterministic traversal.
	sort.Slice(node.DependsOn, func(i, j int) bool {
		return node.DependsOn[i].ID < node.DependsOn[j].ID
	})

	for _, dep := range node.DependsOn {
		if visiting[dep.ID] {
			// Cycle detected.
			return []string{dep.ID, node.ID}, true
		}
		if !visited[dep.ID] {
			path, hasCycle := dfsVisit(dep, visiting, visited)
			if hasCycle {
				// Prepend current node to the cycle path until we find the start of the cycle.
				if path[0] == node.ID {
					return path, true
				}
				return append([]string{node.ID}, path...), true
			}
		}
	}

	visiting[node.ID] = false
	visited[node.ID] = true
	return nil, false
}

// END FILE: parser.go

// ------------------------------------------------------------------

// FILE: traversal.go
// This file contains algorithms for traversing the dependency graph to determine
// startup and shutdown orders.
package topology

import (
	"sort"
)

// GetStartupOrder performs a topological sort on the graph to determine the
// order of operations. It returns a slice of "layers", where each layer
// contains nodes that can be started concurrently.
func GetStartupOrder(graph *Graph) [][]*Node {
	// Kahn's algorithm for topological sorting.

	// 1. Compute in-degrees for all nodes.
	inDegree := make(map[string]int)
	for _, node := range graph.Nodes {
		inDegree[node.ID] = 0
	}
	for _, node := range graph.Nodes {
		for _, dep := range node.DependsOn {
			inDegree[node.ID]++
		}
	}

	// 2. Initialize queue with all nodes that have an in-degree of 0.
	// These are the leaf nodes of the dependency graph (e.g., watchdog).
	queue := make([]*Node, 0)
	for id, degree := range inDegree {
		if degree == 0 {
			queue = append(queue, graph.Nodes[id])
		}
	}

	var order [][]*Node
	for len(queue) > 0 {
		// Sort the current queue layer for deterministic output.
		sort.Slice(queue, func(i, j int) bool {
			return queue[i].ID < queue[j].ID
		})

		order = append(order, queue)
		
		// Process all nodes in the current layer.
		nextQueue := make([]*Node, 0)
		
		// Find all nodes that depend on the nodes in the current layer.
		for _, node := range graph.Nodes {
			 for _, dep := range node.DependsOn {
				 for _, qNode := range queue {
					 if dep.ID == qNode.ID {
						 inDegree[node.ID]--
						 if inDegree[node.ID] == 0 {
							 nextQueue = append(nextQueue, node)
						 }
					 }
				 }
			 }
		}
		queue = nextQueue
	}

	return order
}

// GetShutdownOrder returns the reverse of the startup order, which is the
// safe sequence for shutting down services.
func GetShutdownOrder(graph *Graph) [][]*Node {
	startup := GetStartupOrder(graph)
	// Reverse the order of layers.
	for i, j := 0, len(startup)-1; i < j; i, j = i+1, j-1 {
		startup[i], startup[j] = startup[j], startup[i]
	}
	return startup
}

// GetSubgraphFor creates a new graph containing only the specified node
// and all of its transitive dependencies. This is useful for generating a
// startup plan for a single failed service.
func GetSubgraphFor(graph *Graph, startNodeID string) (*Graph, error) {
	if _, ok := graph.Nodes[startNodeID]; !ok {
		return nil, fmt.Errorf("node '%s' not found in the graph", startNodeID)
	}

	subgraph := &Graph{Nodes: make(map[string]*Node)}
	
	var collectDeps func(node *Node)
	collectDeps = func(node *Node) {
		if _, exists := subgraph.Nodes[node.ID]; exists {
			return // Already visited.
		}
		subgraph.Nodes[node.ID] = node
		for _, dep := range node.DependsOn {
			collectDeps(dep)
		}
	}
	
	collectDeps(graph.Nodes[startNodeID])
	return subgraph, nil
}

// END FILE: traversal.go

// ------------------------------------------------------------------

// FILE: cmd/yaml2dot/main.go
// This file provides the command-line utility for converting a topology
// YAML file into a DOT graph, as described in the README.
package main

import (
	"fmt"
	"io"
	"os"
	"yourcorp/topology"
)

func main() {
	// Read topology from standard input.
	yamlData, err := io.ReadAll(os.Stdin)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error reading from stdin: %v\n", err)
		os.Exit(1)
	}

	// Parse the YAML into a graph.
	graph, err := topology.ParseYAML(yamlData)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error parsing topology: %v\n", err)
		os.Exit(1)
	}

	// Render the graph to DOT format.
	dotOutput, err := graph.DOT()
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error rendering DOT graph: %v\n", err)
		os.Exit(1)
	}

	// Print the result to standard output.
	fmt.Print(dotOutput)
}

// END FILE: cmd/yaml2dot/main.go

// ------------------------------------------------------------------

// FILE: parser_test.go
// This file contains unit tests for the topology parser.
package topology_test

import (
	"strings"
	"testing"
	"yourcorp/topology"
)

func TestParseYAML(t *testing.T) {
	testCases := []struct {
		name              string
		yaml              string
		expectErr         bool
		errContains       string
		expectedNodeCount int
		validateGraph     func(t *testing.T, g *topology.Graph)
	}{
		{
			name: "Valid: Simple case with singleton",
			yaml: `
version: 1
apps:
  api:
    depends_on: [db]
  db: {}
`,
			expectErr:         false,
			expectedNodeCount: 2,
			validateGraph: func(t *testing.T, g *topology.Graph) {
				if g.Nodes["api"].DependsOn[0] != g.Nodes["db"] {
					t.Error("api should depend on db")
				}
			},
		},
		{
			name: "Valid: Sharded with same_host_as",
			yaml: `
version: 1
shards:
  web: 2
  api: 2
apps:
  web:
    depends_on: [api]
    same_host_as: api
  api: {}
`,
			expectErr:         false,
			expectedNodeCount: 4,
			validateGraph: func(t *testing.T, g *topology.Graph) {
				if g.Nodes["web-00"].SameHostAs != g.Nodes["api-00"] {
					t.Error("web-00 should be same_host_as api-00")
				}
				if g.Nodes["web-01"].DependsOn[0] != g.Nodes["api-01"] {
					t.Error("web-01 should depend on api-01")
				}
			},
		},
		{
			name: "Valid: Many-to-one dependency",
			yaml: `
version: 1
shards:
  worker: 4
apps:
  worker:
    depends_on: [queue]
  queue: {}
`,
			expectErr:         false,
			expectedNodeCount: 5,
			validateGraph: func(t *testing.T, g *topology.Graph) {
				if g.Nodes["worker-03"].DependsOn[0] != g.Nodes["queue"] {
					t.Error("worker-03 should depend on the single queue instance")
				}
			},
		},
		{
			name: "Error: Dependency cycle",
			yaml: `
version: 1
apps:
  a:
    depends_on: [b]
  b:
    depends_on: [c]
  c:
    depends_on: [a]
`,
			expectErr:   true,
			errContains: "cycle detected: a -> b -> c -> a",
		},
		{
			name: "Error: Unknown dependency",
			yaml: `
version: 1
apps:
  a:
    depends_on: [b]
`,
			expectErr:   true,
			errContains: "depends_on target 'b' for app 'a' does not exist",
		},
		{
			name: "Error: Unknown same_host_as",
			yaml: `
version: 1
apps:
  a:
    same_host_as: b
`,
			expectErr:   true,
			errContains: "same_host_as target 'b' for app 'a' does not exist",
		},
		{
			name: "Error: Self same_host_as",
			yaml: `
version: 1
apps:
  a:
    same_host_as: a
`,
			expectErr:   true,
			errContains: "cannot have same_host_as itself",
		},
		{
			name: "Error: Mismatched shard counts for same_host_as",
			yaml: `
version: 1
shards:
  a: 2
  b: 3
apps:
  a:
    same_host_as: b
  b: {}
`,
			expectErr:   true,
			errContains: "requires matching shard counts",
		},
		{
			name: "Error: Ambiguous sharded dependency (N-to-M)",
			yaml: `
version: 1
shards:
  a: 2
  b: 3
apps:
  a:
    depends_on: [b]
  b: {}
`,
			expectErr:   true,
			errContains: "ambiguous dependency",
		},
		{
			name: "Error: YAML schema validation (unknown field)",
			yaml: `
version: 1
apps:
  a:
    unknown_field: true
`,
			expectErr:   true,
			errContains: "yaml schema validation failed",
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			graph, err := topology.ParseYAML([]byte(tc.yaml))

			if tc.expectErr {
				if err == nil {
					t.Fatal("expected an error, but got none")
				}
				if !strings.Contains(err.Error(), tc.errContains) {
					t.Errorf("expected error to contain %q, but got %q", tc.errContains, err.Error())
				}
				return
			}

			if err != nil {
				t.Fatalf("did not expect an error, but got: %v", err)
			}

			if len(graph.Nodes) != tc.expectedNodeCount {
				t.Errorf("expected %d nodes, but got %d", tc.expectedNodeCount, len(graph.Nodes))
			}

			if tc.validateGraph != nil {
				tc.validateGraph(t, graph)
			}
		})
	}
}

// END FILE: parser_test.go
