package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
)

const (
	reset = "\033[0m"
	red   = "\033[31m"
	yel   = "\033[33m"
	gre   = "\033[32m"
	blu   = "\033[34m"
)

type colorHandler struct{ slog.Handler }

func (h colorHandler) Handle(ctx context.Context, r slog.Record) error {
	var color string
	switch {
	case r.Level >= slog.LevelError:
		color = red
	case r.Level >= slog.LevelWarn:
		color = yel
	case r.Level >= slog.LevelInfo:
		color = gre
	default: // debug / trace
		color = blu
	}
	fmt.Fprint(os.Stderr, color)
	err := h.Handler.Handle(ctx, r) // delegate actual formatting
	fmt.Fprint(os.Stderr, reset)
	return err
}

func init() {
	base := slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelInfo})
	logger = slog.New(colorHandler{base})
}

// Logger will be our structured logger.
var logger *slog.Logger

func init() {
	// Initialize a structured JSON logger that writes to stderr.
	// This is great for GitLab CI logs, which can parse structured data.
	logger = slog.New(slog.NewJSONHandler(os.Stderr, nil))
}

// Project represents the structure of a single Gradle module from our exported graph.
type Project struct {
	ProjectDir   string   `json:"projectDir"`
	Dependencies []string `json:"dependencies"`
}

func main() {
	// The main function now focuses on high-level flow and error handling.
	if err := run(); err != nil {
		logger.Error("pipeline generator failed", "error", err)
		os.Exit(1)
	}
	logger.Info("pipeline generation completed successfully")
}

// run contains the core logic of our application.
func run() error {
	// --- 1. Get Inputs & Validate ---
	if len(os.Args) < 2 {
		return fmt.Errorf("usage: %s <space-separated-changed-files>", os.Args[0])
	}
	changedFilesArg := os.Args[1]
	graphFile := "build/dependency-graph.json"
	appsDir := "apps" // All deployable apps live under this directory.

	logger.Info("starting pipeline analysis",
		"changed_files", changedFilesArg,
		"graph_file", graphFile,
		"apps_dir", appsDir,
	)

	// --- 2. Load and Parse the Dependency Graph ---
	projects, err := loadProjects(graphFile)
	if err != nil {
		return fmt.Errorf("could not load project graph: %w", err)
	}

	// --- 3. Dynamically Discover Deployable Applications ---
	deployableApps, err := findDeployableApps(appsDir, projects)
	if err != nil {
		return fmt.Errorf("could not discover deployable apps: %w", err)
	}
	logger.Info("discovered deployable applications", "apps", deployableApps)

	// --- 4. Build the Reverse Dependency Graph for efficient lookup ---
	reverseGraph := buildReverseGraph(projects)

	// --- 5. Identify Initial Set of Changed Modules ---
	changedModules, err := findChangedModules(strings.Split(changedFilesArg, " "), projects, deployableApps)
	if err != nil {
		return fmt.Errorf("could not determine changed modules: %w", err)
	}

	// --- 6. Traverse the Graph to Find All Affected Apps ---
	affectedApps := findAffectedApps(changedModules, reverseGraph, deployableApps)
	logger.Info("analysis complete", "affected_apps", affectedApps)

	// --- 7. Generate the Final Pipeline YAML ---
	if err := generatePipelineYAML(os.Stdout, affectedApps); err != nil {
		return fmt.Errorf("could not generate pipeline YAML: %w", err)
	}

	return nil
}

// loadProjects reads and parses the dependency graph JSON file.
func loadProjects(path string) (map[string]Project, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()

	bytes, err := io.ReadAll(file)
	if err != nil {
		return nil, err
	}

	var projects map[string]Project
	if err := json.Unmarshal(bytes, &projects); err != nil {
		return nil, fmt.Errorf("error parsing JSON from %s: %w", path, err)
	}
	return projects, nil
}

// findDeployableApps scans the 'apps/' directory to find all valid application modules.
func findDeployableApps(appsDir string, projects map[string]Project) (map[string]bool, error) {
	apps := make(map[string]bool)
	entries, err := os.ReadDir(appsDir)
	if err != nil {
		// If the apps directory doesn't exist, it's not an error, just means no apps.
		if os.IsNotExist(err) {
			logger.Warn("apps directory not found, assuming no deployable applications", "path", appsDir)
			return apps, nil
		}
		return nil, err
	}

	for _, entry := range entries {
		if entry.IsDir() {
			// Convert directory name to Gradle project path, e.g., "refdata" -> ":apps:refdata"
			appName := entry.Name()
			projectPath := fmt.Sprintf(":%s:%s", filepath.Base(appsDir), appName)

			// Verify this directory corresponds to a known Gradle project
			if _, ok := projects[projectPath]; ok {
				apps[projectPath] = true
			} else {
				logger.Warn("directory in apps/ does not match any known Gradle project", "directory", appName, "expected_project_path", projectPath)
			}
		}
	}
	return apps, nil
}

