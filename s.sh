#!/bin/bash
#
# This script installs 'yourprogram' for macOS and Linux systems by downloading
# the latest release from GitLab.
#
# Usage: curl -sSL https://gitlab.com/your-user/your-repo/-/raw/main/install.sh | bash

# Exit immediately if a command exits with a non-zero status.
set -e

# --- SCRIPT CONFIGURATION ---
# The name of your program's executable.
PROGRAM_NAME="yourprogram"
# The GitLab repository path in the format "username/repo".
GITLAB_REPO="your-user/your-repo"
# ---

# --- MAIN LOGIC ---
main() {
  # 1. Run pre-installation checks
  run_checks

  # 2. Determine OS, architecture, and latest version
  local os
  local arch
  local latest_version
  os=$(get_os)
  arch=$(get_arch)
  latest_version=$(get_latest_version)
  
  local install_dir="$HOME/.$PROGRAM_NAME"
  local bin_path="$install_dir/$PROGRAM_NAME"

  echo "Installing $PROGRAM_NAME v$latest_version ($os/$arch) to $install_dir..."

  # 3. Download and extract the binary
  download_and_extract "$latest_version" "$os" "$arch" "$install_dir"

  # 4. Add the program to the user's PATH
  add_to_path "$install_dir"

  # 5. Provide final instructions
  echo ""
  echo -e "\033[32m$PROGRAM_NAME was installed successfully!\033[0m"
  echo -e "\033[33mPlease restart your terminal or run 'source \$HOME/.bashrc' or 'source \$HOME/.zshrc' to use it.\033[0m"
  echo "You can then run the program by typing: $PROGRAM_NAME"
}


# --- HELPER FUNCTIONS ---

run_checks() {
  echo "Running pre-installation checks..."
  if [ -z "$HOME" ]; then
    echo "Error: The HOME environment variable is not set." >&2
    exit 1
  fi
}

get_os() {
  local os
  os="$(uname -s | tr '[:upper:]' '[:lower:]')"
  case "$os" in
    linux) echo "linux" ;;
    darwin) echo "darwin" ;;
    *)
      echo "Error: Unsupported OS: $os" >&2
      exit 1
      ;;
  esac
}

get_arch() {
  local arch
  arch="$(uname -m)"
  case "$arch" in
    x86_64) echo "amd64" ;;
    arm64 | aarch64) echo "arm64" ;;
    *)
      echo "Error: Unsupported architecture: $arch" >&2
      exit 1
      ;;
  esac
}

get_latest_version() {
  # Fetches the latest tag name from the GitLab releases API
  local url="https://gitlab.com/api/v4/projects/${GITLAB_REPO//\//%2F}/releases"
  local version
  version=$(curl -sSL "$url" | grep -o '"tag_name":"[^"]*' | head -n 1 | cut -d'"' -f4)
  if [ -z "$version" ]; then
    echo "Error: Could not determine the latest version from GitLab." >&2
    exit 1
  fi
  echo "$version"
}

download_and_extract() {
  local version="$1"
  local os="$2"
  local arch="$3"
  local install_dir="$4"
  
  # Example binary name: yourprogram-v1.0.0-darwin-arm64.tar.gz
  local filename="${PROGRAM_NAME}-${version}-${os}-${arch}.tar.gz"
  local download_url="https://gitlab.com/${GITLAB_REPO}/-/releases/${version}/downloads/${filename}"
  local tarball_path="/tmp/$filename"

  echo "Downloading from $download_url..."
  if ! curl -fSL -o "$tarball_path" "$download_url"; then
    echo "Error: Failed to download the release asset. Please check the URL and your connection." >&2
    exit 1
  fi

  mkdir -p "$install_dir"
  if [ ! -d "$install_dir" ] || [ ! -w "$install_dir" ]; then
      echo "Error: Could not create or write to the installation directory: $install_dir" >&2
      exit 1
  fi

  echo "Extracting archive..."
  tar -xzf "$tarball_path" -C "$install_dir"
  # Assuming the binary inside the tar.gz has the name $PROGRAM_NAME
  chmod +x "$install_dir/$PROGRAM_NAME"
  rm "$tarball_path"
}

add_to_path() {
  local install_dir="$1"
  local shell_name
  local profile_file
  
  shell_name=$(basename "$SHELL")
  if [ "$shell_name" = "zsh" ]; then
    profile_file="$HOME/.zshrc"
  elif [ "$shell_name" = "bash" ]; then
    profile_file="$HOME/.bashrc"
  else
    profile_file="$HOME/.profile"
  fi

  local export_line="export PATH=\"\$PATH:$install_dir\""
  
  echo "Adding to PATH in $profile_file..."
  if ! grep -qF -- "$export_line" "$profile_file" 2>/dev/null; then
    echo -e "\n# Added by $PROGRAM_NAME installer\n$export_line" >> "$profile_file"
  else
    echo "PATH entry already exists."
  fi
}

# --- SCRIPT EXECUTION ---
main "$@"