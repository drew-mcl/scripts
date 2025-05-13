import os
import hashlib
import وقت # For timestamping the report
from collections import defaultdict

# --- Configuration ---
MAVEN_BUILD_DIR_NAME = "target"
GRADLE_BUILD_DIR_NAME = "build"
FILES_TO_IGNORE = {".DS_Store", "MANIFEST.MF"} # Add other common, irrelevant files
# Consider adding specific sub-directories within target/build to ignore if they are known to be different
# e.g., "maven-status", "tmp", "generated-sources" (unless you want to compare those)
DIRECTORIES_TO_IGNORE_CONTENT_COMPARISON = {"classes", "test-classes", "generated-sources", "reports", "test-results", "tmp"}
# For these directories, we'll primarily check for existence and file lists, not byte-for-byte content of all files within,
# as contents (like compiled classes) can differ due to compiler versions or metadata even if sources are the same.

def calculate_sha256(filepath):
    """Calculates the SHA256 hash of a file."""
    sha256_hash = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except IOError:
        return None

def get_directory_contents(dir_path, ignore_files, ignore_dirs_content_comparison):
    """
    Scans a directory and returns a dictionary of files and their hashes,
    and a set of subdirectories.
    Skips content hashing for specified directories but lists their files.
    """
    contents = {"files": {}, "dirs": set()}
    if not os.path.isdir(dir_path):
        return contents

    for item in os.listdir(dir_path):
        if item in ignore_files:
            continue
        item_path = os.path.join(dir_path, item)
        if os.path.isdir(item_path):
            contents["dirs"].add(item)
            # Optionally, if you want to list files within these ignored-content-dirs:
            if item in ignore_dirs_content_comparison:
                try:
                    for sub_item in os.listdir(item_path):
                        if os.path.isfile(os.path.join(item_path, sub_item)) and sub_item not in ignore_files:
                             # Store with a special marker or just the name for existence check
                            contents["files"][os.path.join(item, sub_item)] = "PRESENT_IN_IGNORED_CONTENT_DIR"
                except OSError:
                    pass # Can't list directory
        elif os.path.isfile(item_path):
            # Determine if parent is an ignored_content_dir
            parent_dir_name = os.path.basename(os.path.dirname(item_path)) # This will be dir_path's name
            # We are at the top level of MAVEN_BUILD_DIR_NAME or GRADLE_BUILD_DIR_NAME here.
            # The check for DIRECTORIES_TO_IGNORE_CONTENT_COMPARISON applies to *subdirectories* of these.
            contents["files"][item] = calculate_sha256(item_path)
    return contents

