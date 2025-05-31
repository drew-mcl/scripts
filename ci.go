package main

import (
	"encoding/json"
	"fmt"
	"io/ioutil"
	"os"
	"strings"
)

// Represents our dependency graph data
type Project struct {
	ProjectDir   string   `json:"projectDir"`
	Dependencies []string `json:"dependencies"`
}

func main() {
	// --- 1. Get Inputs ---
	changedFilesArg := os.Args[1] // Assume space-separated list of files
	graphFile := "build/dependency-graph.json"

	// --- 2. Load and Parse the Graph ---
	bytes, _ := ioutil.ReadFile(graphFile)
	var projects map[string]Project
	json.Unmarshal(bytes, &projects)

	// --- 3. Build the REVERSE Dependency Graph ---
	// Maps a module to all the modules that depend on it.
	reverseGraph := make(map[string][]string)
	for path, projectData := range projects {
		for _, dep := range projectData.Dependencies {
			reverseGraph[dep] = append(reverseGraph[dep], path)
		}
	}

	// --- 4. Identify Initial Set of Changed Modules ---
	changedModules := make(map[string]bool)
	changedFiles := strings.Split(changedFilesArg, " ")

	if stringInSlice("versions.toml", changedFiles) {
		fmt.Fprintln(os.Stderr, "versions.toml changed, triggering all applications.")
		// In a real scenario, you'd have a list of all "deployable" apps
		// and add them all to changedModules here.
		changedModules[":refdata"] = true // Example
		// ... add all other apps
	} else {
		for _, file := range changedFiles {
			for path, projectData := range projects {
				if strings.HasPrefix(file, projectData.ProjectDir) {
					fmt.Fprintf(os.Stderr, "File '%s' belongs to module '%s'\n", file, path)
					changedModules[path] = true
					break
				}
			}
		}
	}

	// --- 5. Traverse the Graph to Find All Affected Apps ---
	affectedApps := make(map[string]bool)
	queue := make([]string, 0, len(changedModules))
	for mod := range changedModules {
		queue = append(queue, mod)
	}

	for len(queue) > 0 {
		currentModule := queue[0]
		queue = queue[1:]

		if affectedApps[currentModule] {
			continue // Already processed
		}
		affectedApps[currentModule] = true
		fmt.Fprintf(os.Stderr, "Module '%s' is affected. Looking for dependents.\n", currentModule)

		// Find everything that depends on the current module and add it to the queue
		dependents := reverseGraph[currentModule]
		if len(dependents) > 0 {
			fmt.Fprintf(os.Stderr, "  > Found dependents: %v\n", dependents)
			queue = append(queue, dependents...)
		}
	}

	// --- 6. Generate the Final Pipeline YAML ---
	fmt.Println("# Dynamically generated pipeline")
	// This is where you filter for only "deployable" apps like ":refdata"
	// and generate their trigger blocks.
	if affectedApps[":refdata"] {
		fmt.Println(`
trigger-refdata:
  trigger:
    project: 'your-group/refdata-app'
    branch: 'main'
`)
	}
	// ... add other `if affectedApps[":some-other-app"]` blocks
}

func stringInSlice(a string, list []string) bool {
	for _, b := range list {
		if b == a {
			return true
		}
	}
	return false
}
