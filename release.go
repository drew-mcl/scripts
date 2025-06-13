package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"os/exec"
	"regexp"
	"sort"
	"strings"
	"time"

	"golang.org/x/mod/semver" // Using standard library module for semantic versioning
)

//
// ----------------- LOGGER SETUP -----------------
//

const (
	reset = "\033[0m"
	red   = "\033[31m"
	yel   = "\033[33m"
	gre   = "\033[32m"
	blu   = "\033[34m"
)

// colorHandler is a simple slog.Handler that adds color to log levels for console readability.
type colorHandler struct{ slog.Handler }

func (h colorHandler) Handle(ctx context.Context, r slog.Record) error {
	var color string
	switch r.Level {
	case slog.LevelError:
		color = red
	case slog.LevelWarn:
		color = yel
	case slog.LevelInfo:
		color = gre
	default: // Debug
		color = blu
	}
	fmt.Fprint(os.Stderr, color)
	err := h.Handler.Handle(ctx, r) // delegate actual formatting
	fmt.Fprint(os.Stderr, reset)
	return err
}

var logger *slog.Logger

// init initializes a structured logger for the application.
// It uses a colorized text handler for local runs and can be swapped for a JSON handler for CI.
func init() {
	// For local development, a colorful logger is nice.
	// For GitLab CI, you might swap this with:
	// logger = slog.New(slog.NewJSONHandler(os.Stderr, nil))
	base := slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{
		Level: slog.LevelInfo,
		// For cleaner logs, you can hide the source file path.
		// AddSource: false,
	})
	logger = slog.New(colorHandler{base})
}

//
// ----------------- MODELS & CONFIG -----------------
//

// Config holds all the necessary configuration derived from environment variables and arguments.
type Config struct {
	AppName         string
	ReleaseVersion  string
	NewTag          string
	ProjectID       string
	GitLabAPIToken  string
	GraphFile       string
	DependencyGraph map[string]Project
}

// Project represents the structure of a single module from our exported dependency graph.
type Project struct {
	ProjectDir   string   `json:"projectDir"`
	Dependencies []string `json:"dependencies"`
}

//
// ----------------- MAIN EXECUTION FLOW -----------------
//

func main() {
	if err := run(); err != nil {
		logger.Error("release script failed", "error", err)
		os.Exit(1)
	}
	logger.Info("release script completed successfully")
}

// run contains the core logic of our application.
func run() error {
	// --- 1. Load and Validate Configuration ---
	cfg, err := loadConfig()
	if err != nil {
		return fmt.Errorf("configuration error: %w", err)
	}
	logger.Info("configuration loaded", "app", cfg.AppName, "version", cfg.ReleaseVersion, "tag", cfg.NewTag)

	// --- 2. Find Previous Tag ---
	// We fetch tags first to ensure we have the latest information.
	if _, err := runGitCommand("fetch", "--tags"); err != nil {
		return fmt.Errorf("failed to fetch git tags: %w", err)
	}
	previousTag, err := findPreviousTag(cfg.AppName, cfg.NewTag)
	if err != nil {
		return fmt.Errorf("could not determine previous tag: %w", err)
	}
	logger.Info("found previous release tag", "previous_tag", previousTag)

	// --- 3. Determine Changed Paths from Dependency Graph ---
	changedPaths, err := findAppAndDependencyPaths(cfg)
	if err != nil {
		return err
	}
	logger.Info("determined all relevant paths from dependency graph", "count", len(changedPaths))

	// --- 4. Get Changes ---
	changelog, err := getChangelog(previousTag, "HEAD", changedPaths)
	if err != nil {
		return fmt.Errorf("could not generate changelog: %w", err)
	}
	if changelog == "" {
		logger.Warn("no changes detected for this release, aborting")
		return nil // Not an error, just nothing to release.
	}
	logger.Info("changelog generated", "content", changelog)

	// --- 5. Create and Push Git Tag ---
	if _, err := runGitCommand("tag", "-a", cfg.NewTag, "-m", fmt.Sprintf("Release %s for %s", cfg.ReleaseVersion, cfg.AppName)); err != nil {
		return fmt.Errorf("failed to create git tag %s: %w", cfg.NewTag, err)
	}
	logger.Info("successfully created local git tag", "tag", cfg.NewTag)

	if _, err := runGitCommand("push", "origin", cfg.NewTag); err != nil {
		return fmt.Errorf("failed to push git tag %s: %w", cfg.NewTag, err)
	}
	logger.Info("successfully pushed git tag to remote", "tag", cfg.NewTag)

	// --- 6. Create GitLab Release ---
	if err := createGitLabRelease(cfg, changelog); err != nil {
		return fmt.Errorf("failed to create GitLab release: %w", err)
	}

	return nil
}

