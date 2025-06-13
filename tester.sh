#!/bin/bash

# This script sets up a local git repository to simulate a monorepo environment.
# It creates applications and libraries, a dependency graph, and a history of commits and tags.
# This allows for thorough local testing of the 'release.go' script.

# Exit immediately if a command exits with a non-zero status.
set -e

# --- Configuration ---
REPO_DIR="test-repo"
APP_A="app-a"
APP_B="app-b"
LIB_A="lib-a"
LIB_B="lib-b"

# --- Helper Functions ---
# Function to print a message in green
info() {
    echo -e "\033[32m[INFO] $1\033[0m"
}

# Function to create a file and commit it
make_commit() {
    local file_path=$1
    local commit_message=$2
    local jira_id=$3

    # Ensure the directory for the file exists
    mkdir -p "$(dirname "$file_path")"

    # Append a timestamp to the file to simulate a change
    echo "Change at $(date)" >> "$file_path"

    git add "$file_path"
    git commit -m "$commit_message [${jira_id}]"
}

# --- Main Script ---

# 1. Clean up previous test environment
if [ -d "$REPO_DIR" ]; then
    info "Removing existing test repository: $REPO_DIR"
    rm -rf "$REPO_DIR"
fi

info "Creating new test repository in: $REPO_DIR"
mkdir -p "$REPO_DIR/apps/$APP_A"
mkdir -p "$REPO_DIR/apps/$APP_B"
mkdir -p "$REPO_DIR/libs/$LIB_A"
mkdir -p "$REPO_DIR/libs/$LIB_B"
mkdir -p "$REPO_DIR/build"

# 2. Create the dummy dependency graph
info "Creating dependency-graph.json"
cat > "$REPO_DIR/build/dependency-graph.json" << EOL
{
  ":apps:${APP_A}": {
    "projectDir": "apps/${APP_A}",
    "dependencies": [":libs:${LIB_A}"]
  },
  ":apps:${APP_B}": {
    "projectDir": "apps/${APP_B}",
    "dependencies": [":libs:${LIB_A}", ":libs:${LIB_B}"]
  },
  ":libs:${LIB_A}": {
    "projectDir": "libs/${LIB_A}",
    "dependencies": []
  },
  ":libs:${LIB_B}": {
    "projectDir": "libs/${LIB_B}",
    "dependencies": []
  }
}
EOL

# 3. Initialize Git repository and make commits
cd "$REPO_DIR"
git init -b main > /dev/null
git config user.email "test@example.com"
git config user.name "Test User"

info "Initializing git repository and creating commit history..."

# Initial commit
touch README.md
git add .
git commit -m "Initial project structure [EQSRE-100]" > /dev/null

# --- Commit and Tag Sequence ---

# Release v1.0.0 for app-a
make_commit "libs/$LIB_A/main.go" "feat(lib-a): Add core functionality" "EQSRE-101"
make_commit "apps/$APP_A/main.go" "feat(app-a): Initial setup using lib-a" "EQSRE-102"
git tag -a "$APP_A/v1.0.0" -m "Release v1.0.0 for $APP_A"

# Release v1.0.0 for app-b
make_commit "libs/$LIB_B/main.go" "feat(lib-b): New billing module" "EQSRE-201"
make_commit "apps/$APP_B/main.go" "feat(app-b): Integrate billing module from lib-b" "EQSRE-202"
git tag -a "$APP_B/v1.0.0" -m "Release v1.0.0 for $APP_B"

# A shared library change that will affect both apps
make_commit "libs/$LIB_A/main.go" "fix(lib-a): Critical security patch" "EQSRE-103"

# A change only affecting app-a
make_commit "apps/$APP_A/config.yml" "refactor(app-a): Update configuration" "EQSRE-104"

# A change only affecting app-b
make_commit "apps/$APP_B/styles.css" "style(app-b): Improve UI theme" "EQSRE-205"

# Release v1.1.0 for app-a (should include the lib-a fix and the config refactor)
git tag -a "$APP_A/v1.1.0" -m "Release v1.1.0 for $APP_A"

# Another change to the shared library
make_commit "libs/$LIB_A/utils.go" "feat(lib-a): Add new helper functions" "EQSRE-110"

# A final docs change
make_commit "README.md" "docs: Update main project README" "EQSRE-300"


# --- Finished ---
info "Test environment setup complete."
echo
echo "The test repository is located at: $(pwd)"
echo "You can now test the release script from the parent directory."
echo
echo "Example commands to run from outside the '$REPO_DIR' directory:"
echo "  - export RELEASE_VERSION=1.2.0; go run release.go $APP_A"
echo "  - export RELEASE_VERSION=1.0.1; go run release.go $APP_B"
echo
echo "To see the commit log:"
echo "  - cd $REPO_DIR && git log --oneline --graph --all"
