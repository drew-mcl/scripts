// -----------------------------------------------------------------------------
// depgraph.go
// -----------------------------------------------------------------------------
// Package depgraph provides utilities to model a directed acyclic dependency
// graph of projects and to discover which deployable applications are affected
// by changes to underlying libraries or services.
//
// It is intentionally decoupled from I/O; callers are expected to supply the
// already-parsed project metadata (e.g. from a JSON file) and, separately,
// the list of changed project names (e.g. derived from `git diff`).
//
// Example JSON the caller might parse into Project objects:
// {
//   ":admin-resource-api": {
//     "projectDir": "apps/admin-resource-api",
//     "dependencies": [":common-xyz"],
//     "deployable": true
//   },
//   ":common-xyz": {
//     "projectDir": "libs/common-xyz",
//     "dependencies": [],
//     "deployable": false
//   }
// }
package depgraph

import (
    "encoding/json"
    "errors"
    "fmt"
    "sort"
)

// Project represents the static metadata for a single Gradle/Git project.
// Name must be unique (the Gradle path or logical ID like ":common-xyz").
// ProjectDir is the on-disk directory relative to the repository root.
// Dependencies lists other project names this project consumes.
// Deployable indicates whether the project results in a deployable artifact
// (e.g. a container image or runnable service).
type Project struct {
    Name         string   `json:"-"`
    ProjectDir   string   `json:"projectDir"`
    Dependencies []string `json:"dependencies"`
    Deployable   bool     `json:"deployable"`
}

// Node enriches a Project with adjacency lists for fast traversal.
type Node struct {
    Project
    Deps       []*Node // downstream deps (this ➜ dep)
    Dependents []*Node // upstream dependents (dep ➜ this)
}

// Graph owns the in-memory representation. Safe for concurrent reads.
type Graph struct {
    nodes map[string]*Node
}

// NewGraph builds a dependency graph from a slice of projects.
func NewGraph(projects []Project) (*Graph, error) {
    g := &Graph{nodes: make(map[string]*Node, len(projects))}

    // 1. create nodes
    for _, p := range projects {
        if _, dup := g.nodes[p.Name]; dup {
            return nil, fmt.Errorf("duplicate project name: %s", p.Name)
        }
        if p.Dependencies == nil {
            p.Dependencies = make([]string, 0)
        }
        g.nodes[p.Name] = &Node{Project: p}
    }

    // 2. wire edges
    for _, n := range g.nodes {
        for _, depName := range n.Dependencies {
            depNode, ok := g.nodes[depName]
            if !ok {
                return nil, fmt.Errorf("project %s lists unknown dependency %s", n.Name, depName)
            }
            n.Deps = append(n.Deps, depNode)
            depNode.Dependents = append(depNode.Dependents, n)
        }
    }

    // 3. detect cycles early (Kahn)
    if err := g.detectCycle(); err != nil {
        return nil, err
    }
    return g, nil
}

func (g *Graph) detectCycle() error {
    inDegree := make(map[string]int, len(g.nodes))
    for name := range g.nodes {
        inDegree[name] = 0
    }
    for _, n := range g.nodes {
        for range n.Deps {
            inDegree[n.Name]++
        }
    }
    var queue []string
    for name, deg := range inDegree {
        if deg == 0 {
            queue = append(queue, name)
        }
    }
    var visited int
    for len(queue) > 0 {
        cur := queue[0]
        queue = queue[1:]
        visited++
        for _, up := range g.nodes[cur].Dependents {
            inDegree[up.Name]--
            if inDegree[up.Name] == 0 {
                queue = append(queue, up.Name)
            }
        }
    }
    if visited != len(g.nodes) {
        return errors.New("dependency graph contains at least one cycle – check for circular dependencies")
    }
    return nil
}

// AffectedDeployables returns the unique set of deployable project names that
// transitively depend on any of the changed projects.
func (g *Graph) AffectedDeployables(changed []string) ([]string, error) {
    queue := make([]*Node, 0, len(changed))
    for _, name := range changed {
        n, ok := g.nodes[name]
        if !ok {
            return nil, fmt.Errorf("changed project %s not present in graph", name)
        }
        queue = append(queue, n)
    }
    visited := make(map[string]struct{})
    affected := make(map[string]struct{})
    for len(queue) > 0 {
        cur := queue[0]
        queue = queue[1:]
        if _, seen := visited[cur.Name]; seen {
            continue
        }
        visited[cur.Name] = struct{}{}
        if cur.Deployable {
            affected[cur.Name] = struct{}{}
            continue
        }
        for _, up := range cur.Dependents {
            queue = append(queue, up)
        }
    }
    out := make([]string, 0, len(affected))
    for k := range affected {
        out = append(out, k)
    }
    sort.Strings(out)
    return out, nil
}

// Nodes returns a defensive copy of the node map.
func (g *Graph) Nodes() map[string]*Node {
    m := make(map[string]*Node, len(g.nodes))
    for k, v := range g.nodes {
        m[k] = v
    }
    return m
}

// -----------------------------------------------------------------------------
// depgraph_test.go (unit tests)
// -----------------------------------------------------------------------------
//go:build unit
// +build unit

package depgraph

import "testing"

func TestAffectedDeployables(t *testing.T) {
    projects := []Project{
        {Name: ":lib", ProjectDir: "libs/lib"},
        {Name: ":app", ProjectDir: "apps/app", Dependencies: []string{":lib"}, Deployable: true},
    }
    g, err := NewGraph(projects)
    if err != nil {
        t.Fatalf("unexpected error: %v", err)
    }
    got, err := g.AffectedDeployables([]string{":lib"})
    if err != nil {
        t.Fatalf("walk failed: %v", err)
    }
    want := []string{":app"}
    if len(got) != 1 || got[0] != want[0] {
        t.Errorf("want %v, got %v", want, got)
    }
}

func TestCycleDetection(t *testing.T) {
    projects := []Project{
        {Name: ":a", ProjectDir: "a", Dependencies: []string{":b"}},
        {Name: ":b", ProjectDir: "b", Dependencies: []string{":a"}},
    }
    if _, err := NewGraph(projects); err == nil {
        t.Fatal("expected cycle detection error, got nil")
    }
}

// -----------------------------------------------------------------------------
// integration_test.go (integration / JSON round-trip)
// -----------------------------------------------------------------------------
//go:build integration
// +build integration

package depgraph

import (
    "encoding/json"
    "testing"
)

func TestJSONRoundTrip(t *testing.T) {
    raw := `{
      ":lib":  {"projectDir":"libs/lib","dependencies":[],"deployable":false},
      ":app":  {"projectDir":"apps/app","dependencies":[":lib"],"deployable":true}
    }`
    var mm map[string]Project
    if err := json.Unmarshal([]byte(raw), &mm); err != nil {
        t.Fatalf("unmarshal: %v", err)
    }
    var projects []Project
    for name, p := range mm {
        p.Name = name
        projects = append(projects, p)
    }
    g, err := NewGraph(projects)
    if err != nil {
        t.Fatalf("graph build: %v", err)
    }
    apps, _ := g.AffectedDeployables([]string{":lib"})
    if len(apps) != 1 || apps[0] != ":app" {
        t.Errorf("integration walk failed, got %v", apps)
    }
}
