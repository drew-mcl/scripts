// ============================================================================
// File: internal/updater/diff.go
// ----------------------------------------------------------------------------
// Very small SemVer diff util (major/minor/patch) – no external deps beyond
// "golang.org/x/mod/semver" which we already import elsewhere.
// ============================================================================
package updater

import "golang.org/x/mod/semver"

type Level int

const (
    UpToDate Level = iota
    Patch
    Minor
    Major
)

func diff(cur, next string) Level {
    if semver.Compare(cur, next) >= 0 {
        return UpToDate
    }
    if semver.Major(cur) != semver.Major(next) {
        return Major
    }
    if semver.MajorMinor(cur) != semver.MajorMinor(next) {
        return Minor
    }
    return Patch
}

// ============================================================================
// File: internal/updater/updater.go
// ----------------------------------------------------------------------------
// Core update-checker + downloader. No third-party self-update libs. Only uses
// GitLab's official Go client.
// ============================================================================
package updater

import (
    "bufio"
    "compress/gzip"
    "context"
    "crypto/sha256"
    "encoding/hex"
    "errors"
    "fmt"
    "io"
    "log/slog"
    "net/http"
    "os"
    "path/filepath"
    "runtime"
    "strings"
    "time"

    "gitlab.com/gitlab-org/api/client-go/gitlab"
    "golang.org/x/mod/semver"
)

// ---------------------------------------------------------------------------
// Public error values
// ---------------------------------------------------------------------------
var (
    ErrMajorChange      = errors.New("incompatible major version change")
    ErrMinorChange      = errors.New("new features available in minor version change")
    ErrNoUpdate         = errors.New("no new version available")
    ErrChecksumMismatch = errors.New("downloaded file checksum does not match expected checksum")
)

// ---------------------------------------------------------------------------
// ReleaseInfo – what a caller needs to decide what message to print.
// ---------------------------------------------------------------------------
type ReleaseInfo struct {
    Version     string
    BinaryURL   string
    AssetName   string
    ChecksumURL string
    ChangeType  error // one of ErrMajorChange / ErrMinorChange
}

// ---------------------------------------------------------------------------
// Functional options – lets tests override baseURL / httpClient easily.
// ---------------------------------------------------------------------------
type option func(*opts)

type opts struct {
    baseURL    string
    httpClient *http.Client
    logger     *slog.Logger
}

func defaultOpts() *opts {
    return &opts{
        baseURL:    "https://gitlab.com",
        httpClient: http.DefaultClient,
        logger:     slog.New(slog.NewTextHandler(io.Discard, nil)),
    }
}

func WithBaseURL(u string) option   { return func(o *opts) { o.baseURL = u } }
func WithHTTPClient(c *http.Client) option { return func(o *opts) { o.httpClient = c } }
func WithLogger(l *slog.Logger) option     { return func(o *opts) { o.logger = l } }

// ---------------------------------------------------------------------------
// CheckForUpdates – network-calls only.
// ---------------------------------------------------------------------------
func CheckForUpdates(ctx context.Context, currentVersion, projectSlug, token string, optFns ...option) (*ReleaseInfo, error) {
    if !semver.IsValid(currentVersion) {
        return nil, fmt.Errorf("current version %q is not valid semver", currentVersion)
    }
    o := defaultOpts()
    for _, f := range optFns {
        f(o)
    }

    cli, err := gitlab.NewClient(token, gitlab.WithBaseURL(o.baseURL), gitlab.WithHTTPClient(o.httpClient))
    if err != nil {
        return nil, fmt.Errorf("create gitlab client: %w", err)
    }

    rels, _, err := cli.Releases.ListReleases(projectSlug, &gitlab.ListReleasesOptions{PerPage: 1})
    if err != nil {
        return nil, fmt.Errorf("fetch releases: %w", err)
    }
    if len(rels) == 0 {
        return nil, ErrNoUpdate
    }
    latest := rels[0]
    latestVer := latest.TagName
    if semver.Compare(currentVersion, latestVer) >= 0 {
        return nil, ErrNoUpdate
    }

    assetName := fmt.Sprintf("your-cli_%s_%s.tar.gz", runtime.GOOS, runtime.GOARCH)
    var binURL, cksURL string
    for _, l := range latest.Assets.Links {
        switch {
        case l.Name == assetName:
            binURL = l.URL
        case l.Name == "checksums.sha256":
            cksURL = l.URL
        }
    }
    if binURL == "" || cksURL == "" {
        return nil, fmt.Errorf("required assets missing in release %s", latestVer)
    }

    info := &ReleaseInfo{
        Version:     latestVer,
        BinaryURL:   binURL,
        AssetName:   assetName,
        ChecksumURL: cksURL,
    }
    if semver.Major(currentVersion) != semver.Major(latestVer) {
        info.ChangeType = ErrMajorChange
    } else {
        info.ChangeType = ErrMinorChange
    }
    return info, nil
}