//
// ----------------- CONFIG & SETUP -----------------
//

// loadConfig populates the Config struct from arguments and environment variables.
func loadConfig() (*Config, error) {
	if len(os.Args) < 2 {
		return nil, fmt.Errorf("usage: %s <app-name>", os.Args[0])
	}

	cfg := Config{
		AppName:        os.Args[1],
		ReleaseVersion: os.Getenv("RELEASE_VERSION"),
		ProjectID:      os.Getenv("CI_PROJECT_ID"),
		GitLabAPIToken: os.Getenv("GITLAB_API_TOKEN"),
		GraphFile:      "build/dependency-graph.json",
	}

	// Validate required config
	if cfg.AppName == "" {
		return nil, fmt.Errorf("app-name argument is required")
	}
	if cfg.ReleaseVersion == "" {
		return nil, fmt.Errorf("RELEASE_VERSION environment variable is not set")
	}
	if !semver.IsValid("v" + cfg.ReleaseVersion) {
		return nil, fmt.Errorf("RELEASE_VERSION '%s' is not a valid semantic version", cfg.ReleaseVersion)
	}
	if cfg.ProjectID == "" {
		return nil, fmt.Errorf("CI_PROJECT_ID environment variable is not set")
	}
	if cfg.GitLabAPIToken == "" {
		return nil, fmt.Errorf("GITLAB_API_TOKEN environment variable is not set")
	}

	cfg.NewTag = fmt.Sprintf("%s/v%s", cfg.AppName, cfg.ReleaseVersion)

	// Load the dependency graph
	graph, err := loadProjects(cfg.GraphFile)
	if err != nil {
		return nil, fmt.Errorf("could not load project graph: %w", err)
	}
	cfg.DependencyGraph = graph

	return &cfg, nil
}

