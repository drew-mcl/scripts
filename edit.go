func (inv *Inventory) GetResolvedVariablesForHost(hostName string) (map[string]any, error) {
	host, exists := inv.Hosts[hostName]
	if !exists {
		return nil, fmt.Errorf("host '%s' not found in inventory", hostName)
	}

	resolvedVars := make(map[string]any)

	// Find all groups the host belongs to
	var memberGroups []*Group
	for _, group := range inv.Groups {
		if _, isMember := group.Hosts[hostName]; isMember {
			memberGroups = append(memberGroups, group)
		}
	}

	// Sort groups by name to ensure a stable, predictable order of variable application.
	// 'all' should always come first.
	sort.Slice(memberGroups, func(i, j int) bool {
		if memberGroups[i].Name == "all" {
			return true
		}
		if memberGroups[j].Name == "all" {
			return false
		}
		return memberGroups[i].Name < memberGroups[j].Name
	})

	// Apply variables from groups first
	for _, group := range memberGroups {
		for key, val := range group.Vars {
			resolvedVars[key] = val
		}
	}

	// Finally, apply host-specific vars, which have the highest precedence
	for key, val := range host.Vars {
		resolvedVars[key] = val
	}

	return resolvedVars, nil
}

package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"os"
	"sort"

	"github.com/charmbracelet/lipgloss"
	"github.com/your-username/ansible-inventory-go/ansibleinv" // <-- IMPORTANT: Use your module path
	"gopkg.in/yaml.v3"
)

// --- Lipgloss Styles for beautiful output ---
var (
	headerStyle = lipgloss.NewStyle().
			Bold(true).
			Foreground(lipgloss.Color("63")). // Purple
			Underline(true).
			MarginBottom(1)

	groupStyle = lipgloss.NewStyle().
			Bold(true).
			Foreground(lipgloss.Color("205")) // Pink

	hostStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("201")) // Another pink/purple

	errorStyle = lipgloss.NewStyle().
			Bold(true).
			Foreground(lipgloss.Color("196")) // Red
)

func main() {
	// --- Define CLI Flags ---
	inventoryPath := flag.String("i", "inventory.yaml", "Path to the Ansible inventory file.")
	graphFlag := flag.Bool("graph", false, "Display the inventory graph of groups and hosts.")
	hostFlag := flag.String("host", "", "Display all variables for a specific host.")
	listFlag := flag.Bool("list", false, "Output the entire inventory as JSON (compatible with Ansible's --list).")

	flag.Parse()

	// --- Input Validation ---
	if !*graphFlag && *hostFlag == "" && !*listFlag {
		fmt.Println(errorStyle.Render("Error: You must specify an action: --graph, --host <name>, or --list"))
		fmt.Println("Usage:")
		flag.PrintDefaults()
		os.Exit(1)
	}

	// --- Parse the Inventory ---
	inv, err := ansibleinv.ParseYAMLFile(*inventoryPath)
	if err != nil {
		log.Fatal(errorStyle.Render(fmt.Sprintf("Failed to parse inventory: %v", err)))
	}

	// --- Execute the requested action ---
	if *graphFlag {
		displayGraph(inv)
	} else if *hostFlag != "" {
		displayHost(inv, *hostFlag)
	} else if *listFlag {
		displayListJSON(inv)
	}
}

// displayGraph shows all groups and their hosts.
func displayGraph(inv *ansibleinv.Inventory) {
	fmt.Println(headerStyle.Render("Inventory Graph"))

	// Get a sorted list of group names for consistent output
	var groupNames []string
	for name := range inv.Groups {
		groupNames = append(groupNames, name)
	}
	sort.Strings(groupNames)

	for _, groupName := range groupNames {
		group := inv.Groups[groupName]
		// Use @ to denote a group, similar to ansible-inventory
		fmt.Println(groupStyle.Render(fmt.Sprintf("@%s:", group.Name)))

		if len(group.Hosts) == 0 {
			fmt.Println("  |-- (no hosts in this group directly)")
			continue
		}

		// Get a sorted list of hosts for consistent output
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

// displayHost shows all computed variables for a single host.
func displayHost(inv *ansibleinv.Inventory, hostName string) {
	fmt.Println(headerStyle.Render(fmt.Sprintf("Variables for Host: %s", hostName)))

	resolvedVars, err := inv.GetResolvedVariablesForHost(hostName)
	if err != nil {
		log.Fatal(errorStyle.Render(err.Error()))
	}

	// Use the YAML marshaller for beautiful, human-readable output of variables
	yamlOutput, err := yaml.Marshal(resolvedVars)
	if err != nil {
		log.Fatal(errorStyle.Render(fmt.Sprintf("Failed to format variables: %v", err)))
	}

	fmt.Println(string(yamlOutput))
}

// displayListJSON outputs the inventory in a JSON format compatible with Ansible.
func displayListJSON(inv *ansibleinv.Inventory) {
	// This structure mimics Ansible's --list output
	output := make(map[string]interface{})
	meta := make(map[string]interface{})
	hostvars := make(map[string]interface{})

	allHosts := []string{}
	for hostName, host := range inv.Hosts {
		allHosts = append(allHosts, hostName)
		// We can pre-calculate resolved vars for the _meta section
		resolved, _ := inv.GetResolvedVariablesForHost(hostName)
		hostvars[hostName] = resolved
	}
	sort.Strings(allHosts)
	meta["hostvars"] = hostvars
	output["_meta"] = meta
	output["all"] = map[string][]string{"hosts": allHosts}

	// Add all other groups
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

	// Marshal to indented JSON
	jsonOutput, err := json.MarshalIndent(output, "", "  ")
	if err != nil {
		log.Fatal(errorStyle.Render(fmt.Sprintf("Failed to generate JSON: %v", err)))
	}

	fmt.Println(string(jsonOutput))
}