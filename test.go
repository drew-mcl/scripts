package tui

import (
	"context"
	"fmt"

	"loki/internal/scaffold"

	"github.com/charmbracelet/huh/v2"
)

// mockHosts simulates an API/database call.
func mockHosts(env string) []string {
	switch env {
	case "dev":
		return []string{"dev-01", "dev-02", "dev-03"}
	case "qa":
		return []string{"qa-blue", "qa-green"}
	case "uat":
		return []string{"uat-canary"}
	case "staging":
		return []string{"stage-east", "stage-west"}
	case "prod":
		return []string{"prod-a", "prod-b", "prod-c"}
	default:
		return []string{"host-x"}
	}
}

// RunCreateAppForm launches the interactive form and returns
// fully-populated scaffold.Options.
func RunCreateAppForm(ctx context.Context, appName string) (scaffold.Options, error) {
	var (
		// first page
		envChoices []string
		// dynamic page values (one slice per env)
		hostSel    = map[string]string{}
		sshUsers   = map[string]string{}
		sshSecrets = map[string]string{}
	)

	/* ───── Page 1 – pick environments ───── */

	pageEnvs := huh.NewGroup(
		huh.NewMultiSelect[string]().
			Title("Select environments").
			Options(
				huh.NewOption("dev", "dev"),
				huh.NewOption("qa", "qa"),
				huh.NewOption("uat", "uat"),
				huh.NewOption("staging", "staging"),
				huh.NewOption("canary", "canary"),
				huh.NewOption("prod", "prod"),
			).
			Value(&envChoices),
	)

	if err := huh.NewForm(pageEnvs).WithContext(ctx).Run(); err != nil {
		return scaffold.Options{}, err
	}
	if len(envChoices) == 0 {
		return scaffold.Options{}, fmt.Errorf("you must pick at least one environment")
	}

	/* ───── Page 2+ – one dynamic group per env ───── */

	var groups []huh.Group
	for _, env := range envChoices {
		// allocate backing vars so pointers stay stable
		envCopy := env
		hostSel[env] = ""
		sshUsers[env] = ""
		sshSecrets[env] = ""

		groups = append(groups,
			huh.NewGroup(
				huh.NewSelect[string]().
					Title(fmt.Sprintf("%s → pick host", env)).
					Options(func() []huh.Option[string] {
						opts := make([]huh.Option[string], 0)
						for _, h := range mockHosts(envCopy) {
							opts = append(opts, huh.NewOption(h, h))
						}
						return opts
					}()...).
					Value(&hostSel[envCopy]),
				huh.NewInput().
					Title(fmt.Sprintf("%s → ssh user", env)).
					Placeholder("svc_user").
					Value(&sshUsers[envCopy]).
					Validate(huh.Required[string]("user required")),
				huh.NewInput().
					Title(fmt.Sprintf("%s → secret / key path", env)).
					Placeholder("vault:apps/foo/dev").
					Value(&sshSecrets[envCopy]).
					Validate(huh.Required[string]("secret required")),
			),
		)
	}

	if err := huh.NewForm(groups...).WithContext(ctx).Run(); err != nil {
		return scaffold.Options{}, err
	}

	/* ───── Build scaffold.Options ───── */

	var meta []scaffold.Env
	for _, env := range envChoices {
		meta = append(meta, scaffold.Env{
			Name:   env,
			User:   sshUsers[env],
			Secret: sshSecrets[env],
			Host:   hostSel[env],
		})
	}

	return scaffold.Options{
		Name:    appName,
		Envs:    envChoices,
		EnvMeta: meta,
	}, nil
}