// loadProjects reads and parses the dependency graph JSON file.
func loadProjects(path string) (map[string]Project, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, fmt.Errorf("failed to open dependency graph at %s: %w", path, err)
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

//
// ----------------- GIT & CHANGELOG LOGIC -----------------
//

// runGitCommand executes a git command and returns its output or an error.
func runGitCommand(args ...string) (string, error) {
	cmd := exec.Command("git", args...)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	logger.Debug("executing git command", "args", args)

	if err := cmd.Run(); err != nil {
		return "", fmt.Errorf("git command failed: %v\n%s", err, stderr.String())
	}
	return strings.TrimSpace(stdout.String()), nil
}

// findPreviousTag finds the latest semantic version tag for a specific app.
func findPreviousTag(appName, newTag string) (string, error) {
	tagPrefix := appName + "/"
	out, err := runGitCommand("tag", "-l", tagPrefix+"*")
	if err != nil {
		return "", err
	}

	tags := strings.Split(out, "\n")
	if len(tags) == 0 || (len(tags) == 1 && tags[0] == "") {
		logger.Warn("no previous tags found for app, will compare against initial commit", "app", appName)
		return getFirstCommitForPath("apps/" + appName)
	}

	var validTags []string
	for _, tag := range tags {
		// remove prefix for semver comparison
		version := strings.TrimPrefix(tag, tagPrefix)
		if semver.IsValid(version) {
			validTags = append(validTags, version)
		}
	}

	if len(validTags) == 0 {
		logger.Warn("no valid semantic version tags found, will compare against initial commit", "app", appName)
		return getFirstCommitForPath("apps/" + appName)
	}

	// Sort tags using semantic versioning rules
	sort.Slice(validTags, func(i, j int) bool {
		return semver.Compare(validTags[i], validTags[j]) < 0
	})

	// Return the latest tag, which is the last in the sorted list.
	return tagPrefix + validTags[len(validTags)-1], nil
}

// getFirstCommitForPath finds the hash of the very first commit that touched a given path.
func getFirstCommitForPath(path string) (string, error) {
	// --diff-filter=A gets the first commit that added files
	// --reverse lists commits in chronological order
	out, err := runGitCommand("log", "--reverse", "--diff-filter=A", "--pretty=format:%H", "--", path)
	if err != nil {
		return "", fmt.Errorf("could not get first commit for path %s: %w", path, err)
	}
	commits := strings.Split(out, "\n")
	if len(commits) > 0 && commits[0] != "" {
		return commits[0], nil
	}
	return "", fmt.Errorf("no commits found for path %s", path)
}

// findAppAndDependencyPaths traverses the graph to find all filesystem paths for an app and its dependencies.
func findAppAndDependencyPaths(cfg *Config) ([]string, error) {
	appGradlePath := ":apps:" + cfg.AppName

	// Use a map to avoid duplicate paths
	paths := make(map[string]bool)
	queue := []string{appGradlePath}
	processed := make(map[string]bool)

	for len(queue) > 0 {
		currentModule := queue[0]
		queue = queue[1:]

		if processed[currentModule] {
			continue
		}
		processed[currentModule] = true

		projectData, ok := cfg.DependencyGraph[currentModule]
		if !ok {
			logger.Warn("module not found in dependency graph, skipping", "module", currentModule)
			continue
		}

		paths[projectData.ProjectDir] = true
		queue = append(queue, projectData.Dependencies...)
	}

	// Convert map keys to a slice
	pathList := make([]string, 0, len(paths))
	for path := range paths {
		pathList = append(pathList, path)
	}
	return pathList, nil
}

// getChangelog generates a formatted changelog string from git commits.
func getChangelog(fromRef, toRef string, paths []string) (string, error) {
	// The format is: <short-hash> <commit-subject>
	gitLogCmd := []string{"log", "--pretty=format:* %h %s", fmt.Sprintf("%s..%s", fromRef, toRef), "--"}
	gitLogCmd = append(gitLogCmd, paths...)

	out, err := runGitCommand(gitLogCmd...)
	if err != nil {
		return "", fmt.Errorf("failed to get git log: %w", err)
	}

	// Extract Jira IDs and format the changelog
	jiraRegex := regexp.MustCompile(`([A-Z]+-[0-9]+)`)
	var changelog strings.Builder
	lines := strings.Split(out, "\n")

	for _, line := range lines {
		if line == "" {
			continue
		}
		matches := jiraRegex.FindAllString(line, -1)
		if len(matches) > 0 {
			// e.g., "* 7f4d2f8 feat(billing): implement new invoice system [BILL-123]"
			changelog.WriteString(fmt.Sprintf("%s [%s]\n", line, strings.Join(matches, ", ")))
		} else {
			changelog.WriteString(line + "\n")
		}
	}

	return changelog.String(), nil
}

//
// ----------------- GITLAB API INTEGRATION -----------------
//

// createGitLabRelease posts the new release information to the GitLab API.
func createGitLabRelease(cfg *Config, changelog string) error {
	apiURL := fmt.Sprintf("%s/api/v4/projects/%s/releases", os.Getenv("CI_SERVER_URL"), cfg.ProjectID)

	releaseTitle := fmt.Sprintf("%s %s", cfg.AppName, cfg.ReleaseVersion)

	payload := map[string]string{
		"name":        releaseTitle,
		"tag_name":    cfg.NewTag,
		"description": changelog,
	}
	body, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("failed to marshal release payload: %w", err)
	}

	req, err := http.NewRequest("POST", apiURL, bytes.NewBuffer(body))
	if err != nil {
		return fmt.Errorf("failed to create http request: %w", err)
	}

	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("PRIVATE-TOKEN", cfg.GitLabAPIToken)

	logger.Info("creating GitLab release", "url", apiURL, "title", releaseTitle)

	client := &http.Client{Timeout: 15 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("failed to send request to GitLab API: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 300 {
		respBody, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("GitLab API returned an error\nStatus: %s\nResponse: %s", resp.Status, string(respBody))
	}

	logger.Info("GitLab release created successfully", "status", resp.Status)
	return nil
}
