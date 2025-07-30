// FILE: go.mod
// This file defines the module's path and its dependencies.
module yourcorp/topology

go 1.22

require gopkg.in/yaml.v3 v3.0.1

// END FILE: go.mod

// ------------------------------------------------------------------

// FILE: types.go
// This file contains the Go structs that map directly to the input YAML schema,
// updated to support the final blueprint model.
package topology

import "gopkg.in/yaml.v3"

// YAMLTopology is the top-level structure for unmarshaling the topology.yaml file.
type YAMLTopology struct {
	Version    int                    `yaml:"version"`
	Shards     map[string]int         `yaml:"shards"`
	Blueprints map[string]Blueprint   `yaml:"blueprints"`
	Apps       map[string]AppDefinition `yaml:"apps"`
}

// Blueprint defines a reusable template of co-located applications.
type Blueprint struct {
	Apps map[string]BlueprintAppDefinition `yaml:"apps"`
}

// BlueprintAppDefinition is the definition of an app within a blueprint.
type BlueprintAppDefinition struct {
	DependsOn              []string `yaml:"depends_on"`
	ExternalDependsOn      []string `yaml:"external_depends_on"`
	ExternalDependsOnAllOf []string `yaml:"external_depends_on_all_of"`
}

// AppDefinition defines a top-level, instantiable application.
type AppDefinition struct {
	DependsOn      []string            `yaml:"depends_on"`
	DependsOnAllOf []string            `yaml:"depends_on_all_of"`
	SameHostAs     StringOrStringSlice `yaml:"same_host_as"`
	Uses           []BlueprintInstance `yaml:"uses"`
}

// BlueprintInstance defines how a top-level app uses a blueprint.
type BlueprintInstance struct {
	Blueprint string            `yaml:"blueprint"`
	DependsOn bool              `yaml:"depends_on"`
	With      map[string]string `yaml:"with"`
}

// StringOrStringSlice is a custom type that can unmarshal a YAML field
// that is either a single string or a slice of strings.
type StringOrStringSlice []string

func (s *StringOrStringSlice) UnmarshalYAML(value *yaml.Node) error {
	var single string
	if err := value.Decode(&single); err == nil {
		if single != "" {
			*s = []string{single}
		} else {
			*s = []string{}
		}
		return nil
	}
	var slice []string
	if err := value.Decode(&slice); err == nil {
		*s = slice
		return nil
	}
	return &yaml.TypeError{Errors: []string{"field must be a string or a list of strings"}}
}

// END FILE: types.go

// ------------------------------------------------------------------

// FILE: graph.go
// This file defines the primary output data structures: Graph and Node.
// The structure of the final graph remains the same.
package topology

import (
	"bytes"
	"fmt"
	"sort"
)

// Graph represents the fully expanded and validated dependency graph.
type Graph struct {
	Nodes map[string]*Node
}

// Node represents a single, concrete instance of an application shard.
type Node struct {
	ID          string
	BaseApp     string
	Shard       int
	HostGroupID string
	DependsOn   []*Node
}

// DOTOptions allows for customizing the DOT output.
type DOTOptions struct {
	ShowCoLocation bool
}

