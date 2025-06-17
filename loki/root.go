package main

import (
	"context"
	"fmt"
	"log/slog"
	"os"
	"strconv"
	"time"

	"github.com/spf13/cobra"

	"your-cli/internal/config"
	"your-cli/internal/updater"
)

// --------------------------------------------------------------------
// build-time flags  (set by Goreleaser: -X main.version=v1.2.3 …)
// --------------------------------------------------------------------
var (
	version   = "dev"
	commit    = "none"
	buildDate = "unknown"
)

// --------------------------------------------------------------------
// rootCmd – all global flags & logger setup
// --------------------------------------------------------------------
var rootCmd = &cobra.Command{
	Use:   "your-cli",
	Short: "Next-gen monorepo controller",
	PersistentPreRunE: func(cmd *cobra.Command, _ []string) error {
		// 1) (re)configure slog exactly once
		if !loggerReady {
			level := slog.LevelInfo
			if v, _ := cmd.Flags().GetString("log-level"); v == "debug" {
				level = slog.LevelDebug
			}
			h := slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: level})
			slog.SetDefault(slog.New(h))
			loggerReady = true
		}
		return nil
	},
}

var loggerReady bool

func init() {
	// global flags
	rootCmd.PersistentFlags().String("log-level", "info", "debug or info")

	// sub-commands
	rootCmd.AddCommand(
		newInitAuthCmd(),                   // one-time PAT setup
		newUpdateCmd(version, projectSlug), // explicit update
		// … your create/graph/dev/manage commands here …
	)

	// wire automatic *notice* check (yellow / red) – not the upgrade
	attachUpdateCheck(rootCmd, version, projectSlug)
}

// --------------------------------------------------------------------
// main()
// --------------------------------------------------------------------
const projectSlug = "your-group/your-cli" // GitLab path or numeric ID

func main() {
	if err := rootCmd.Execute(); err != nil {
		os.Exit(1)
	}
}

// --------------------------------------------------------------------
// attachUpdateCheck – runs once after any non-update command in a TTY
// --------------------------------------------------------------------
func attachUpdateCheck(root *cobra.Command, ver, project string) {
	var checked bool

	root.PersistentPostRunE = func(cmd *cobra.Command, _ []string) error {
		if checked || cmd.Name() == "update" || !isTerminal() {
			return nil
		}
		checked = true

		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()

		token, _ := config.Token() // env var, keyring, or file
		info, err := updater.CheckForUpdates(ctx, ver, project, token)
		switch {
		case err == nil:
			notifyColour(info) // yellow/minor, red/major
		case updater.ErrNoUpdate.Is(err):
			// quiet
		default:
			slog.Debug("update check failed", "err", err)
		}
		return nil
	}
}

// --------------------------------------------------------------------
// tiny helpers
// --------------------------------------------------------------------
func isTerminal() bool {
	fi, err := os.Stderr.Stat()
	return err == nil && (fi.Mode()&os.ModeCharDevice) != 0
}

func notifyColour(info *updater.ReleaseInfo) {
	const (
		yellow = 33
		red    = 31
	)
	colour := func(c int, msg string) string { return "\033[" + strconv.Itoa(c) + "m" + msg + "\033[0m" }

	switch info.ChangeType {
	case updater.ErrMinorChange:
		fmt.Fprintln(os.Stderr, colour(yellow,
			"A newer minor version ("+info.Version+") is available – run 'your-cli update'."))
	case updater.ErrMajorChange:
		fmt.Fprintln(os.Stderr, colour(red,
			"You are a major version behind ("+info.Version+"). Templates may fail – please 'your-cli update' now!"))
	}
}
