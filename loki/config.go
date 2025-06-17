package config

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"

	"github.com/zalando/go-keyring"
)

const (
	service   = "your-cli"
	tokenItem = "gitlab-pat"
)

type Config struct {
	Token string `json:"token,omitempty"`
}

func SaveToken(token string) error {
	// 1. try OS keyring
	if err := keyring.Set(service, tokenItem, token); err == nil {
		return nil
	}
	// 2. fallback to file
	path, err := filePath()
	if err != nil {
		return err
	}
	os.MkdirAll(filepath.Dir(path), 0o700)
	f, err := os.OpenFile(path, os.O_RDWR|os.O_CREATE|os.O_TRUNC, 0o600)
	if err != nil {
		return err
	}
	defer f.Close()
	return json.NewEncoder(f).Encode(Config{Token: token})
}

func Token() (string, error) {
	// env-var always wins
	if t := os.Getenv("GITLAB_TOKEN"); t != "" {
		return t, nil
	}
	if t, err := keyring.Get(service, tokenItem); err == nil {
		return t, nil
	}
	// check file fallback
	path, err := filePath()
	if err != nil {
		return "", err
	}
	f, err := os.Open(path)
	if err != nil {
		return "", fmt.Errorf("no token found—run `your-cli init-auth`: %w", err)
	}
	defer f.Close()
	var cfg Config
	if err := json.NewDecoder(f).Decode(&cfg); err != nil {
		return "", err
	}
	if cfg.Token == "" {
		return "", fmt.Errorf("token empty—run `your-cli init-auth`")
	}
	return cfg.Token, nil
}

func filePath() (string, error) {
	base := os.Getenv("XDG_CONFIG_HOME")
	if base == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return "", err
		}
		base = filepath.Join(home, ".config")
	}
	return filepath.Join(base, "your-cli", "config.json"), nil
}