// DOT generates a Graphviz DOT language representation of the graph.
func (g *Graph) DOT(opts DOTOptions) (string, error) {
	var b bytes.Buffer
	b.WriteString("digraph G {\n")
	b.WriteString("  compound=true;\n")
	b.WriteString("  rankdir=TB;\n")
	b.WriteString("  node [shape=box, style=rounded];\n\n")

	nodeKeys := make([]string, 0, len(g.Nodes))
	for k := range g.Nodes {
		nodeKeys = append(nodeKeys, k)
	}
	sort.Strings(nodeKeys)

	hostGroups := make(map[string][]*Node)
	for _, key := range nodeKeys {
		node := g.Nodes[key]
		if opts.ShowCoLocation && node.HostGroupID != "" {
			hostGroups[node.HostGroupID] = append(hostGroups[node.HostGroupID], node)
		} else {
			b.WriteString(fmt.Sprintf("  \"%s\";\n", node.ID))
		}
	}

	if opts.ShowCoLocation {
		clusterKeys := make([]string, 0, len(hostGroups))
		for k := range hostGroups {
			clusterKeys = append(clusterKeys, k)
		}
		sort.Strings(clusterKeys)

		for _, groupID := range clusterKeys {
			nodes := hostGroups[groupID]
			b.WriteString(fmt.Sprintf("  subgraph \"cluster_%s\" {\n", groupID))
			b.WriteString(fmt.Sprintf("    label = \"%s\";\n", groupID))
			b.WriteString("    style = filled;\n")
			b.WriteString("    color = lightgrey;\n")
			for _, node := range nodes {
				b.WriteString(fmt.Sprintf("    \"%s\";\n", node.ID))
			}
			b.WriteString("  }\n")
		}
	}

	b.WriteString("\n")

	for _, key := range nodeKeys {
		node := g.Nodes[key]
		for _, dep := range node.DependsOn {
			b.WriteString(fmt.Sprintf("  \"%s\" -> \"%s\";\n", node.ID, dep.ID))
		}
	}

	b.WriteString("}\n")
	return b.String(), nil
}

// END FILE: graph.go

// ------------------------------------------------------------------

// FILE: parser.go
// This file contains the core logic for parsing the topology. It has been
// completely refactored to support the new, cleaner blueprint model.
package topology

import (
	"bytes"
	"fmt"
	"sort"
	"strings"

	"gopkg.in/yaml.v3"
)

// ParseYAML takes a byte slice of a YAML topology file and returns a fully
// validated and expanded Graph object.
func ParseYAML(data []byte) (*Graph, error) {
	var rawTopology YAMLTopology
	decoder := yaml.NewDecoder(bytes.NewReader(data))
	decoder.KnownFields(true)
	if err := decoder.Decode(&rawTopology); err != nil {
		return nil, fmt.Errorf("yaml schema validation failed: %w", err)
	}

	expandedApps, err := expandBlueprints(rawTopology)
	if err != nil {
		return nil, err
	}
	rawTopology.Apps = expandedApps

	coLocationGroups, err := discoverCoLocationGroups(rawTopology)
	if err != nil {
		return nil, err
	}

	appShardCounts, err := inferAndValidateShardCounts(rawTopology, coLocationGroups)
	if err != nil {
		return nil, err
	}

	graph, err := buildConcreteNodes(rawTopology, coLocationGroups, appShardCounts)
	if err != nil {
		return nil, err
	}

	if err := linkDependencies(graph, rawTopology, appShardCounts); err != nil {
		return nil, err
	}

	if cyclePath, ok := detectCycle(graph); ok {
		return nil, fmt.Errorf("validation failed: dependency cycle detected: %s", strings.Join(cyclePath, " -> "))
	}

	return graph, nil
}