def compare_build_dirs(project_path, maven_dir_path, gradle_dir_path):
    """
    Compares the contents of Maven's target and Gradle's build directories.
    Returns a dictionary with comparison results.
    """
    print(f"\n--- Comparing Project: {project_path} ---")
    comparison_results = {
        "project_path": project_path,
        "maven_dir": maven_dir_path,
        "gradle_dir": gradle_dir_path,
        "maven_exists": os.path.isdir(maven_dir_path),
        "gradle_exists": os.path.isdir(gradle_dir_path),
        "matches": [],
        "mismatches": [], # Files with same name, different content
        "only_in_maven": [],
        "only_in_gradle": [],
        "common_dirs": [],
        "overall_match": False # Default to False
    }

    if not comparison_results["maven_exists"]:
        print(f"INFO: Maven '{MAVEN_BUILD_DIR_NAME}' directory not found at: {maven_dir_path}")
    if not comparison_results["gradle_exists"]:
        print(f"INFO: Gradle '{GRADLE_BUILD_DIR_NAME}' directory not found at: {gradle_dir_path}")

    if not comparison_results["maven_exists"] or not comparison_results["gradle_exists"]:
        print("Cannot compare: one or both build directories are missing.")
        return comparison_results

    maven_content = get_directory_contents(maven_dir_path, FILES_TO_IGNORE, DIRECTORIES_TO_IGNORE_CONTENT_COMPARISON)
    gradle_content = get_directory_contents(gradle_dir_path, FILES_TO_IGNORE, DIRECTORIES_TO_IGNORE_CONTENT_COMPARISON)

    # Compare files
    all_files = set(maven_content["files"].keys()) | set(gradle_content["files"].keys())
    for f_name in sorted(list(all_files)):
        maven_hash = maven_content["files"].get(f_name)
        gradle_hash = gradle_content["files"].get(f_name)

        if maven_hash and gradle_hash:
            # Special handling for files within dirs where content comparison is skipped
            if maven_hash == "PRESENT_IN_IGNORED_CONTENT_DIR" and gradle_hash == "PRESENT_IN_IGNORED_CONTENT_DIR":
                comparison_results["matches"].append(f"{f_name} (Existence checked in ignored content dir)")
            elif maven_hash == gradle_hash:
                comparison_results["matches"].append(f_name)
            else:
                comparison_results["mismatches"].append(f_name)
        elif maven_hash:
            comparison_results["only_in_maven"].append(f_name)
        elif gradle_hash:
            comparison_results["only_in_gradle"].append(f_name)

    # Compare directories (existence)
    common_dirs_set = maven_content["dirs"] & gradle_content["dirs"]
    comparison_results["common_dirs"] = sorted(list(common_dirs_set))
    # Directories only in one or the other can also be listed if needed
    # comparison_results["dirs_only_in_maven"] = sorted(list(maven_content["dirs"] - gradle_content["dirs"]))
    # comparison_results["dirs_only_in_gradle"] = sorted(list(gradle_content["dirs"] - maven_content["dirs"]))


    # --- Determine "Overall Match" ---
    # This is subjective. A simple definition:
    # - No mismatches in files directly under target/build (unless in ignored content dir).
    # - Key artifacts (e.g., .jar, .war) exist in both and match (if not in ignored content dir).
    # - No significant unexpected files only in one or the other (outside ignored content dirs).
    # You might need to refine this logic based on your specific expectations.

    # For this script, let's say it's an "exact same" if no mismatches and no files exclusively in one or the other
    # at the top level of build/target, and common critical subdirectories are present.
    # Files within DIRECTORIES_TO_IGNORE_CONTENT_COMPARISON won't cause a mismatch if they are just present in both.

    is_different = False
    if comparison_results["mismatches"]:
        is_different = True
        print(f"DIFFERENCE: Mismatched files found.")
    if any(f for f in comparison_results["only_in_maven"] if not maven_content["files"].get(f) == "PRESENT_IN_IGNORED_CONTENT_DIR"):
        is_different = True
        print(f"DIFFERENCE: Files found only in '{MAVEN_BUILD_DIR_NAME}'.")
    if any(f for f in comparison_results["only_in_gradle"] if not gradle_content["files"].get(f) == "PRESENT_IN_IGNORED_CONTENT_DIR"):
        is_different = True
        print(f"DIFFERENCE: Files found only in '{GRADLE_BUILD_DIR_NAME}'.")

    # Check if top-level directories differ (ignoring those where content is ignored)
    maven_top_level_dirs_to_check = maven_content["dirs"] - DIRECTORIES_TO_IGNORE_CONTENT_COMPARISON
    gradle_top_level_dirs_to_check = gradle_content["dirs"] - DIRECTORIES_TO_IGNORE_CONTENT_COMPARISON
    if maven_top_level_dirs_to_check != gradle_top_level_dirs_to_check:
        # is_different = True # This might be too strict, depends on expectations
        print(f"INFO: Top-level directory structure (excluding ignored content dirs) differs.")
        print(f"  Maven dirs: {maven_top_level_dirs_to_check}")
        print(f"  Gradle dirs: {gradle_top_level_dirs_to_check}")


    if not is_different:
        comparison_results["overall_match"] = True
        print("SAME: Build output directories appear to have comparable content based on the defined checks.")
    else:
        comparison_results["overall_match"] = False
        print("DIFFERENT: Build output directories have notable differences.")

    return comparison_results

def find_projects_and_compare(start_path="."):
    """
    Recursively finds projects (Maven or Gradle) and initiates comparison.
    """
    all_project_results = []
    found_projects = set()

    for root, dirs, files in os.walk(start_path):
        # Skip build directories themselves to avoid re-processing
        if os.path.basename(root) == MAVEN_BUILD_DIR_NAME or \
           os.path.basename(root) == GRADLE_BUILD_DIR_NAME or \
           ".git" in root or ".idea" in root or "node_modules" in root: # Add other common ignores
            dirs[:] = [] # Don't go deeper into these
            continue

        project_path = None
        is_maven = "pom.xml" in files
        is_gradle = "build.gradle" in files or "build.gradle.kts" in files

        if is_maven or is_gradle:
            project_path = os.path.abspath(root)
            # Avoid processing sub-modules if parent already processed them as distinct projects
            # This simple check helps, but for complex multi-modules, might need more sophisticated discovery.
            is_sub_project = False
            for found_p in found_projects:
                if project_path.startswith(found_p + os.sep) and project_path != found_p:
                    is_sub_project = True
                    break
            if is_sub_project:
                continue

            found_projects.add(project_path)

            maven_build_path = os.path.join(project_path, MAVEN_BUILD_DIR_NAME)
            gradle_build_path = os.path.join(project_path, GRADLE_BUILD_DIR_NAME)

            # Only compare if at least one build system seems to have produced output
            # Or if both pom.xml and build.gradle exist, indicating a conversion in progress
            if (is_maven and os.path.exists(maven_build_path)) or \
               (is_gradle and os.path.exists(gradle_build_path)) or \
               (is_maven and is_gradle): # If both project files exist, attempt comparison
                results = compare_build_dirs(project_path, maven_build_path, gradle_build_path)
                all_project_results.append(results)
            else:
                print(f"\n--- Skipping Project: {project_path} ---")
                print(f"Neither '{MAVEN_BUILD_DIR_NAME}' nor '{GRADLE_BUILD_DIR_NAME}' directory found with output, or project type unclear for comparison.")


    if not all_project_results:
        print("\nNo projects found or no build directories to compare.")
        return

    # CLI Summary
    print("\n\n--- Overall Summary ---")
    for res in all_project_results:
        status = "SAME" if res["overall_match"] else "DIFFERENT"
        if not res["maven_exists"] and not res["gradle_exists"]:
            status = "MISSING_BUILD_DIRS"
        elif not res["maven_exists"]:
            status = "MISSING_MAVEN_TARGET"
        elif not res["gradle_exists"]:
            status = "MISSING_GRADLE_BUILD"
        print(f"Project: {os.path.basename(res['project_path'])} ({res['project_path']}) - Status: {status}")

    # Detailed Report Prompt
    while True:
        detailed_report_choice = input("\nDo you want a detailed report in a text file? (yes/no): ").strip().lower()
        if detailed_report_choice in ["yes", "y"]:
            generate_detailed_report(all_project_results)
            break
        elif detailed_report_choice in ["no", "n"]:
            print("Skipping detailed report.")
            break
        else:
            print("Invalid choice. Please enter 'yes' or 'no'.")


