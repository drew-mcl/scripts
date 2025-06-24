package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"os"
	"sort"
	"strings"

	"github.com/charmbracelet/huh"
	"github.com/charmbracelet/lipgloss"
	"github.com/your-username/ansible-inventory-go/ansibleinv" // <-- IMPORTANT: Use your module path
	"gopkg.in/yaml.v3"
)

// --- Lipgloss Styles (re-used from before) ---
var (
	headerStyle = lipgloss.NewStyle().
			Bold(true).
			Foreground(lipgloss.Color("63")).
			Underline(true).
			MarginBottom(1)

	groupStyle = lipgloss.NewStyle().
			Bold(true).
			Foreground(lipgloss.Color("205"))

	hostStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("201"))

	errorStyle = lipgloss.NewStyle().
			Bold(true).
			Foreground(lipgloss.Color("196"))

	successStyle = lipgloss.NewStyle().
			Bold(true).
			Foreground(lipgloss.Color("46")) // Green
)

// --- Main application router ---
func main() {
	// If the user runs `go run . generate`, start the interactive session.
	if len(os.Args) > 1 && os.Args[1] == "generate" {
		runInteractiveGenerator()
		return
	}

	// Otherwise, proceed with the existing flag-based viewer logic.
	runViewer()
}

// --- Interactive Generator Logic ---

// Struct to hold the answers from the form
type InventoryConfig struct {
	Team         string
	Name         string
	Environments []string
	Filename     string
}

func runInteractiveGenerator() {
	config := InventoryConfig{}
	predefinedEnvs := []string{"dev", "qa", "staging", "uat", "oat", "canary", "prod"}

	// Create the interactive form using huh
	form := huh.NewForm(
		huh.NewGroup(
			huh.NewInput().
				Title("What is the team name?").
				Description("This will be used for metadata and the filename.").
				Value(&config.Team),

			huh.NewInput().
				Title("What is the inventory name?").
				Description("e.g., 'microservices', 'data-platform'").
				Value(&config.Name),

			huh.NewMultiSelect[string]().
				Title("Select environments to create").
				Description("Use space to select, enter to confirm.").
				Options(huh.NewOptions(predefinedEnvs...)...).
				Value(&config.Environments),

			huh.NewInput().
				Title("What should the output filename be?").
				Value(&config.Filename).
				// Suggest a default filename based on previous answers
				WithSuggestion(func() string {
					if config.Team != "" && config.Name != "" {
						return fmt.Sprintf("inventory-%s-%s.yaml", strings.ToLower(config.Team), strings.ToLower(config.Name))
					}
					return "inventory.yaml"
				}()),
		),
	)

	fmt.Println(headerStyle.Render("Ansible Inventory Generator"))
	err := form.Run()
	if err != nil {
		log.Fatal("Aborted.", err)
	}

	// Confirmation step before writing to disk
	var confirmed bool
	confirmForm := huh.NewConfirm().
		Title("Ready to create inventory?").
		Description(fmt.Sprintf("Team: %s\nName: %s\nEnvs: %s\nFile: %s",
			config.Team, config.Name, strings.Join(config.Environments, ", "), config.Filename)).
		Value(&confirmed)

	err = confirmForm.Run()
	if err != nil || !confirmed {
		log.Println("Cancelled.")
		return
	}

	// Generate the file
	err = generateInventoryFile(config)
	if err != nil {
		log.Fatal(errorStyle.Render(fmt.Sprintf("Failed to generate file: %v", err)))
	}

	fmt.Println(successStyle.Render(fmt.Sprintf("✔ Successfully created inventory file: %s", config.Filename)))
}

// generateInventoryFile builds the YAML structure and writes it to a file.
func generateInventoryFile(config InventoryConfig) error {
	// We build a map[string]any that directly represents the desired YAML structure.
	// This is simpler than building our full Inventory struct and trying to serialize it.

	// --- Create the YAML structure programmatically ---
	children := make(map[string]any)
	for _, env := range config.Environments {
		// Each environment is a group with an empty hosts map to start.
		children[env] = map[string]any{
			"hosts": make(map[string]any),
		}
	}

	allGroup := map[string]any{
		"vars": map[string]string{
			"team":           config.Team,
			"inventory_name": config.Name,
		},
		"children": children,
	}

	root := map[string]any{
		"all": allGroup,
	}

	// --- Marshal the structure into YAML bytes ---
	yamlData, err := yaml.Marshal(root)
	if err != nil {
		return fmt.Errorf("could not marshal data to YAML: %w", err)
	}

	// --- Write the file ---
	return os.WriteFile(config.Filename, yamlData, 0644)
}