// ---------------------------------------------------------------------------
// ApplyUpdate – download, verify checksum, untar+swap.
// ---------------------------------------------------------------------------
func ApplyUpdate(ctx context.Context, info *ReleaseInfo, token string, optFns ...option) error {
    o := defaultOpts()
    for _, f := range optFns {
        f(o)
    }

    // 1. download checksums file first
    cksMap, err := fetchChecksums(ctx, info.ChecksumURL, token, o)
    if err != nil {
        return err
    }
    expected, ok := cksMap[info.AssetName]
    if !ok {
        return fmt.Errorf("checksum file missing entry for %s", info.AssetName)
    }

    // 2. download binary asset (tgz)
    tgzPath, err := downloadTemp(ctx, info.BinaryURL, token, o)
    if err != nil {
        return err
    }
    defer os.Remove(tgzPath)

    if err := verifySHA256(tgzPath, expected); err != nil {
        return err
    }

    // 3. extract actual binary out of tar.gz
    binTmp, err := extractBinary(tgzPath)
    if err != nil {
        return err
    }
    defer os.Remove(binTmp)

    // 4. atomic swap
    curExe, err := os.Executable()
    if err != nil {
        return err
    }
    if runtime.GOOS == "windows" {
        return swapWindows(curExe, binTmp)
    }
    return os.Rename(binTmp, curExe)
}

// ---------------------------------------------------------------------------
// helpers – download & verify
// ---------------------------------------------------------------------------
func fetchChecksums(ctx context.Context, url, token string, o *opts) (map[string]string, error) {
    tmp, err := downloadTemp(ctx, url, token, o)
    if err != nil {
        return nil, err
    }
    defer os.Remove(tmp)

    f, err := os.Open(tmp)
    if err != nil {
        return nil, err
    }
    defer f.Close()

    m := make(map[string]string)
    scanner := bufio.NewScanner(f)
    for scanner.Scan() {
        parts := strings.Fields(scanner.Text())
        if len(parts) == 2 {
            m[parts[1]] = parts[0]
        }
    }
    return m, scanner.Err()
}

func downloadTemp(ctx context.Context, url, token string, o *opts) (string, error) {
    req, _ := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
    if token != "" {
        req.Header.Set("PRIVATE-TOKEN", token)
    }
    resp, err := o.httpClient.Do(req)
    if err != nil {
        return "", err
    }
    defer resp.Body.Close()
    if resp.StatusCode != http.StatusOK {
        return "", fmt.Errorf("download %s: %s", url, resp.Status)
    }

    tmp, err := os.CreateTemp("", "yourcli-*")
    if err != nil {
        return "", err
    }
    if _, err := io.Copy(tmp, resp.Body); err != nil {
        tmp.Close()
        return "", err
    }
    tmp.Close()
    return tmp.Name(), nil
}

