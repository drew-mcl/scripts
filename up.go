package updater

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"time"

	"github.com/im-kulikov/go-updater"
	"github.com/im-kulikov/go-updater/provider"
	"github.com/im-kulikov/go-updater/source"
	"golang.org/x/mod/semver"
)

// Custom errors to signal the type of version change to the caller.
var (
	ErrMajorChange = errors.New("incompatible major version change")
	ErrMinorChange = errors.New("new features available in minor version change")
	ErrNoUpdate    = errors.New("no new version available")
)

// Result holds the outcome of an update check.
type Result struct {
	// The latest release found.
	LatestRelease updater.Release
	// The specific type of change (ErrMajorChange, ErrMinorChange, etc.).
	ChangeType error
}

// CheckForUpdates encapsulates the core update-checking logic.
// It returns a Result containing the latest release and the type of change.
func CheckForUpdates(currentVersion, gitlabSlug string) (*Result, error) {
	slog.Debug("Entering raw update check", "currentVersion", currentVersion, "repo", gitlabSlug)
	if !semver.IsValid(currentVersion) {
		return nil, fmt.Errorf("current version %q is not a valid semantic version", currentVersion)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()

	// Initialize the updater from the go-updater library
	upd, err := updater.New(ctx, updater.Params{
		Version:  currentVersion,
		Provider: provider.NewGitlab(ctx, gitlabSlug, provider.GitlabOptions{
			// Token can be passed in or loaded from env here for more abstraction
		}),
		Source: source.NewGoInstall(), // This is needed for the updater's internal logic
	})
	if err != nil {
		return nil, fmt.Errorf("failed to initialize updater: %w", err)
	}

	// Check for the latest release without performing the update yet.
	latest, err := upd.Check()
	if err != nil {
		return nil, fmt.Errorf("failed to check for new release: %w", err)
	}

	// Case 1: No new release found.
	if latest == nil {
		slog.Debug("No new release found by the updater library.")
		return &Result{ChangeType: ErrNoUpdate}, nil
	}

	latestVersion := latest.Version()
	slog.Debug("Found new release", "latestVersion", latestVersion)
	if !semver.IsValid(latestVersion) {
		return nil, fmt.Errorf("latest version %q from release is not a valid semantic version", latestVersion)
	}

	// Case 2: A new release is found, now determine the type of change.
	result := &Result{
		LatestRelease: latest,
	}

	majorCurrent := semver.Major(currentVersion)
	majorLatest := semver.Major(latestVersion)

	if majorCurrent != majorLatest {
		slog.Debug("Detected major version change.", "current", majorCurrent, "latest", majorLatest)
		result.ChangeType = ErrMajorChange
	} else if semver.Compare(majorCurrent, majorLatest) == 0 && semver.Compare(currentVersion, latestVersion) < 0 {
		// Since majors are the same, any higher version is either minor or patch.
		// We can consider any non-major bump a "minor" change for warning purposes.
		slog.Debug("Detected minor or patch version change.")
		result.ChangeType = ErrMinorChange
	}

	return result, nil
}

// PerformUpdate executes the actual update process.
func PerformUpdate(release updater.Release) error {
	slog.Debug("Performing update", "version", release.Version())
	if err := release.Update(); err != nil {
		return fmt.Errorf("failed to apply update: %w", err)
	}
	return nil
}
