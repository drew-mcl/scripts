// ────────────────────────────────────────────────────────────────────────────────
// Bootstrap Wizard – extended with file scaffolding
// Replace "github.com/yourorg/cli" with your real module path.
//
// Directory tree created:
//   apps/<app-name>/
//   .gitlab/<app-name>.yml     – per‑app job definitions
//   .gitlab-ci.yml             – includes the above file
//   ansible/<app-name>/inventory.yml – generated inventory
//
// ────────────────────────────────────────────────────────────────────────────────
// file: pkg/bootstrap/inventory.go
package bootstrap

import (
    "fmt"
    "strings"

    "gopkg.in/yaml.v3"
)

// Host represents one line under the Ansible host group.
type Host struct {
    AnsibleHost string `yaml:"ansible_host"`
}

// EnvConfig holds vars + hosts for a single environment (dev, qa, …).
type EnvConfig struct {
    AnsibleUser string          `yaml:"ansible_user"`
    Hosts       map[string]Host `yaml:"hosts"`
    KeyPath     string          `yaml:"-"` // kept for runtime reference only
}

// Inventory mirrors the Ansible “all → children → envs” structure.
type Inventory struct {
    All struct {
        Children map[string]EnvConfig `yaml:"children"`
    } `yaml:"all"`
}

// GenerateHostName returns app_###_region_env – lowercase and zero‑padded.
func GenerateHostName(app string, index int, region, env string) string {
    return fmt.Sprintf("%s_%03d_%s_%s", strings.ToLower(app), index, region, env)
}

// NewPlaceholderConfig seeds an env so the group exists even if skipped.
func NewPlaceholderConfig(app, region, env string) EnvConfig {
    name := GenerateHostName(app, 1, region, env)
    return EnvConfig{
        AnsibleUser: "deploy",
        Hosts: map[string]Host{
            name: {AnsibleHost: "REPLACE_WITH_IP"},
        },
    }
}

// BuildInventory merges explicit configs with placeholders.
func BuildInventory(selected []string, explicit map[string]EnvConfig, app, region string) Inventory {
    inv := Inventory{}
    inv.All.Children = make(map[string]EnvConfig)
    for _, env := range selected {
        if cfg, ok := explicit[env]; ok {
            inv.All.Children[env] = cfg
        } else {
            inv.All.Children[env] = NewPlaceholderConfig(app, region, env)
        }
    }
    return inv
}

// MarshalInventory renders Inventory to YAML.
func MarshalInventory(inv Inventory) ([]byte, error) { return yaml.Marshal(inv) }

// ────────────────────────────────────────────────────────────────────────────────
// file: pkg/bootstrap/scaffold.go
package bootstrap

import (
    "fmt"
    "os"
    "path/filepath"
    "strings"
)

// WriteSkeleton writes inventory + GitLab CI stubs + blank app dir.
// rootDir is typically the repo root ("." when running locally).
func WriteSkeleton(rootDir, app string, invYAML []byte, envs []string) error {
    // 1️⃣  apps/<app>/
    if err := os.MkdirAll(filepath.Join(rootDir, "apps", app), 0o755); err != nil {
        return err
    }

    // 2️⃣  ansible/<app>/
    ansibleDir := filepath.Join(rootDir, "ansible", app)
    if err := os.MkdirAll(ansibleDir, 0o755); err != nil {
        return err
    }
    if err := os.WriteFile(filepath.Join(ansibleDir, "inventory.yml"), invYAML, 0o644); err != nil {
        return err
    }

    // 3️⃣  .gitlab-ci.yml  (includes the per‑app component)
    ciPath := filepath.Join(rootDir, ".gitlab-ci.yml")
    includeStmt := fmt.Sprintf("include:\n  - local: \".gitlab/%s.yml\"\n", app)
    if err := os.WriteFile(ciPath, []byte(includeStmt), 0o644); err != nil {
        return err
    }

    // 4️⃣  .gitlab/<app>.yml – one deploy job per env
    gitlabDir := filepath.Join(rootDir, ".gitlab")
    if err := os.MkdirAll(gitlabDir, 0o755); err != nil {
        return err
    }

    var b strings.Builder
    for _, env := range envs {
        fmt.Fprintf(&b, "\n%s_deploy:\n  stage: deploy\n  variables:\n    INVENTORY: ansible/%s/inventory.yml\n  script:\n    - ansible-playbook -i $INVENTORY playbooks/deploy.yml\n  environment:\n    name: %s\n", env, app, env)
    }
    return os.WriteFile(filepath.Join(gitlabDir, fmt.Sprintf("%s.yml", app)), []byte(b.String()), 0o644)
}

// ────────────────────────────────────────────────────────────────────────────────
// file: pkg/bootstrap/wizard.go
package bootstrap

import (
    "fmt"
    "os"

    "github.com/charmbracelet/huh/v2"
)

