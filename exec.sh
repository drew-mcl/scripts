# Set environment variables required by the Go script
export CI_PROJECT_ID="12345"
export GITLAB_API_TOKEN="your-dummy-token" # The script will still run and generate a changelog
export CI_SERVER_URL="https://gitlab.com"

# Set the version for the new release
export RELEASE_VERSION="1.2.0"

# Run the Go release script against the test repo
# This should create a release for