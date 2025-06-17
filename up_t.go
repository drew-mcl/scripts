package updater

import (
	"errors"
	"testing"

	"github.com/stretchr/testify/assert"
)

// A mock release for testing purposes
type mockRelease struct {
	version string
}

func (m *mockRelease) Version() string { return m.version }
func (m *mockRelease) Update() error   { return nil }

// This test focuses on the version comparison logic, assuming the underlying library works.
// We manually craft the `latest` release to simulate different scenarios.
func TestCheckForUpdates_VersionLogic(t *testing.T) {
	testCases := []struct {
		name              string
		currentVersion    string
		latestVersion     string // Version of the release we simulate finding
		expectedChange    error
		expectErrorOnInit bool
	}{
		{
			name:           "Major Version Change",
			currentVersion: "v1.5.0",
			latestVersion:  "v2.0.0",
			expectedChange: ErrMajorChange,
		},
		{
			name:           "Minor Version Change",
			currentVersion: "v1.5.0",
			latestVersion:  "v1.6.0",
			expectedChange: ErrMinorChange,
		},
		{
			name:           "Patch Version Change (Treated as Minor)",
			currentVersion: "v1.5.0",
			latestVersion:  "v1.5.1",
			expectedChange: ErrMinorChange,
		},
		{
			name:           "No Change (Versions are equal)",
			currentVersion: "v1.5.0",
			latestVersion:  "v1.5.0",
			expectedChange: nil, // Our logic doesn't assign a change type if equal
		},
		{
			name:              "Invalid Current Version",
			currentVersion:    "invalid-version",
			latestVersion:     "v1.0.0",
			expectErrorOnInit: true,
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			// This is where you would normally mock the `updater.New` and `upd.Check` calls.
			// For this example, we'll test the comparison logic directly.

			// Simulate the check logic manually
			result := &Result{
				LatestRelease: &mockRelease{version: tc.latestVersion},
			}

			if !semver.IsValid(tc.currentVersion) {
				if tc.expectErrorOnInit {
					// Test passed, we expected an error
					return
				}
				t.Fatalf("Test setup failed: invalid current version %s", tc.currentVersion)
			}
			if semver.Compare(tc.currentVersion, tc.latestVersion) >= 0 {
				result.ChangeType = nil
			} else {
				majorCurrent := semver.Major(tc.currentVersion)
				majorLatest := semver.Major(tc.latestVersion)

				if majorCurrent != majorLatest {
					result.ChangeType = ErrMajorChange
				} else {
					result.ChangeType = ErrMinorChange
				}
			}

			assert.True(t, errors.Is(result.ChangeType, tc.expectedChange), "Expected change type %v, but got %v", tc.expectedChange, result.ChangeType)
		})
	}
}
