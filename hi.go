package main

import (
	"bytes"
	"errors"
	"flag"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"text/template"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/huh"
	"gopkg.in/yaml.v3"
)

// -------------------------------------------------------------
// DATA MODELS
// -------------------------------------------------------------

type selection struct {
	Envs  []string            // selected envs in deterministic order
	Hosts map[string][]string // env -> host list (order preserved as chosen)
}

// -------------------------------------------------------------
// MOCK DISCOVERY (replace with real svc / DB / API)
// -------------------------------------------------------------

var allEnvs = []string{"dev", "qa", "staging", "prod"}

func fetchHostsForEnv(env string) []string {
	switch env {
	case "dev":
		return []string{"dev-app1", "dev-db1"}
	case "qa":
		return []string{"qa-app1", "qa-app2", "qa-db1"}
	case "staging":
		return []string{"stg-app", "stg-db"}
	case "prod":
		return []string{"app1", "app2", "db1", "db2"}
	default:
		return nil
	}
}

// -------------------------------------------------------------
// YAML (DE)SERIALISATION
// -------------------------------------------------------------

// Minimal inventory schema for
// all:
//   children:
//     env:
//       hosts:
//         hostname:
//           ansible_host: hostname
//

type hostNode struct {
	AnsibleHost string `yaml:"ansible_host"`
}

type envNode struct {
	Hosts map[string]hostNode `yaml:"hosts"`
}

type inventoryRoot struct {
	All struct {
		Children map[string]envNode `yaml:"children"`
	} `yaml:"all"`
}

func parseInventory(path string) (*selection, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var root inventoryRoot
	if err := yaml.Unmarshal(data, &root); err != nil {
		return nil, err
	}
	sel := &selection{Hosts: map[string][]string{}}
	for env, node := range root.All.Children {
		sel.Envs = append(sel.Envs, env)
		for host := range node.Hosts {
			sel.Hosts[env] = append(sel.Hosts[env], host)
		}
	}
	sort.Strings(sel.Envs)
	return sel, nil
}

func writeInventory(path string, sel selection) error {
	root := inventoryRoot{}
	root.All.Children = map[string]envNode{}
	for _, env := range sel.Envs {
		hosts := map[string]hostNode{}
		for _, h := range sel.Hosts[env] {
			hosts[h] = hostNode{AnsibleHost: h}
		}
		root.All.Children[env] = envNode{Hosts: hosts}
	}
	out, err := yaml.Marshal(root)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	return os.WriteFile(path, out, fs.FileMode(0o644))
}

// -------------------------------------------------------------
// BUBBLE TEA + HUH DYNAMIC FORM
// -------------------------------------------------------------

type model struct {
	form *huh.Form

	// reactive fields bound to widgets
	envSelected string              // current env for host picker
	envMulti    []string            // final env selection
	hostsPicked map[string][]string // accumulates per-env hosts

	// finish
	done    bool
	invYaml string
	err     error
}

func newModel(existing *selection) *model {
	m := &model{hostsPicked: map[string][]string{}}

	// prime defaults if editing
	if existing != nil {
		m.envMulti = append([]string(nil), existing.Envs...) // copy
		m.hostsPicked = make(map[string][]string, len(existing.Hosts))
		for k, v := range existing.Hosts {
			m.hostsPicked[k] = append([]string(nil), v...)
		}
	}

	// --- widgets ------------------------------------------------

	envMultiSel := huh.NewMultiSelect[string]().
		Title("Select environments:").
		Options(huh.NewOptions(allEnvs...)...).
		Value(&m.envMulti)

	envSelect := huh.NewSelect[string]().
		Title("Active environment (for host picking):").
		Options(huh.NewOptions(allEnvs...)...).
		Value(&m.envSelected)

	hostMulti := huh.NewMultiSelect[string]().
		Height(10).
		TitleFunc(func() string {
			if m.envSelected == "" {
				return "— choose an environment first —"
			}
			return fmt.Sprintf("Hosts for %s:", m.envSelected)
		}, &m.envSelected).
		OptionsFunc(func() []huh.Option[string] {
			if m.envSelected == "" {
				return nil
			}
			opts := []huh.Option[string]{}
			for _, h := range fetchHostsForEnv(m.envSelected) {
				sel := contains(m.hostsPicked[m.envSelected], h)
				opts = append(opts, huh.Option[string]{Key: h, Value: h, Selected: sel})
			}
			return opts
		}, &m.envSelected).
		ValueFunc(func(picks []string) {
			if m.envSelected != "" {
				m.hostsPicked[m.envSelected] = picks
			}
		})

	m.form = huh.NewForm(envMultiSel, envSelect, hostMulti).WithSubmitFunc(func(f *huh.Form) error {
		// basic validation
		if len(m.envMulti) == 0 {
			return errors.New("pick at least one environment")
		}
		// finalise selection into YAML
		sel := selection{Envs: m.envMulti, Hosts: m.hostsPicked}
		buf := &bytes.Buffer{}
		if err := template.Must(template.New("inv").Parse(`# generated\nall:\n  children:\n{{- range $env, $hosts := .Hosts }}\n    {{$env}}:\n      hosts:\n{{- range $hosts }}\n        {{.}}:\n          ansible_host: {{.}}\n{{- end }}{{ end }}\n`)).Execute(buf, sel); err != nil {
			return err
		}
		m.invYaml = buf.String()
		m.done = true
		return nil
	})

	return m
}

func (m *model) Init() tea.Cmd {
	return m.form.Init()
}

func (m *model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	if m.done || m.err != nil {
		return m, tea.Quit
	}
	var cmd tea.Cmd
	m.form, cmd = m.form.Update(msg)
	return m, cmd
}

func (m *model) View() string {
	if m.err != nil {
		return "error: " + m.err.Error()
	}
	if m.done {
		return "Generated inventory:\n\n" + m.invYaml + "\n"
	}
	return m.form.View()
}

// -------------------------------------------------------------
// MAIN
// -------------------------------------------------------------

func main() {
	var (
		outputPath = flag.String("o", ".ansible/app.yml", "output inventory path")
		edit       = flag.Bool("edit", false, "edit existing inventory if present")
	)
	flag.Parse()

	var existing *selection
	if *edit {
		if sel, err := parseInventory(*outputPath); err == nil {
			existing = sel
		}
	}

	m := newModel(existing)
	if _, err := tea.NewProgram(m).Run(); err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}

	if m.invYaml != "" {
		if err := os.WriteFile(*outputPath, []byte(m.invYaml), 0o644); err != nil {
			fmt.Fprintln(os.Stderr, "failed to save inventory:", err)
			os.Exit(1)
		}
		fmt.Println("Inventory written to", *outputPath)
	}
}

// -------------------------------------------------------------
// HELPERS
// -------------------------------------------------------------

func contains(list []string, v string) bool {
	for _, x := range list {
		if x == v {
			return true
		}
	}
	return false
}
