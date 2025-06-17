 // ----------------------------
// File: internal/config/config.go
// ----------------------------
package config

import (
    "log/slog"
    "os"
    "path/filepath"
)

const (
    AppName   = "loki"
    tokenFile = "token"
    envDebug  = "LOKI_DEBUG" // set to 1 to enable debug output
)

// Dir returns the configuration directory (e.g. ~/.config/loki).
func Dir() string {
    dir, _ := os.UserConfigDir()
    return filepath.Join(dir, AppName)
}

// TokenPath returns the full path to the stored GitLab PAT.
func TokenPath() string {
    return filepath.Join(Dir(), tokenFile)
}

// NewLogger constructs a slog.Logger that logs Info and above by default.
// When the environment variable LOKI_DEBUG=1 is set, the level is lowered to Debug.
func NewLogger() *slog.Logger {
    lvl := new(slog.LevelVar)
    if os.Getenv(envDebug) == "1" {
        lvl.Set(slog.LevelDebug)
    } else {
        lvl.Set(slog.LevelInfo)
    }
    handler := slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: lvl})
    return slog.New(handler)
}

// -------------------------
// File: cmd/init_cmd.go
// -------------------------
package cmd

import (
    "bufio"
    "context"
    "errors"
    "fmt"
    "io/fs"
    "net/url"
    "os"
    "os/exec"
    "path/filepath"
    "strings"
    "time"

    "github.com/fatih/color"
    "github.com/spf13/cobra"
    "github.com/xanzy/go-gitlab"

    "your-module/internal/config"
)

const (
    repoURL   = "git@gitlab.com:your-group/your-monorepo.git"
    groupPath = "your-group"
)

var (
    logger = config.NewLogger()
)

var initCmd = &cobra.Command{
    Use:   "init",
    Short: "Interactively set up Loki for first-time use.",
    Long: `init performs the following tasks:\n  • Verifies your GitLab Personal Access Token (PAT) and group access.\n  • Checks SSH connectivity to GitLab.\n  • Clones the Asgard monorepo to a directory you choose.\n  • Emits a summary of the actions taken.`,
    RunE: runInit,
}

func init() {
    rootCmd.AddCommand(initCmd)
}

// runInit coordinates the full initialization flow.
func runInit(cmd *cobra.Command, _ []string) error {
    green := color.New(color.FgGreen).PrintfFunc()
    cyan := color.New(color.FgCyan).PrintfFunc()
    bold := color.New(color.Bold).PrintfFunc()

    bold("\n👋  Welcome to Loki! Let’s get you set up.\n\n")

    pat, err := ensureToken(cmd.Context(), green, cyan)
    if err != nil {
        return err
    }

    if err := validateGroupAccess(cmd.Context(), pat); err != nil {
        return err
    }
    green("✔ GitLab access confirmed.\n")

    if err := checkSSHAccess(); err != nil {
        return err
    }
    green("✔ SSH access to GitLab OK.\n")

    clonePath, err := promptCloneDir()
    if err != nil {
        return err
    }

    if err := gitCloneOrPull(repoURL, clonePath); err != nil {
        return err
    }
    green("✔ Repository ready at %s\n", clonePath)

    bold("\n🎉  Loki initialization complete. Happy shipping!\n")
    return nil
}

// ensureToken loads an existing PAT or prompts the user.
func ensureToken(ctx context.Context, green, cyan func(string, ...interface{})) (string, error) {
    tokPath := config.TokenPath()
    if data, err := os.ReadFile(tokPath); err == nil {
        logger.Debug("token already present", "path", tokPath)
        return strings.TrimSpace(string(data)), nil
    }

    cyan("A GitLab Personal Access Token with \"api\" scope is required.\n")
    fmt.Print("Paste your PAT: ")
    scanner := bufio.NewScanner(os.Stdin)
    if !scanner.Scan() {
        return "", errors.New("no input received")
    }
    tok := strings.TrimSpace(scanner.Text())
    logger.Debug("user entered token")

    client, err := gitlab.NewClient(tok)
    if err != nil {
        return "", err
    }
    ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
    defer cancel()
    if _, _, err = client.Users.CurrentUser(gitlab.WithContext(ctx)); err != nil {
        return "", fmt.Errorf("token validation failed: %w", err)
    }
    green("✔ Token validated.\n")

    if err := os.MkdirAll(filepath.Dir(tokPath), 0o700); err != nil {
        return "", err
    }
    if err := os.WriteFile(tokPath, []byte(tok+"\n"), fs.FileMode(0o600)); err != nil {
        return "", err
    }
    green("✔ Token stored at %s (0600).\n", tokPath)
    logger.Debug("token stored", "path", tokPath)
    return tok, nil
}

func validateGroupAccess(ctx context.Context, pat string) error {
    client, err := gitlab.NewClient(pat)
    if err != nil {
        return err
    }
    ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
    defer cancel()
    groups, _, err := client.Groups.ListGroups(&gitlab.ListGroupsOptions{Search: gitlab.String(groupPath)}, gitlab.WithContext(ctx))
    if err != nil {
        return fmt.Errorf("unable to list groups: %w", err)
    }
    for _, g := range groups {
        if g.Path == groupPath {
            return nil
        }
    }
    return fmt.Errorf("token lacks access to group %q", groupPath)
}

func checkSSHAccess() error {
    cmd := exec.Command("ssh", "-T", "git@gitlab.com")
    cmd.Stdin = nil
    cmd.Stdout = nil
    cmd.Stderr = nil
    if err := cmd.Run(); err != nil {
        return fmt.Errorf("SSH authentication failed: %w", err)
    }
    return nil
}

func promptCloneDir() (string, error) {
    cwd, _ := os.Getwd()
    def := filepath.Join(cwd, "asgard")
    fmt.Printf("\nRepo will be cloned to %s.\nPress Enter to accept or type a new path: ", def)
    scanner := bufio.NewScanner(os.Stdin)
    scanner.Scan()
    input := strings.TrimSpace(scanner.Text())
    if input == "" {
        return def, nil
    }
    return input, nil
}

func gitCloneOrPull(repo, dir string) error {
    if _, err := os.Stat(filepath.Join(dir, ".git")); err == nil {
        cmd := exec.Command("git", "-C", dir, "pull", "--ff-only")
        cmd.Stdout = os.Stdout
        cmd.Stderr = os.Stderr
        return cmd.Run()
    }
    if err := os.MkdirAll(dir, 0o755); err != nil {
        return err
    }
    u, _ := url.Parse(repo)
    fmt.Printf("Cloning %s...\n", u.Path)
    cmd := exec.Command("git", "clone", repo, dir)
    cmd.Stdout = os.Stdout
    cmd.Stderr = os.Stderr
    return cmd.Run()
}