// --- Existing Viewer Logic (wrapped in a function) ---

func runViewer() {
	inventoryPath := flag.String("i", "inventory.yaml", "Path to the Ansible inventory file.")
	graphFlag := flag.Bool("graph", false, "Display the inventory graph of groups and hosts.")
	hostFlag := flag.String("host", "", "Display all variables for a specific host.")
	listFlag := flag.Bool("list", false, "Output the entire inventory as JSON (compatible with Ansible's --list).")
	flag.Parse()

	if !*graphFlag && *hostFlag == "" && !*listFlag {
		fmt.Println(errorStyle.Render("Error: You must specify a viewer action: --graph, --host <name>, or --list"))
		fmt.Println("Or run 'go run . generate' to create a new inventory.")
		fmt.Println("\nViewer Usage:")
		flag.PrintDefaults()
		os.Exit(1)
	}

	inv, err := ansibleinv.ParseYAMLFile(*inventoryPath)
	if err != nil {
		log.Fatal(errorStyle.Render(fmt.Sprintf("Failed to parse inventory: %v", err)))
	}

	if *graphFlag {
		displayGraph(inv)
	} else if *hostFlag != "" {
		displayHost(inv, *hostFlag)
	} else if *listFlag {
		displayListJSON(inv)
	}
}

// ... (The rest of the file: displayGraph, displayHost, and displayListJSON functions remain unchanged)
func displayGraph(inv *ansibleinv.Inventory) {
	fmt.Println(headerStyle.Render("Inventory Graph"))

	var groupNames []string
	for name := range inv.Groups {
		groupNames = append(groupNames, name)
	}
	sort.Strings(groupNames)

	for _, groupName := range groupNames {
		group := inv.Groups[groupName]
		fmt.Println(groupStyle.Render(fmt.Sprintf("@%s:", group.Name)))

		if len(group.Hosts) == 0 {
			fmt.Println("  |-- (no hosts in this group directly)")
			continue
		}

		var hostNames []string
		for name := range group.Hosts {
			hostNames = append(hostNames, name)
		}
		sort.Strings(hostNames)

		for _, hostName := range hostNames {
			fmt.Printf("  |-- %s\n", hostStyle.Render(hostName))
		}
	}
}

func displayHost(inv *ansibleinv.Inventory, hostName string) {
	fmt.Println(headerStyle.Render(fmt.Sprintf("Variables for Host: %s", hostName)))

	resolvedVars, err := inv.GetResolvedVariablesForHost(hostName)
	if err != nil {
		log.Fatal(errorStyle.Render(err.Error()))
	}

	yamlOutput, err := yaml.Marshal(resolvedVars)
	if err != nil {
		log.Fatal(errorStyle.Render(fmt.Sprintf("Failed to format variables: %v", err)))
	}

	fmt.Println(string(yamlOutput))
}

func displayListJSON(inv *ansibleinv.Inventory) {
	output := make(map[string]interface{})
	meta := make(map[string]interface{})
	hostvars := make(map[string]interface{})

	allHosts := []string{}
	for hostName, host := range inv.Hosts {
		allHosts = append(allHosts, hostName)
		resolved, _ := inv.GetResolvedVariablesForHost(hostName)
		hostvars[hostName] = resolved
	}
	sort.Strings(allHosts)
	meta["hostvars"] = hostvars
	output["_meta"] = meta
	output["all"] = map[string][]string{"hosts": allHosts}

	for groupName, group := range inv.Groups {
		if groupName == "all" {
			continue
		}
		groupHosts := []string{}
		for hostName := range group.Hosts {
			groupHosts = append(groupHosts, hostName)
		}
		sort.Strings(groupHosts)
		output[groupName] = map[string]interface{}{
			"hosts": groupHosts,
			"vars":  group.Vars,
		}
	}

	jsonOutput, err := json.MarshalIndent(output, "", "  ")
	if err != nil {
		log.Fatal(errorStyle.Render(fmt.Sprintf("Failed to generate JSON: %v", err)))
	}

	fmt.Println(string(jsonOutput))
}