def generate_detailed_report(all_results):
    """
    Generates a detailed comparison report in a text file.
    """
    timestamp = وقت.strftime("%Y%m%d-%H%M%S")
    report_filename = f"maven_gradle_comparison_report_{timestamp}.txt"

    with open(report_filename, "w", encoding="utf-8") as f:
        f.write(f"Maven vs. Gradle Build Output Comparison Report\n")
        f.write(f"Generated: {وقت.asctime()}\n")
        f.write(f"Compared '{MAVEN_BUILD_DIR_NAME}' vs. '{GRADLE_BUILD_DIR_NAME}'\n")
        f.write("=" * 80 + "\n")

        for res in all_results:
            f.write(f"\nProject Path: {res['project_path']}\n")
            f.write("-" * 60 + "\n")

            if not res["maven_exists"] and not res["gradle_exists"]:
                f.write("  Status: Neither Maven target nor Gradle build directory found.\n")
                continue
            elif not res["maven_exists"]:
                f.write(f"  Status: Maven '{MAVEN_BUILD_DIR_NAME}' directory NOT FOUND at {res['maven_dir']}\n")
                f.write(f"  Gradle '{GRADLE_BUILD_DIR_NAME}' directory FOUND at {res['gradle_dir']}\n")
                if res.get("only_in_gradle"):
                    f.write(f"  Files only in Gradle '{GRADLE_BUILD_DIR_NAME}':\n")
                    for item in res["only_in_gradle"]:
                        f.write(f"    - {item}\n")
                continue
            elif not res["gradle_exists"]:
                f.write(f"  Status: Gradle '{GRADLE_BUILD_DIR_NAME}' directory NOT FOUND at {res['gradle_dir']}\n")
                f.write(f"  Maven '{MAVEN_BUILD_DIR_NAME}' directory FOUND at {res['maven_dir']}\n")
                if res.get("only_in_maven"):
                    f.write(f"  Files only in Maven '{MAVEN_BUILD_DIR_NAME}':\n")
                    for item in res["only_in_maven"]:
                        f.write(f"    - {item}\n")
                continue

            overall_status = "SAME (Comparable)" if res["overall_match"] else "DIFFERENT"
            f.write(f"  Overall Comparison Status: {overall_status}\n\n")

            f.write(f"  Maven Dir ('{MAVEN_BUILD_DIR_NAME}'): {res['maven_dir']}\n")
            f.write(f"  Gradle Dir ('{GRADLE_BUILD_DIR_NAME}'): {res['gradle_dir']}\n\n")

            sections = {
                "Matching Files/Items (Name & Content or Ignored Content Dir Presence)": res["matches"],
                "Mismatched Files (Same Name, Different Content - SHA256 differs)": res["mismatches"],
                f"Items Only in Maven '{MAVEN_BUILD_DIR_NAME}'": res["only_in_maven"],
                f"Items Only in Gradle '{GRADLE_BUILD_DIR_NAME}'": res["only_in_gradle"],
                "Common Top-Level Subdirectories (Existence Checked)": res["common_dirs"]
            }

            for title, items in sections.items():
                if items:
                    f.write(f"  {title}:\n")
                    if not items:
                        f.write("    - None\n")
                    for item in items:
                        f.write(f"    - {item}\n")
                    f.write("\n")
            f.write("-" * 60 + "\n")

    print(f"\nDetailed report generated: {report_filename}")


if __name__ == "__main__":
    current_directory = os.getcwd()
    print(f"Starting comparison from current directory: {current_directory}")
    print(f"Will compare contents of '{MAVEN_BUILD_DIR_NAME}/' with '{GRADLE_BUILD_DIR_NAME}/' in found projects.")
    print(f"Ignoring files: {FILES_TO_IGNORE}")
    print(f"Skipping content hash comparison for subdirectories named: {DIRECTORIES_TO_IGNORE_CONTENT_COMPARISON} (will check file presence)\n")
    find_projects_and_compare(current_directory)