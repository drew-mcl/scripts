// Package ansibleinv provides tools to parse and manipulate Ansible inventory files.
package ansibleinv

import (
	"fmt"
	"sort"
	"strings"

	"gopkg.in/yaml.v3"
)

// Host represents a single host in the inventory.
type Host struct {
	Name string
	Vars map[string]any // Changed to any to support rich YAML types
}

// Group represents a group of hosts.
type Group struct {
	Name     string
	Hosts    map[string]*Host  // Key: Host name
	Vars     map[string]any    // Changed to any
	Children map[string]*Group // Key: Child group name
}

// Inventory represents the entire Ansible inventory.
type Inventory struct {
	Hosts  map[string]*Host  // A flat map of all unique hosts for easy access
	Groups map[string]*Group // All groups defined in the inventory
}

// NewInventory creates and initializes a new Inventory object.
func NewInventory() *Inventory {
	return &Inventory{
		Hosts:  make(map[string]*Host),
		Groups: make(map[string]*Group),
	}
}

// Display prints the inventory in a human-readable format.
func (inv *Inventory) Display() {
	var groupNames []string
	for name := range inv.Groups {
		groupNames = append(groupNames, name)
	}
	sort.Strings(groupNames)

	for _, groupName := range groupNames {
		group := inv.Groups[groupName]
		fmt.Printf("GROUP: [%s]\n", group.Name)

		if len(group.Vars) > 0 {
			fmt.Println("  Vars:")
			// Use YAML marshalling for a nice, readable format of variables
			varsYAML, _ := yaml.Marshal(group.Vars)
			for _, line := range strings.Split(strings.TrimSpace(string(varsYAML)), "\n") {
				fmt.Printf("    %s\n", line)
			}
		}

		if len(group.Children) > 0 {
			fmt.Println("  Children Groups:")
			var childNames []string
			for name := range group.Children {
				childNames = append(childNames, name)
			}
			sort.Strings(childNames)
			for _, childName := range childNames {
				fmt.Printf("    - %s\n", childName)
			}
		}

		if len(group.Hosts) > 0 {
			fmt.Println("  Hosts:")
			var hostNames []string
			for name := range group.Hosts {
				hostNames = append(hostNames, name)
			}
			sort.Strings(hostNames)

			for _, hostName := range hostNames {
				host := group.Hosts[hostName]
				fmt.Printf("    - %s\n", host.Name)
				if len(host.Vars) > 0 {
					// Also print host vars in a nice format
					hostVarsYAML, _ := yaml.Marshal(host.Vars)
					for _, line := range strings.Split(strings.TrimSpace(string(hostVarsYAML)), "\n") {
						fmt.Printf("        %s\n", line)
					}
				}
			}
		}
		fmt.Println(strings.Repeat("-", 40))
	}
}