func verifySHA256(path, expected string) error {
    f, err := os.Open(path)
    if err != nil {
        return err
    }
    defer f.Close()
    h := sha256.New()
    if _, err := io.Copy(h, f); err != nil {
        return err
    }
    got := hex.EncodeToString(h.Sum(nil))
    if got != expected {
        return fmt.Errorf("%w: exp %s got %s", ErrChecksumMismatch, expected, got)
    }
    return nil
}

// ---------------------------------------------------------------------------
// tar extraction
// ---------------------------------------------------------------------------
func extractBinary(tgz string) (string, error) {
    f, err := os.Open(tgz)
    if err != nil {
        return "", err
    }
    defer f.Close()
    gz, err := gzip.NewReader(f)
    if err != nil {
        return "", err
    }
    defer gz.Close()

    tr := io.TeeReader(gz, io.Discard)
    // We only need to copy first file out (goreleaser puts bin at root)
    // Very small hand-rolled extractor:
    tmp := filepath.Join(os.TempDir(), "yourcli-new-")
    out, err := os.CreateTemp("", "yourcli-bin-*")
    if err != nil {
        return "", err
    }
    if _, err := io.Copy(out, tr); err != nil {
        out.Close()
        return "", err
    }
    out.Chmod(0o755)
    out.Close()
    return out.Name(), nil
}

// ============================================================================
// File: internal/updater/swap_windows.go (build tag)
// +build windows
// ============================================================================

package updater

import (
    "os"
    "syscall"
)

func swapWindows(dest, src string) error {
    destBackup := dest + ".old"
    // remove any stale .old
    _ = os.Remove(destBackup)
    if err := os.Rename(dest, destBackup); err != nil {
        return err
    }
    // MOVEFILE_REPLACE_EXISTING
    return syscall.MoveFileEx(syscall.StringToUTF16Ptr(src), syscall.StringToUTF16Ptr(dest), syscall.MOVEFILE_REPLACE_EXISTING)
}

// ============================================================================
// File: internal/updater/swap_unix.go (build tag)
// +build !windows
// ============================================================================

// empty – Unix handled by os.Rename in main code

// ============================================================================
// File: internal/updater/updater_test.go
// ----------------------------------------------------------------------------
// Basic coverage: no-update, minor, major, checksum mismatch.
// ============================================================================
package updater_test

import (
    "bytes"
    "context"
    "crypto/sha256"
    "encoding/hex"
    "fmt"
    "net/http"
    "net/http/httptest"
    "os"
    "runtime"
    "strings"
    "testing"

    "github.com/stretchr/testify/require"
    "your-cli/internal/updater"
)

// fakeGitLab spins a minimal GitLab Releases API with one release.
func fakeGitLab(t *testing.T, tag string, assetBody []byte, goodChecksum bool) *httptest.Server {
    t.Helper()
    assetName := fmt.Sprintf("your-cli_%s_%s.tar.gz", runtime.GOOS, runtime.GOARCH)
    sum := sha256.Sum256(assetBody)
    checksum := hex.EncodeToString(sum[:])
    if !goodChecksum {
        checksum = strings.Repeat("0", 64)
    }
    cksContent := fmt.Sprintf("%s  %s\n", checksum, assetName)

    srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        switch {
        case strings.HasSuffix(r.URL.Path, "/releases"):
            fmt.Fprintf(w, `[{"tag_name":"%s","assets":{"links":[{"name":"%s","url":"%s/assets/bin"},{"name":"checksums.sha256","url":"%s/assets/cks"}]}}]`, tag, assetName, srv.URL, srv.URL)
        case strings.HasSuffix(r.URL.Path, "/assets/bin"):
            w.Write(assetBody)
        case strings.HasSuffix(r.URL.Path, "/assets/cks"):
            w.Write([]byte(cksContent))
        default:
            http.NotFound(w, r)
        }
    }))
    return srv
}

func TestNoUpdate(t *testing.T) {
    srv := fakeGitLab(t, "v1.0.0", []byte("dummy"), true)
    defer srv.Close()

    info, err := updater.CheckForUpdates(context.Background(), "v1.0.0", "dummy", "", updater.WithBaseURL(srv.URL))
    require.ErrorIs(t, err, updater.ErrNoUpdate)
    require.Nil(t, info)
}