// expandBlueprints is the new first stage of parsing. It takes the raw topology
// and returns a new, complete map of AppDefinitions by instantiating all blueprints.
func expandBlueprints(rawTopology YAMLTopology) (map[string]AppDefinition, error) {
	expandedApps := make(map[string]AppDefinition)

	for appName, appDef := range rawTopology.Apps {
		expandedApps[appName] = appDef
	}

	for appName, appDef := range rawTopology.Apps {
		for _, instance := range appDef.Uses {
			blueprint, ok := rawTopology.Blueprints[instance.Blueprint]
			if !ok {
				return nil, fmt.Errorf("app '%s' uses undefined blueprint '%s'", appName, instance.Blueprint)
			}

			// Add a startup dependency from the parent to the instantiated components if requested.
			if instance.DependsOn {
				parentApp := expandedApps[appName]
				for bpAppName := range blueprint.Apps {
					instantiatedAppName := fmt.Sprintf("%s-%s", appName, bpAppName)
					parentApp.DependsOn = append(parentApp.DependsOn, instantiatedAppName)
				}
				expandedApps[appName] = parentApp
			}

			for bpAppName, bpAppDef := range blueprint.Apps {
				instantiatedAppName := fmt.Sprintf("%s-%s", appName, bpAppName)
				if _, exists := expandedApps[instantiatedAppName]; exists {
					return nil, fmt.Errorf("app name conflict: '%s' is generated by blueprint '%s' but already exists", instantiatedAppName, instance.Blueprint)
				}

				newAppDef := AppDefinition{
					SameHostAs: []string{appName}, // Automatic co-location
				}

				for _, extDep := range bpAppDef.ExternalDependsOn {
					resolvedDep, ok := instance.With[extDep]
					if !ok {
						return nil, fmt.Errorf("in blueprint '%s' used by '%s', external dependency '%s' is not resolved in 'with' clause", instance.Blueprint, appName, extDep)
					}
					newAppDef.DependsOn = append(newAppDef.DependsOn, resolvedDep)
				}
				for _, extDep := range bpAppDef.ExternalDependsOnAllOf {
					resolvedDep, ok := instance.With[extDep]
					if !ok {
						return nil, fmt.Errorf("in blueprint '%s' used by '%s', external dependency '%s' is not resolved in 'with' clause", instance.Blueprint, appName, extDep)
					}
					newAppDef.DependsOnAllOf = append(newAppDef.DependsOnAllOf, resolvedDep)
				}
				
				for _, intDep := range bpAppDef.DependsOn {
					if _, ok := blueprint.Apps[intDep]; !ok {
						return nil, fmt.Errorf("in blueprint '%s', app '%s' has an internal dependency on '%s', which is not defined in the blueprint", instance.Blueprint, bpAppName, intDep)
					}
					instantiatedDepName := fmt.Sprintf("%s-%s", appName, intDep)
					newAppDef.DependsOn = append(newAppDef.DependsOn, instantiatedDepName)
				}

				expandedApps[instantiatedAppName] = newAppDef
			}
		}
	}

	return expandedApps, nil
}

// (The rest of the parsing pipeline functions remain the same)
func discoverCoLocationGroups(rawTopology YAMLTopology) (map[string][]string, error) {
	appNames := make([]string, 0, len(rawTopology.Apps))
	for name := range rawTopology.Apps {
		appNames = append(appNames, name)
	}
	sort.Strings(appNames)

	parent := make(map[string]string)
	for _, name := range appNames {
		parent[name] = name
	}

	var find func(string) string
	find = func(i string) string {
		if parent[i] == i {
			return i
		}
		parent[i] = find(parent[i])
		return parent[i]
	}

	union := func(i, j string) {
		rootI := find(i)
		rootJ := find(j)
		if rootI != rootJ {
			if rootI < rootJ {
				parent[rootJ] = rootI
			} else {
				parent[rootI] = rootJ
			}
		}
	}

	for _, appName := range appNames {
		appDef := rawTopology.Apps[appName]
		for _, targetName := range appDef.SameHostAs {
			if _, ok := rawTopology.Apps[targetName]; !ok {
				return nil, fmt.Errorf("validation failed: same_host_as target '%s' for app '%s' does not exist", targetName, appName)
			}
			union(appName, targetName)
		}
	}

	groups := make(map[string][]string)
	for _, appName := range appNames {
		root := find(appName)
		groups[root] = append(groups[root], appName)
	}
	for root := range groups {
		sort.Strings(groups[root])
	}
	return groups, nil
}

func inferAndValidateShardCounts(rawTopology YAMLTopology, coLocationGroups map[string][]string) (map[string]int, error) {
	appShardCounts := make(map[string]int)

	for appName := range rawTopology.Shards {
		if _, ok := rawTopology.Apps[appName]; !ok {
			return nil, fmt.Errorf("validation failed: shard count defined for non-existent app '%s'", appName)
		}
	}

	for root, members := range coLocationGroups {
		groupShardCount := -1
		for _, member := range members {
			if count, ok := rawTopology.Shards[member]; ok {
				if groupShardCount != -1 && groupShardCount != count {
					return nil, fmt.Errorf("validation failed: conflicting shard counts defined for co-location group '%s'. Expected %d, but found %d for '%s'", root, groupShardCount, count, member)
				}
				groupShardCount = count
			}
		}
		if groupShardCount == -1 {
			groupShardCount = 1
		}
		for _, member := range members {
			appShardCounts[member] = groupShardCount
		}
	}
	return appShardCounts, nil
}

