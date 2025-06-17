package selfupdate

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"time"

	"github.com/spf13/cobra"

	"your-cli/internal/config"
	"your-cli/internal/updater"
)

type Mode int

const (
	// Inform only – print a yellow (minor) or red (major) notice and continue.
	Notice Mode = iota
	// Strict – block when a newer *major* is found unless --allow-outdated is set.
	Strict
)

// Attach wires the update-check to root.PersistentPreRunE or
// root.PersistentPostRunE depending on chosen Mode.
// Call this exactly once in main.go **after** you’ve added all sub-commands.
func Attach(root *cobra.Command, version, project string, mode Mode) {
	switch mode {
	case Notice:
		attachPost(root, version, project)
	case Strict:
		attachPre(root, version, project)
	default:
		panic("unsupported self-update mode")
	}
}

/* ------------------------------------------------------------------------- */
// Notice mode – just print after the user’s command finishes.
/* ------------------------------------------------------------------------- */

func attachPost(root *cobra.Command, ver, project string) {
	var done bool
	root.PersistentPostRunE = func(cmd *cobra.Command, _ []string) error {
		if done || cmd.Name() == "update" || !isTTY() {
			return nil
		}
		done = true
		checkAndNotify(cmd.Context(), ver, project, false) // never block
		return nil
	}
}

/* ------------------------------------------------------------------------- */
// Strict mode – block before execution on major mismatch.
/* ------------------------------------------------------------------------- */

func attachPre(root *cobra.Command, ver, project string) {
	root.PersistentFlags().Bool("allow-outdated", false,
		"run even when a newer major version is available")

	root.PersistentPreRunE = func(cmd *cobra.Command, _ []string) error {
		if cmd.Name() == "update" || !isTTY() {
			return nil
		}
		allow, _ := cmd.Flags().GetBool("allow-outdated")
		return checkAndNotify(cmd.Context(), ver, project, allow)
	}
}

/* ------------------------------------------------------------------------- */
// shared helper
/* ------------------------------------------------------------------------- */

func checkAndNotify(ctx context.Context, ver, project string, allow bool) error {
	ctx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()

	token, _ := config.Token()
	info, err := updater.CheckForUpdates(ctx, ver, project, token)
	switch {
	case errors.Is(err, updater.ErrNoUpdate):
		return nil
	case err != nil:
		slog.Debug("update check failed", "err", err)
		return nil
	}

	switch info.ChangeType {
	case updater.ErrMinorChange:
		notice(yellow, "A newer minor version (%s) is available – run 'your-cli update'.", info.Version)
		return nil
	case updater.ErrMajorChange:
		if allow {
			notice(red, "You are a major version behind (%s) – continuing anyway.", info.Version)
			return nil
		}
		notice(red, "You are a major version behind (%s). Please run 'your-cli update'.", info.Version)
		return updater.ErrMajorChange
	}
	return nil
}

func notice(col int, format string, a ...interface{}) {
	fmt.Fprintf(os.Stderr, "\033[%dm%s\033[0m\n", col, fmt.Sprintf(format, a...))
}

func isTTY() bool {
	fi, err := os.Stderr.Stat()
	return err == nil && (fi.Mode()&os.ModeCharDevice) != 0
}

const (
	yellow = 33
	red    = 31
)
