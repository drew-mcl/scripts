// -----------------------------------------------------------------------------
// depgraph.go
// -----------------------------------------------------------------------------
// Package depgraph models a directed acyclic dependency graph and answers
// “which deployable apps are affected if these projects changed?”.
// The API is intentionally pure-in-memory so callers can parse metadata and feed
// it in from any source (JSON, YAML, DB, etc.).
package depgraph

import (
    "errors"
    "fmt"
    "sort"
)

type Project struct {
    Name         string   `json:"-"`
    ProjectDir   string   `json:"projectDir"`
    Dependencies []string `json:"dependencies"`
    Deployable   bool     `json:"deployable"`
}

type Node struct {
    Project
    Deps       []*Node // this ➜ dep
    Dependents []*Node // dep  ➜ this
}

type Graph struct{ nodes map[string]*Node }

func NewGraph(ps []Project) (*Graph, error) {
    g := &Graph{nodes: make(map[string]*Node, len(ps))}
    for _, p := range ps {
        if _, dup := g.nodes[p.Name]; dup {
            return nil, fmt.Errorf("duplicate project name %s", p.Name)
        }
        if p.Dependencies == nil {
            p.Dependencies = make([]string, 0)
        }
        g.nodes[p.Name] = &Node{Project: p}
    }
    for _, n := range g.nodes {
        for _, d := range n.Dependencies {
            dep, ok := g.nodes[d]
            if !ok {
                return nil, fmt.Errorf("project %s lists unknown dependency %s", n.Name, d)
            }
            n.Deps = append(n.Deps, dep)
            dep.Dependents = append(dep.Dependents, n)
        }
    }
    if err := g.detectCycle(); err != nil {
        return nil, err
    }
    return g, nil
}

func (g *Graph) detectCycle() error {
    indeg := make(map[string]int, len(g.nodes))
    for name := range g.nodes {
        indeg[name] = 0
    }
    for _, n := range g.nodes {
        for range n.Deps {
            indeg[n.Name]++
        }
    }
    var q []string
    for n, d := range indeg {
        if d == 0 {
            q = append(q, n)
        }
    }
    var seen int
    for len(q) > 0 {
        cur := q[0]
        q = q[1:]
        seen++
        for _, up := range g.nodes[cur].Dependents {
            indeg[up.Name]--
            if indeg[up.Name] == 0 {
                q = append(q, up.Name)
            }
        }
    }
    if seen != len(g.nodes) {
        return errors.New("dependency graph contains at least one cycle")
    }
    return nil
}

func (g *Graph) AffectedDeployables(changed []string) ([]string, error) {
    var work []*Node
    for _, n := range changed {
        node, ok := g.nodes[n]
        if !ok {
            return nil, fmt.Errorf("changed project %s not present in graph", n)
        }
        work = append(work, node)
    }
    visited := make(map[string]struct{})
    affected := make(map[string]struct{})

    for len(work) > 0 {
        cur := work[0]
        work = work[1:]
        if _, seen := visited[cur.Name]; seen {
            continue
        }
        visited[cur.Name] = struct{}{}
        if cur.Deployable {
            affected[cur.Name] = struct{}{}
            continue
        }
        for _, up := range cur.Dependents {
            work = append(work, up)
        }
    }
    out := make([]string, 0, len(affected))
    for k := range affected {
        out = append(out, k)
    }
    sort.Strings(out)
    return out, nil
}

// -----------------------------------------------------------------------------
// gitdiff.go
// -----------------------------------------------------------------------------
// Package gitdiff shells out to Git to list changed files for CI flows.
package gitdiff

import (
    "bytes"
    "context"
    "fmt"
    "os/exec"
    "strings"
)

func run(ctx context.Context, dir string, args ...string) (string, error) {
    cmd := exec.CommandContext(ctx, "git", args...)
    cmd.Dir = dir
    var outBuf, errBuf bytes.Buffer
    cmd.Stdout, cmd.Stderr = &outBuf, &errBuf
    if err := cmd.Run(); err != nil {
        return "", fmt.Errorf("git %s: %v – %s", strings.Join(args, " "), err, strings.TrimSpace(errBuf.String()))
    }
    return strings.TrimSpace(outBuf.String()), nil
}

func ChangedFilesAgainstBase(ctx context.Context, repo, base string) ([]string, error) {
    o, err := run(ctx, repo, "diff", "--name-only", fmt.Sprintf("%s...HEAD", base))
    if err != nil || o == "" {
        return nil, err
    }
    return strings.Split(o, "\n"), nil
}

func ChangedFilesSinceLastCommit(ctx context.Context, repo string) ([]string, error) {
    o, err := run(ctx, repo, "diff", "--name-only", "HEAD~1")
    if err != nil || o == "" {
        return nil, err
    }
    return strings.Split(o, "\n"), nil
}

