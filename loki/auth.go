package cmd

import (
	"fmt"
	"os"

	"your-cli/internal/config"

	"github.com/spf13/cobra"
	"gitlab.com/gitlab-org/api/client-go/gitlab"
	"golang.org/x/crypto/ssh/terminal"
)

func newInitAuthCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "init-auth",
		Short: "Authenticate your CLI with GitLab once",
		RunE: func(cmd *cobra.Command, _ []string) error {
			fmt.Print("🔑  Paste your GitLab Personal Access Token (read_api scope): ")
			byteToken, err := terminal.ReadPassword(int(os.Stdin.Fd()))
			fmt.Println()
			if err != nil {
				return err
			}
			token := string(byteToken)

			// quick validation
			cli, err := gitlab.NewClient(token)
			if err == nil {
				_, _, err = cli.Users.CurrentUser()
			}
			if err != nil {
				return fmt.Errorf("token validation failed: %w", err)
			}
			if err := config.SaveToken(token); err != nil {
				return err
			}
			fmt.Println("✔ Token saved securely – you’re ready to go!")
			return nil
		},
	}
}
