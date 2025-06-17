package cmd

import (
	"errors"
	"fmt"
	"log/slog"
	"your-cli/updater" // <-- Import our new module

	"github.com/fatih/color"
	"github.com/spf13/cobra"
)

var updateCmd = &cobra.Command{
	Use:   "update",
	Short: "Update the CLI to the latest version from GitLab",
	Run: func(cmd *cobra.Command, args []string) {
		red := color.New(color.FgRed).Add(color.Bold)
		yellow := color.New(color.FgYellow)
		green := color.New(color.FgGreen)
		bold := color.New(color.Bold).SprintFunc()

		gitlabRepoSlug := "your-gitlab-group/your-repo"
		currentVersion := rootCmd.Version

		fmt.Printf("Current version: %s\n", bold(currentVersion))
		fmt.Println("Checking for new releases...")

		// 1. Call the raw updater module to check for changes
		result, err := updater.CheckForUpdates(currentVersion, gitlabRepoSlug)
		if err != nil {
			slog.Error("Update check failed", "error", err)
			red.Println("Error: Could not check for updates.")
			return
		}

		// 2. Use the result to drive user-facing output
		switch {
		case errors.Is(result.ChangeType, updater.ErrNoUpdate):
			green.Println("✅ You are already using the latest version.")
			return

		case errors.Is(result.ChangeType, updater.ErrMajorChange):
			red.Printf("WARNING: A new major version (%s) is available!\n", result.LatestRelease.Version())
			red.Println("This may contain incompatible changes.")

		case errors.Is(result.ChangeType, updater.ErrMinorChange):
			yellow.Printf("INFO: A new version with added features (%s) is available!\n", result.LatestRelease.Version())
		}

		// Ask for confirmation before performing the update
		fmt.Print("Do you want to update? (y/n): ")
		var response string
		fmt.Scanln(&response)
		if response != "y" && response != "Y" {
			fmt.Println("Update cancelled.")
			return
		}

		// 3. Call the raw module again to perform the update
		slog.Debug("User confirmed, performing update...")
		if err := updater.PerformUpdate(result.LatestRelease); err != nil {
			slog.Error("Failed to perform update", "error", err)
			red.Println("Error: The update process failed.")
			return
		}

		green.Printf("✅ Successfully updated to version %s\n", bold(result.LatestRelease.Version()))
	},
}
