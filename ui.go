package main

import (
	"fmt"
	"log"
	"os"
	"sort"
	"strings"

	"github.com/charmbracelet/bubbles/viewport"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/your-username/ansible-inventory-go/ansibleinv" // <-- IMPORTANT: Use your module path
)

// Define some styles using Lipgloss
var (
	// Style for the container around the panes
	containerStyle = lipgloss.NewStyle().
			Border(lipgloss.RoundedBorder()).
			BorderForeground(lipgloss.Color("63")) // Purple

	// Style for an active, focused pane
	activePaneStyle = lipgloss.NewStyle().
			Border(lipgloss.NormalBorder()).
			BorderForeground(lipgloss.Color("205")) // Pink

	// Style for an inactive pane
	inactivePaneStyle = lipgloss.NewStyle().
				Border(lipgloss.NormalBorder()).
				BorderForeground(lipgloss.Color("240")) // Grey

	// Style for a selected item in a list
	selectedItemStyle = lipgloss.NewStyle().
				Background(lipgloss.Color("205")).
				Foreground(lipgloss.Color("231"))

	// Help text style
	helpStyle = lipgloss.NewStyle().Foreground(lipgloss.Color("241"))
)

// model holds the state of our TUI application.
type model struct {
	inventory *ansibleinv.Inventory // The parsed inventory data
	groups    []*ansibleinv.Group   // A sorted slice of all groups to display
	cursor    int                   // Which group we're pointing at in the left pane
	width     int
	height    int
	viewport  viewport.Model // Use a viewport for the right pane to handle scrolling
}

// initialModel creates the starting state of our application.
// This is where we parse the inventory file.
func initialModel() model {
	// For this example, we'll hardcode the file path.
	// In a real app, you'd get this from a command-line argument.
	inventoryFile := "example.yaml"
	if _, err := os.Stat(inventoryFile); os.IsNotExist(err) {
		log.Fatalf("Inventory file not found: %s. Please create it.", inventoryFile)
	}

	inv, err := ansibleinv.ParseYAMLFile(inventoryFile)
	if err != nil {
		log.Fatalf("Could not parse inventory: %v", err)
	}

	// Get a sorted list of groups for stable ordering
	var groups []*ansibleinv.Group
	for _, group := range inv.Groups {
		groups = append(groups, group)
	}
	sort.Slice(groups, func(i, j int) bool {
		return groups[i].Name < groups[j].Name
	})

	return model{
		inventory: inv,
		groups:    groups,
		cursor:    0,
		viewport:  viewport.New(80, 20), // Initial size, will be updated
	}
}

// Init is the first command that's run when the program starts.
func (m model) Init() tea.Cmd {
	return nil // No initial command needed
}

// Update handles all incoming events, like key presses and window resizes.
func (m model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {

	// Window was resized
	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		// Recalculate layout
		containerStyle.Width(m.width - 2)
		containerStyle.Height(m.height - 4)
		paneWidth := (m.width - 6) / 2
		activePaneStyle.Width(paneWidth)
		inactivePaneStyle.Width(paneWidth)
		activePaneStyle.Height(m.height - 6)
		inactivePaneStyle.Height(m.height - 6)
		m.viewport.Width = paneWidth
		m.viewport.Height = m.height - 6

	// A key was pressed
	case tea.KeyMsg:
		switch msg.String() {
		// Exit the program
		case "ctrl+c", "q":
			return m, tea.Quit

		// Move the cursor up
		case "up", "k":
			if m.cursor > 0 {
				m.cursor--
			}

		// Move the cursor down
		case "down", "j":
			if m.cursor < len(m.groups)-1 {
				m.cursor++
			}
		}
	}

	// Update the content of the right-pane viewport
	m.viewport.SetContent(m.renderRightPane())

	// Handle viewport scrolling
	var cmd tea.Cmd
	m.viewport, cmd = m.viewport.Update(msg)

	return m, cmd
}

// View is called to render the UI.
func (m model) View() string {
	if m.width == 0 {
		return "Initializing..."
	}

	// Render the two panes
	left := activePaneStyle.Render(m.renderLeftPane())
	right := inactivePaneStyle.Render(m.viewport.View())

	// Join the panes horizontally
	panes := lipgloss.JoinHorizontal(lipgloss.Top, left, right)

	// Final layout
	ui := lipgloss.JoinVertical(lipgloss.Top,
		containerStyle.Render(panes),
		helpStyle.Render("Use ↑/↓ to navigate. Press 'q' to quit."),
	)

	return ui
}

// renderLeftPane builds the string content for the groups list.
func (m model) renderLeftPane() string {
	var b strings.Builder
	for i, group := range m.groups {
		if i == m.cursor {
			b.WriteString(selectedItemStyle.Render(fmt.Sprintf("> %s (%d hosts)", group.Name, len(group.Hosts))))
		} else {
			b.WriteString(fmt.Sprintf("  %s (%d hosts)", group.Name, len(group.Hosts)))
		}
		b.WriteRune('\n')
	}
	return b.String()
}

// renderRightPane builds the string content for the hosts list of the selected group.
func (m model) renderRightPane() string {
	if len(m.groups) == 0 {
		return "No groups found."
	}

	selectedGroup := m.groups[m.cursor]

	var hostNames []string
	for name := range selectedGroup.Hosts {
		hostNames = append(hostNames, name)
	}
	sort.Strings(hostNames)

	var b strings.Builder
	b.WriteString(lipgloss.NewStyle().Bold(true).Render(fmt.Sprintf("Hosts in [%s]", selectedGroup.Name)))
	b.WriteString("\n\n")
	for _, hostName := range hostNames {
		b.WriteString(fmt.Sprintf("- %s\n", hostName))
	}

	return b.String()
}

func main() {
	// Create the `example.yaml` file if it doesn't exist for a smooth first run.
	if _, err := os.Stat("example.yaml"); os.IsNotExist(err) {
		content := `
all:
  children:
    prod:
      hosts:
        prod-web-1:
        prod-web-2:
        prod-db-1:
      vars:
        env: production
    staging:
      hosts:
        staging-web-1:
        staging-db-1:
      vars:
        env: staging
`
		os.WriteFile("example.yaml", []byte(content), 0644)
	}

	p := tea.NewProgram(initialModel(), tea.WithAltScreen())
	if _, err := p.Run(); err != nil {
		log.Fatalf("Alas, there's been an error: %v", err)
	}
}