func ChangedFilesSinceLastTag(ctx context.Context, repo string) ([]string, error) {
    hash, err := run(ctx, repo, "rev-list", "--tags", "--skip=1", "-n1")
    if err != nil {
        return nil, err
    }
    var rangeSpec string
    if hash == "" {
        rangeSpec = "$(git hash-object -t tree /dev/null)" // diff from repo root if only one tag exists
    } else {
        tag, err := run(ctx, repo, "describe", "--tags", "--abbrev=0", hash)
        if err != nil {
            return nil, err
        }
        rangeSpec = fmt.Sprintf("%s..HEAD", tag)
    }
    o, err := run(ctx, repo, "diff", "--name-only", rangeSpec)
    if err != nil || o == "" {
        return nil, err
    }
    return strings.Split(o, "\n"), nil
}

// -----------------------------------------------------------------------------
// cmd/pipeline-gen/main.go
// -----------------------------------------------------------------------------
// A tiny CLI that wires the `gitdiff` and `depgraph` packages together.
// It determines which deployable projects need CI pipelines based on Git changes
// and project-dependency metadata.
package main

import (
    "context"
    "encoding/json"
    "flag"
    "log/slog"
    "os"
    "path/filepath"
    "strings"
    "time"

    "github.com/yourorg/tool/depgraph"
    "github.com/yourorg/tool/gitdiff"
)

func main() {
    var (
        repo     = flag.String("repo", ".", "path to git repo root")
        meta     = flag.String("metadata", "projects.json", "project metadata JSON file")
        mode     = flag.String("mode", "branch", "diff mode: branch|main|tag")
        baseRef  = flag.String("base-ref", "origin/main", "base ref when mode=branch")
        verbose  = flag.Bool("v", false, "verbose logging")
    )
    flag.Parse()

    // logger setup
    handler := slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelInfo})
    if *verbose {
        handler = slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelDebug})
    }
    log := slog.New(handler)

    // ------------------------------------------------------------ load metadata
    raw, err := os.ReadFile(*meta)
    if err != nil {
        log.Error("read metadata", "err", err)
        os.Exit(1)
    }
    var mm map[string]depgraph.Project
    if err := json.Unmarshal(raw, &mm); err != nil {
        log.Error("parse metadata", "err", err)
        os.Exit(1)
    }
    var projects []depgraph.Project
    for name, p := range mm {
        p.Name = name
        projects = append(projects, p)
    }
    g, err := depgraph.NewGraph(projects)
    if err != nil {
        log.Error("build graph", "err", err)
        os.Exit(1)
    }

    // ------------------------------------------------------------ git changes
    ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
    defer cancel()

    var changedFiles []string
    switch *mode {
    case "branch":
        changedFiles, err = gitdiff.ChangedFilesAgainstBase(ctx, *repo, *baseRef)
    case "main":
        changedFiles, err = gitdiff.ChangedFilesSinceLastCommit(ctx, *repo)
    case "tag":
        changedFiles, err = gitdiff.ChangedFilesSinceLastTag(ctx, *repo)
    default:
        log.Error("unknown mode", "mode", *mode)
        os.Exit(1)
    }
    if err != nil {
        log.Error("git diff", "err", err)
        os.Exit(1)
    }
    log.Debug("changed files", "count", len(changedFiles))

    // ------------------------------------------------------------ map → projects
    changedSet := map[string]struct{}{}
    for _, f := range changedFiles {
        rel := filepath.ToSlash(f)
        for _, p := range projects {
            if p.ProjectDir == "" {
                continue
            }
            if strings.HasPrefix(rel, p.ProjectDir+"/") || rel == p.ProjectDir {
                changedSet[p.Name] = struct{}{}
            }
        }
    }
    if len(changedSet) == 0 {
        log.Info("no projects matched changed files – exiting")
        return
    }
    var changedProjects []string
    for n := range changedSet {
        changedProjects = append(changedProjects, n)
    }

    // ------------------------------------------------------------ dependency walk
    impacted, err := g.AffectedDeployables(changedProjects)
    if err != nil {
        log.Error("dependency walk", "err", err)
        os.Exit(1)
    }
    if len(impacted) == 0 {
        log.Info("no deployable apps impacted – nothing to do")
        return
    }
    log.Info("deployable apps impacted", "count", len(impacted), "apps", impacted)

    // future: emit CI job YAML / JSON here
}

// -----------------------------------------------------------------------------
// Tests (unit + integration) remain unchanged from previous revision and are
// omitted here for brevity, but still live in this module so `go test ./...`
// works.
// -----------------------------------------------------------------------------