// RunWizard executes the form, writes files, and returns the inventory path.
func RunWizard(rootDir string) (string, error) {
    var (
        appName, region, language string
        envChoices               []string
    )

    form := huh.NewForm(
        huh.NewGroup(
            huh.NewNote().Title("Let’s start with the basics…"),
            huh.NewInput().Title("App name").Value(&appName).Validate(huh.ValidateNotEmpty()),
            huh.NewSelect[string]().Title("Region").Options(huh.NewOptions("us-east-1", "us-west-2", "eu-west-1")...).Value(&region),
            huh.NewSelect[string]().Title("Language").Options(huh.NewOptions("Java", "C")...).Value(&language),
        ),
        huh.NewGroup(
            huh.NewNote().Title("Pick all environments you’ll deploy to"),
            huh.NewMultiSelect[string]().Title("Environments").Options(huh.NewOptions("dev", "qa", "staging", "prod")...).Value(&envChoices),
        ),
    ).WithTheme(huh.ThemeCharm(true))

    if err := form.Run(); err != nil {
        return "", err
    }

    explicit := map[string]EnvConfig{}
    for _, env := range envChoices {
        var configure bool
        if err := huh.NewForm(
            huh.NewGroup(
                huh.NewConfirm().Title(fmt.Sprintf("Configure %q now?", env)).Affirmative("Yes").Negative("Skip").Value(&configure),
            ),
        ).Run(); err != nil {
            return "", err
        }
        if !configure {
            continue
        }
        cfg, err := collectEnvDetails(appName, region, env)
        if err != nil {
            return "", err
        }
        explicit[env] = cfg
    }

    inv := BuildInventory(envChoices, explicit, appName, region)
    invYAML, err := MarshalInventory(inv)
    if err != nil {
        return "", err
    }

    if err := WriteSkeleton(rootDir, appName, invYAML, envChoices); err != nil {
        return "", err
    }

    invPath := fmt.Sprintf("%s/ansible/%s/inventory.yml", rootDir, appName)
    return invPath, nil
}

// collectEnvDetails captures user + key + arbitrary hosts for one environment.
func collectEnvDetails(app, region, env string) (EnvConfig, error) {
    var cfg EnvConfig
    cfg.Hosts = make(map[string]Host)

    // ── credentials page ────────────────────────────────────────────────
    if err := huh.NewForm(
        huh.NewGroup(
            huh.NewInput().
                Title(fmt.Sprintf("[%s] ansible_user", env)).
                Placeholder("deploy").
                Value(&cfg.AnsibleUser).
                Validate(huh.ValidateNotEmpty()),

            huh.NewInput().
                Title("SSH key path").
                Placeholder("~/.ssh/id_rsa").
                Value(&cfg.KeyPath).
                Validate(huh.ValidateNotEmpty()),
        ),
    ).Run(); err != nil {
        return cfg, err
    }

    // ── hosts loop ─────────────────────────────────────────────────────
    idx := 1
    for {
        var hostAddr string
        var again bool

        if err := huh.NewForm(
            huh.NewGroup(
                huh.NewInput().
                    Title(fmt.Sprintf("Host %02d ansible_host", idx)).
                    Value(&hostAddr).
                    Validate(huh.ValidateNotEmpty()),

                huh.NewConfirm().
                    Title("Add another host?").
                    Affirmative("Yes").
                    Negative("No").
                    Value(&again),
            ),
        ).Run(); err != nil {
            return cfg, err
        }

        hostName := GenerateHostName(app, idx, region, env)
        cfg.Hosts[hostName] = Host{AnsibleHost: hostAddr}

        if !again {
            break
        }
        idx++
    }

    return cfg, nil
}
// ────────────────────────────────────────────────────────────────────────

// ────────────────────────────────────────────────────────────────────────────────
// file: cmd/bootstrap.go
package cmd

import (
    "fmt"

    "github.com/spf13/cobra"

    "github.com/yourorg/cli/pkg/bootstrap"
)

func NewBootstrapCmd() *cobra.Command {
    var rootDir string
    cmd := &cobra.Command{
        Use:   "bootstrap",
        Short: "Interactive wizard to scaffold an app, inventory, and GitLab CI",
        RunE: func(cmd *cobra.Command, _ []string) error {
            path, err := bootstrap.RunWizard(rootDir)
            if err != nil {
                return err
            }
            fmt.Printf("\n✅  Scaffold complete! Inventory written to %s\n", path)
            return nil
        },
    }
    cmd.Flags().StringVar(&rootDir, "root", ".", "project root (defaults to cwd)")
    return cmd
}

// ────────────────────────────────────────────────────────────────────────────────
// file: pkg/bootstrap/scaffold_test.go
package bootstrap_test

import (
    "os"
    "path/filepath"
    "testing"

    "github.com/yourorg/cli/pkg/bootstrap"
)

func TestWriteSkeleton(t *testing.T) {
    dir := t.TempDir()
    inv := bootstrap.Inventory{}
    inv.All.Children = map[string]bootstrap.EnvConfig{"dev": {}}
    yamlBytes, _ := bootstrap.MarshalInventory(inv)

    if err := bootstrap.WriteSkeleton(dir, "svc", yamlBytes, []string{"dev"}); err != nil {
        t.Fatalf("WriteSkeleton failed: %v", err)
    }

    if _, err := os.Stat(filepath.Join(dir, "apps", "svc")); err != nil {
        t.Fatalf("apps dir missing: %v", err)
    }
    if _, err := os.Stat(filepath.Join(dir, ".gitlab-ci.yml")); err != nil {
        t.Fatalf("ci file missing: %v", err)
    }
}