func TestMinorAndMajor(t *testing.T) {
    srv := fakeGitLab(t, "v1.1.0", []byte("dummy"), true)
    defer srv.Close()
    info, err := updater.CheckForUpdates(context.Background(), "v1.0.0", "dummy", "", updater.WithBaseURL(srv.URL))
    require.NoError(t, err)
    require.Equal(t, updater.ErrMinorChange, info.ChangeType)

    srv2 := fakeGitLab(t, "v2.0.0", []byte("dummy"), true)
    defer srv2.Close()
    info2, err := updater.CheckForUpdates(context.Background(), "v1.1.0", "dummy", "", updater.WithBaseURL(srv2.URL))
    require.NoError(t, err)
    require.Equal(t, updater.ErrMajorChange, info2.ChangeType)
}

func TestChecksumMismatch(t *testing.T) {
    srv := fakeGitLab(t, "v1.1.0", []byte("dummy"), false)
    defer srv.Close()
    info, err := updater.CheckForUpdates(context.Background(), "v1.0.0", "dummy", "", updater.WithBaseURL(srv.URL))
    require.NoError(t, err)
    err = updater.ApplyUpdate(context.Background(), info, "", updater.WithBaseURL(srv.URL))
    require.ErrorIs(t, err, updater.ErrChecksumMismatch)
}

// ============================================================================
// File: cmd/update.go (excerpt)
// ============================================================================
package cmd

import (
    "context"
    "os"

    "github.com/spf13/cobra"
    "your-cli/internal/updater"
)

func newUpdateCmd(version, project string) *cobra.Command {
    return &cobra.Command{
        Use:   "update",
        Short: "Download and install the latest version of your-cli",
        RunE: func(cmd *cobra.Command, _ []string) error {
            ctx := context.Background()
            token := os.Getenv("GITLAB_TOKEN")
            info, err := updater.CheckForUpdates(ctx, version, project, token)
            if err != nil {
                return err
            }
            return updater.ApplyUpdate(ctx, info, token)
        },
    }
}

// ============================================================================
// File: cmd/root.go (snippet showing PersistentPostRunE)
// ============================================================================
package cmd

import (
    "context"
    "errors"
    "fmt"
    "log/slog"
    "os"
    "time"

    "github.com/spf13/cobra"
    "your-cli/internal/updater"
)

var updateChecked bool

func colour(code int, msg string) string { return fmt.Sprintf("\033[%dm%s\033[0m", code, msg) }

const ( yellow = 33; red = 31 )

func notifyColour(info *updater.ReleaseInfo) {
    switch info.ChangeType {
    case updater.ErrMinorChange:
        fmt.Fprintln(os.Stderr, colour(yellow, "A newer minor version ("+info.Version+") is available – run 'your-cli update'."))
    case updater.ErrMajorChange:
        fmt.Fprintln(os.Stderr, colour(red, "You are a major version behind ("+info.Version+"). Generated templates may fail – please 'your-cli update' now."))
    }
}

func attachUpdateCheck(root *cobra.Command, version, project string) {
    root.PersistentPostRunE = func(cmd *cobra.Command, _ []string) error {
        if updateChecked || cmd.Name() == "update" || !isTerminal() {
            return nil
        }
        updateChecked = true
        ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
        defer cancel()
        info, err := updater.CheckForUpdates(ctx, version, project, os.Getenv("GITLAB_TOKEN"))
        switch {
        case errors.Is(err, updater.ErrNoUpdate):
            return nil
        case err != nil:
            slog.Debug("update check failed", "err", err)
            return nil
        default:
            notifyColour(info)
            return nil
        }
    }
}

// helper – very small TTY check
func isTerminal() bool {
    fi, err := os.Stderr.Stat()
    return err == nil && (fi.Mode()&os.ModeCharDevice) != 0
}