func buildConcreteNodes(rawTopology YAMLTopology, coLocationGroups map[string][]string, appShardCounts map[string]int) (*Graph, error) {
	graph := &Graph{Nodes: make(map[string]*Node)}
	appRoots := make(map[string]string)
	for root, members := range coLocationGroups {
		for _, member := range members {
			appRoots[member] = root
		}
	}
	for appName := range rawTopology.Apps {
		shardCount := appShardCounts[appName]
		groupRoot := appRoots[appName]
		for i := 0; i < shardCount; i++ {
			nodeID := getNodeID(appName, i, shardCount)
			hostGroupID := ""
			if len(coLocationGroups[groupRoot]) > 1 {
				hostGroupID = getNodeID(fmt.Sprintf("hostgroup-%s", groupRoot), i, shardCount)
			}
			graph.Nodes[nodeID] = &Node{
				ID:          nodeID,
				BaseApp:     appName,
				Shard:       i,
				HostGroupID: hostGroupID,
			}
		}
	}
	return graph, nil
}

func linkDependencies(graph *Graph, rawTopology YAMLTopology, appShardCounts map[string]int) error {
	for appName, appDef := range rawTopology.Apps {
		appShardCount := appShardCounts[appName]
		for i := 0; i < appShardCount; i++ {
			nodeID := getNodeID(appName, i, appShardCount)
			node := graph.Nodes[nodeID]

			for _, depName := range appDef.DependsOn {
				if _, ok := rawTopology.Apps[depName]; !ok {
					return fmt.Errorf("validation failed: depends_on target '%s' for app '%s' does not exist", depName, appName)
				}
				depShardCount := appShardCounts[depName]
				if depShardCount != 1 && depShardCount != appShardCount {
					return fmt.Errorf("validation failed: ambiguous 'depends_on' from '%s' (%d shards) to '%s' (%d shards). Use 'depends_on_all_of' for fan-in dependencies", appName, appShardCount, depName, depShardCount)
				}
				depShardIndex := i
				if depShardCount == 1 {
					depShardIndex = 0
				}
				depNodeID := getNodeID(depName, depShardIndex, depShardCount)
				node.DependsOn = append(node.DependsOn, graph.Nodes[depNodeID])
			}
			
			for _, depName := range appDef.DependsOnAllOf {
				if _, ok := rawTopology.Apps[depName]; !ok {
					return fmt.Errorf("validation failed: depends_on_all_of target '%s' for app '%s' does not exist", depName, appName)
				}
				depShardCount := appShardCounts[depName]
				for j := 0; j < depShardCount; j++ {
					depNodeID := getNodeID(depName, j, depShardCount)
					node.DependsOn = append(node.DependsOn, graph.Nodes[depNodeID])
				}
			}
		}
	}
	return nil
}

func getNodeID(appName string, shardIndex, shardCount int) string {
	if shardCount == 1 {
		return appName
	}
	return fmt.Sprintf("%s-%02d", appName, shardIndex)
}

