package tui

import (
	"fmt"
	"strings"

	"github.com/charmbracelet/bubbles/checkbox"
	"github.com/charmbracelet/bubbles/textinput"
	tea "github.com/charmbracelet/bubbletea"

	"loki/internal/scaffold"
)

type step int

const (
	stepChooseEnvs step = iota
	stepPerEnv
	stepDone
)

// ----- Bubble Tea model -----
type createAppModel struct {
	appName string
	step    step
	envBox  checkbox.Model   // multi-select
	input   textinput.Model  // user/secret capture
	opts    scaffold.Options // accumulates answers
	currEnv int              // index while iterating per-env
	err     error
}

func NewCreateAppModel(name string) *createAppModel {
	envs := []string{"dev", "qa", "uat", "staging", "canary", "prod", "oat"}
	box := checkbox.New(envs)
	box.CursorMode = checkbox.CursorBlink
	box.Focus()

	in := textinput.New()
	in.CharLimit = 64
	in.Placeholder = "ssh user"

	return &createAppModel{
		appName: name,
		step:    stepChooseEnvs,
		envBox:  box,
		input:   in,
		opts:    scaffold.Options{Name: name},
	}
}

// ----- tea.Model interface -----
func (m *createAppModel) Init() tea.Cmd { return nil }

func (m *createAppModel) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch m.step {

	// STEP 1 ─ choose environments (checkbox list)
	case stepChooseEnvs:
		var cmd tea.Cmd
		m.envBox, cmd = m.envBox.Update(msg)
		if m.envBox.Submitted() {
			m.opts.Envs = m.envBox.Value()
			if len(m.opts.Envs) == 0 {
				m.err = fmt.Errorf("no environments chosen")
				return m, tea.Quit
			}
			m.step = stepPerEnv
			m.input.Placeholder = fmt.Sprintf("%s ssh user", m.opts.Envs[0])
			return m, nil
		}
		return m, cmd

	// STEP 2 ─ gather per-environment user & secret
	case stepPerEnv:
		var cmd tea.Cmd
		m.input, cmd = m.input.Update(msg)

		switch msg := msg.(type) {
		case tea.KeyMsg:
			switch msg.String() {
			case "enter":
				entry := m.input.Value()
				if strings.TrimSpace(entry) == "" {
					return m, cmd
				}
				env := m.opts.Envs[m.currEnv]

				if !strings.Contains(entry, "@") { // assume first field is user
					m.opts.EnvMeta = append(m.opts.EnvMeta, scaffold.Env{
						Name: env, User: entry,
					})
					m.input.Reset()
					m.input.Placeholder = fmt.Sprintf("%s secret path", env)
					return m, nil
				}
				// second field: secret
				m.opts.EnvMeta[len(m.opts.EnvMeta)-1].Secret = entry
				m.currEnv++
				m.input.Reset()

				if m.currEnv >= len(m.opts.Envs) {
					m.step = stepDone
					return m, tea.Quit
				}
				env = m.opts.Envs[m.currEnv]
				m.input.Placeholder = fmt.Sprintf("%s ssh user", env)
			}
		}
		return m, cmd
	}
	return m, nil
}

func (m *createAppModel) View() string {
	if m.err != nil {
		return fmt.Sprintf("error: %v\n", m.err)
	}
	switch m.step {
	case stepChooseEnvs:
		return lipgloss.NewStyle().Bold(true).Render(
			"Choose environments (space to toggle, enter to confirm):\n") +
			m.envBox.View()
	case stepPerEnv:
		return lipgloss.NewStyle().Bold(true).Render(
			"Provide ssh user then secret for each env (enter to confirm):\n") +
			m.input.View()
	default:
		return "Scaffolding...\n"
	}
}

// ----- result carrier -----
type CreateAppResult struct {
	Options scaffold.Options
	Err     error
}

func (m *createAppModel) Quit() tea.Msg { // called automatically on Quit
	return CreateAppResult{Options: m.opts, Err: m.err}
}