// buildReverseGraph creates a map for quick lookups of which projects depend on a given module.
func buildReverseGraph(projects map[string]Project) map[string][]string {
	reverseGraph := make(map[string][]string)
	for path, projectData := range projects {
		for _, dep := range projectData.Dependencies {
			reverseGraph[dep] = append(reverseGraph[dep], path)
		}
	}
	return reverseGraph
}

// findChangedModules determines the initial set of impacted modules from the list of changed files.
func findChangedModules(changedFiles []string, projects map[string]Project, deployableApps map[string]bool) (map[string]bool, error) {
	changedModules := make(map[string]bool)

	// Handle the special case for a shared version catalog
	if stringInSlice("versions.toml", changedFiles) {
		logger.Info("'versions.toml' changed, triggering all deployable applications.")
		return deployableApps, nil
	}

	for _, file := range changedFiles {
		// Find which project this file belongs to.
		// We iterate in reverse to find the most specific path match first, e.g. "apps/a/b" before "apps/a".
		// Note: A more robust solution might use a trie, but this is fine for most projects.
		var bestMatch string
		for projectPath, projectData := range projects {
			if strings.HasPrefix(file, projectData.ProjectDir) && len(projectData.ProjectDir) > len(bestMatch) {
				bestMatch = projectPath
			}
		}
		if bestMatch != "" {
			logger.Info("file change detected", "file", file, "module", bestMatch)
			changedModules[bestMatch] = true
		}
	}
	return changedModules, nil
}

// findAffectedApps traverses the reverse dependency graph to find all upstream applications that are affected.
// findAffectedApps traverses the reverse dependency graph to find all upstream applications that are affected.
func findAffectedApps(initialModules map[string]bool, reverseGraph map[string][]string, deployableApps map[string]bool) []string {
	// The set of all modules we determine are affected.
	affectedSet := make(map[string]bool)
	// The queue for our breadth-first search through the dependency graph.
	queue := make([]string, 0, len(initialModules))

	// **THIS IS THE CORRECTED PART**
	// We iterate over the keys of the initialModules map to populate our queue.
	for module := range initialModules {
		queue = append(queue, module)
	}

	for len(queue) > 0 {
		// Dequeue the next module to process.
		currentModule := queue[0]
		queue = queue[1:]

		if affectedSet[currentModule] {
			continue // Already processed, skip.
		}

		// Mark this module as affected and log our progress.
		affectedSet[currentModule] = true
		logger.Debug("traversing dependency", "module", currentModule)

		// Find everything that depends on the current module...
		if dependents, ok := reverseGraph[currentModule]; ok {
			logger.Debug("found dependents", "module", currentModule, "dependents", dependents)
			// ...and add them to the queue to be processed.
			queue = append(queue, dependents...)
		}
	}

	// Filter the final set to only include actual deployable applications.
	finalApps := make([]string, 0)
	for app := range affectedSet {
		if deployableApps[app] {
			finalApps = append(finalApps, app)
		}
	}

	return finalApps
}

// generatePipelineYAML writes the final GitLab CI YAML to the provided writer.
func generatePipelineYAML(w io.Writer, affectedApps []string) error {
	if _, err := fmt.Fprintln(w, "# This pipeline was dynamically generated by the pipeline-generator tool."); err != nil {
		return err
	}

	if len(affectedApps) == 0 {
		logger.Info("no applications affected, generating an empty pipeline.")
		return nil
	}

	for _, appPath := range affectedApps {
		// Convert Gradle path ":apps:refdata" to just "refdata"
		appName := strings.TrimPrefix(appPath, ":apps:")
		// Dynamically create the trigger job name and include path
		jobName := fmt.Sprintf("trigger:%s", appName)
		includePath := fmt.Sprintf(".gitlab/%s.yml", appName)

		// Using a multi-line string literal for clarity
		jobYAML := fmt.Sprintf(`
%s:
  stage: downstream-pipelines # Assumes you have this stage in parent .gitlab-ci.yml
  trigger:
    include:
      - project: '%s' # GitLab predefined variable for the current project
        ref: '%s'     # GitLab predefined variable for the current branch/ref
        file: '%s'
`, jobName, os.Getenv("CI_PROJECT_PATH"), os.Getenv("CI_COMMIT_REF_NAME"), includePath)

		if _, err := fmt.Fprint(w, jobYAML); err != nil {
			return err
		}
	}
	return nil
}

// stringInSlice is a simple helper function.
func stringInSlice(a string, list []string) bool {
	for _, b := range list {
		if a == b {
			return true
		}
	}
	return false
}