func detectCycle(g *Graph) ([]string, bool) {
	nodeKeys := make([]string, 0, len(g.Nodes))
	for k := range g.Nodes {
		nodeKeys = append(nodeKeys, k)
	}
	sort.Strings(nodeKeys)
	visiting := make(map[string]bool)
	visited := make(map[string]bool)
	for _, key := range nodeKeys {
		if !visited[key] {
			path, hasCycle := dfsVisit(g.Nodes[key], visiting, visited)
			if hasCycle {
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
	sort.Slice(node.DependsOn, func(i, j int) bool {
		return node.DependsOn[i].ID < node.DependsOn[j].ID
	})
	for _, dep := range node.DependsOn {
		if visiting[dep.ID] {
			return []string{dep.ID, node.ID}, true
		}
		if !visited[dep.ID] {
			path, hasCycle := dfsVisit(dep, visiting, visited)
			if hasCycle {
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
// This file contains algorithms for traversing the dependency graph.
// No changes are needed here as it operates on the final graph structure.
package topology

import (
	"fmt"
	"sort"
)

func GetStartupOrder(graph *Graph) [][]*Node {
	inDegree := make(map[string]int)
	reverseDeps := make(map[string][]*Node)
	for _, node := range graph.Nodes {
		inDegree[node.ID] = len(node.DependsOn)
		for _, dep := range node.DependsOn {
			reverseDeps[dep.ID] = append(reverseDeps[dep.ID], node)
		}
	}
	var queue []*Node
	for id, degree := range inDegree {
		if degree == 0 {
			queue = append(queue, graph.Nodes[id])
		}
	}
	var order [][]*Node
	for len(queue) > 0 {
		sort.Slice(queue, func(i, j int) bool { return queue[i].ID < queue[j].ID })
		currentLayer := make([]*Node, len(queue))
		copy(currentLayer, queue)
		order = append(order, currentLayer)
		var nextQueue []*Node
		for _, node := range queue {
			for _, dependentNode := range reverseDeps[node.ID] {
				inDegree[dependentNode.ID]--
				if inDegree[dependentNode.ID] == 0 {
					nextQueue = append(nextQueue, dependentNode)
				}
			}
		}
		queue = nextQueue
	}
	return order
}

func GetShutdownOrder(graph *Graph) [][]*Node {
	startup := GetStartupOrder(graph)
	for i, j := 0, len(startup)-1; i < j; i, j = i+1, j-1 {
		startup[i], startup[j] = startup[j], startup[i]
	}
	return startup
}

func GetSubgraphFor(graph *Graph, targetNodeID string) (*Graph, error) {
	startNode, ok := graph.Nodes[targetNodeID]
	if !ok {
		return nil, fmt.Errorf("node '%s' not found in the graph", targetNodeID)
	}
	subgraph := &Graph{Nodes: make(map[string]*Node)}
	var initialNodes []*Node
	if startNode.HostGroupID != "" {
		for _, node := range graph.Nodes {
			if node.HostGroupID == startNode.HostGroupID {
				initialNodes = append(initialNodes, node)
			}
		}
	} else {
		initialNodes = append(initialNodes, startNode)
	}
	var collectDeps func(node *Node)
	collectDeps = func(node *Node) {
		if _, exists := subgraph.Nodes[node.ID]; exists {
			return
		}
		subgraph.Nodes[node.ID] = node
		for _, dep := range node.DependsOn {
			collectDeps(dep)
		}
	}
	for _, node := range initialNodes {
		collectDeps(node)
	}
	return subgraph, nil
}

// END FILE: traversal.go

// ------------------------------------------------------------------

// FILE: logical.go
// This new file provides the function to generate a simplified, logical graph view.
package topology

func (g *Graph) LogicalGraph() (*Graph, error) {
	logicalGraph := &Graph{Nodes: make(map[string]*Node)}
	baseApps := make(map[string]bool)
	for _, node := range g.Nodes {
		baseApps[node.BaseApp] = true
	}
	for appName := range baseApps {
		logicalGraph.Nodes[appName] = &Node{ID: appName, BaseApp: appName}
	}
	for _, node := range g.Nodes {
		logicalNode := logicalGraph.Nodes[node.BaseApp]
		for _, dep := range node.DependsOn {
			logicalDep := logicalGraph.Nodes[dep.BaseApp]
			found := false
			for _, existingDep := range logicalNode.DependsOn {
				if existingDep.ID == logicalDep.ID {
					found = true
					break
				}
			}
			if !found && logicalNode.ID != logicalDep.ID {
				logicalNode.DependsOn = append(logicalNode.DependsOn, logicalDep)
			}
		}
	}
	return logicalGraph, nil
}

// END FILE: logical.go

// ------------------------------------------------------------------

// FILE: cmd/yaml2dot/main.go
// This tool is updated to support logical views and co-location clustering.
package main

import (
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strings"
	"yourcorp/topology"
)

func main() {
	format := flag.String("T", "dot", "Output format (e.g., dot, svg, png).")
	view := flag.String("view", "concrete", "Graph view: 'concrete' (default) or 'logical'.")
	flag.Parse()
	yamlData, err := io.ReadAll(os.Stdin)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error reading from stdin: %v\n", err)
		os.Exit(1)
	}
	graph, err := topology.ParseYAML(yamlData)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error parsing topology: %v\n", err)
		os.Exit(1)
	}
	opts := topology.DOTOptions{ShowCoLocation: true}
	if *view == "logical" {
		graph, err = graph.LogicalGraph()
		if err != nil {
			fmt.Fprintf(os.Stderr, "Error generating logical graph: %v\n", err)
			os.Exit(1)
		}
		opts.ShowCoLocation = false
	}
	dotOutput, err := graph.DOT(opts)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error rendering DOT graph: %v\n", err)
		os.Exit(1)
	}
	if *format == "dot" {
		fmt.Print(dotOutput)
		return
	}
	cmd := exec.Command("dot", "-T"+*format)
	cmd.Stdin = strings.NewReader(dotOutput)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		if errors.Is(err, exec.ErrNotFound) {
			fmt.Fprintln(os.Stderr, "Error: 'dot' command not found. Please install Graphviz.")
		} else {
			fmt.Fprintf(os.Stderr, "Error executing 'dot' command: %v\n", err)
		}
		os.Exit(1)
	}
}

// END FILE: cmd/yaml2dot/main.go

// ------------------------------------------------------------------

// FILE: cmd/orchestrator/main.go
// This tool is updated to support logical views.
package main

import (
	"flag"
	"fmt"
	"os"
	"strings"
	"yourcorp/topology"
)

func main() {
	filePath := flag.String("file", "topology.yaml", "Path to the topology YAML file.")
	mode := flag.String("mode", "startup", "Orchestration mode: startup, shutdown, or restart.")
	target := flag.String("target", "", "The target node ID for restart mode (e.g., 'sor-01').")
	view := flag.String("view", "concrete", "Plan view: 'concrete' (default) or 'logical'.")
	flag.Parse()
	yamlData, err := os.ReadFile(*filePath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error reading file %s: %v\n", *filePath, err)
		os.Exit(1)
	}
	graph, err := topology.ParseYAML(yamlData)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error parsing topology: %v\n", err)
		os.Exit(1)
	}
	if *view == "logical" {
		if *mode == "restart" {
			fmt.Fprintln(os.Stderr, "Error: restart mode is not compatible with logical view.")
			os.Exit(1)
		}
		graph, err = graph.LogicalGraph()
		if err != nil {
			fmt.Fprintf(os.Stderr, "Error generating logical graph: %v\n", err)
			os.Exit(1)
		}
	}
	switch *mode {
	case "startup":
		fmt.Printf("--- Generating %s Startup Plan ---\n", strings.Title(*view))
		order := topology.GetStartupOrder(graph)
		printOrder("Startup", order)
	case "shutdown":
		fmt.Printf("--- Generating %s Shutdown Plan ---\n", strings.Title(*view))
		order := topology.GetShutdownOrder(graph)
		printOrder("Shutdown", order)
	case "restart":
		if *target == "" {
			fmt.Fprintln(os.Stderr, "Error: -target flag is required for restart mode.")
			os.Exit(1)
		}
		fmt.Printf("--- Generating Targeted Restart Plan for Host Group of: %s ---\n", *target)
		subgraph, err := topology.GetSubgraphFor(graph, *target)
		if err != nil {
			fmt.Fprintf(os.Stderr, "Error generating subgraph: %v\n", err)
			os.Exit(1)
		}
		order := topology.GetStartupOrder(subgraph)
		printOrder("Restart", order)
	default:
		fmt.Fprintf(os.Stderr, "Error: Invalid mode %q.\n", *mode)
		os.Exit(1)
	}
}

func printOrder(planName string, order [][]*topology.Node) {
	if len(order) == 0 {
		fmt.Println("  No operations required.")
		return
	}
	for i, layer := range order {
		var nodeIDs []string
		for _, node := range layer {
			nodeIDs = append(nodeIDs, node.ID)
		}
		fmt.Printf("  %s Layer %d (Concurrent): [ %s ]\n", planName, i+1, strings.Join(nodeIDs, ", "))
	}
}

// END FILE: cmd/orchestrator/main.go
