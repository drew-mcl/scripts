import os
import filecmp
from pathlib import Path
from datetime import datetime # For timestamping output file

def find_project_roots(search_path):
    """Finds potential Maven or Gradle project roots."""
    project_roots = []
    for root, dirs, files in os.walk(search_path):
        # Avoid descending into build/target directories themselves for project root detection
        if 'build' in dirs:
            dirs.remove('build')
        if 'target' in dirs:
            dirs.remove('target')

        is_maven = 'pom.xml' in files
        is_gradle = 'build.gradle' in files or 'build.gradle.kts' in files

        if is_maven or is_gradle:
            # Determine type. If both, prefer to list it once, maybe as 'maven_gradle_migrating'
            # For simplicity here, we'll just note its path. The comparison logic
            # will then check for both target and build dirs.
            project_type = []
            if is_maven:
                project_type.append("Maven")
            if is_gradle:
                project_type.append("Gradle")
            project_roots.append({'path': Path(root), 'type': '/'.join(project_type)})
    return project_roots

def compare_outputs(project_path, maven_target_dir, gradle_build_dir):
    """
    Compares the contents of Maven 'target' and Gradle 'build' directories.
    Returns a dictionary with structured comparison results.
    """
    results = {
        "project_path": str(project_path),
        "maven_target_exists": "N/A",
        "gradle_build_exists": "N/A",
        "artifact_comparison_status": "N/A",
        "artifact_details": "",
        "classes_comparison_status": "N/A",
        "classes_details": "",
        "test_reports_status": "N/A",
        "test_reports_details": "",
        "overall_notes": []
    }

    results["maven_target_exists"] = "Yes" if maven_target_dir.exists() else "No"
    results["gradle_build_exists"] = "Yes" if gradle_build_dir.exists() else "No"

    if not maven_target_dir.exists() and not gradle_build_dir.exists():
        results["overall_notes"].append("Neither 'target' nor 'build' directory found.")
        return results
    if not maven_target_dir.exists():
        results["overall_notes"].append("Maven 'target' directory not found.")
        # Set N/A for comparisons if one side is missing, or indicate "Gradle Only"
    if not gradle_build_dir.exists():
        results["overall_notes"].append("Gradle 'build' directory not found.")
        # Set N/A for comparisons if one side is missing, or indicate "Maven Only"

    # 1. Compare primary artifacts (JARs, WARs)
    if maven_target_dir.exists() or gradle_build_dir.exists(): # Proceed if at least one exists
        maven_artifacts = []
        if maven_target_dir.exists():
            maven_artifacts = list(maven_target_dir.glob('*.jar')) + list(maven_target_dir.glob('*.war'))
        
        gradle_artifacts = []
        gradle_libs_dir = gradle_build_dir / 'libs'
        if gradle_libs_dir.exists():
            gradle_artifacts = list(gradle_libs_dir.glob('*.jar')) + list(gradle_libs_dir.glob('*.war'))

        maven_artifact_names = sorted([a.name for a in maven_artifacts])
        gradle_artifact_names = sorted([a.name for a in gradle_artifacts])

        if maven_artifact_names and gradle_artifact_names:
            if maven_artifact_names == gradle_artifact_names:
                results["artifact_comparison_status"] = "Match"
                results["artifact_details"] = f"{len(maven_artifact_names)} artifact(s): {', '.join(maven_artifact_names)}"
                # Size check
                size_mismatches = []
                for ma_name in maven_artifact_names:
                    ma = next((a for a in maven_artifacts if a.name == ma_name), None)
                    ga = next((a for a in gradle_artifacts if a.name == ma_name), None)
                    if ma and ga and ma.stat().st_size != ga.stat().st_size:
                        size_mismatches.append(f"{ma_name} (M:{ma.stat().st_size}, G:{ga.stat().st_size})")
                if size_mismatches:
                    results["artifact_comparison_status"] = "Partial Match (Size Diff)"
                    results["artifact_details"] += f" Size mismatches: {', '.join(size_mismatches)}"
            else:
                results["artifact_comparison_status"] = "Mismatch"
                results["artifact_details"] = f"Maven: {maven_artifact_names}, Gradle: {gradle_artifact_names}"
        elif maven_artifact_names:
            results["artifact_comparison_status"] = "Maven Only"
            results["artifact_details"] = f"Maven: {maven_artifact_names}"
        elif gradle_artifact_names:
            results["artifact_comparison_status"] = "Gradle Only"
            results["artifact_details"] = f"Gradle: {gradle_artifact_names}"
        else:
            results["artifact_comparison_status"] = "None Found"
            results["artifact_details"] = "No primary artifacts in expected locations."
    else:
        results["artifact_comparison_status"] = "N/A (No build/target dirs)"


    # 2. Compare compiled classes
    if maven_target_dir.exists() or gradle_build_dir.exists():
        maven_classes_dir = maven_target_dir / 'classes'
        gradle_java_classes_dir = gradle_build_dir / 'classes' / 'java' / 'main'
        gradle_kotlin_classes_dir = gradle_build_dir / 'classes' / 'kotlin' / 'main'
        gradle_classes_dirs_to_check = [d for d in [gradle_java_classes_dir, gradle_kotlin_classes_dir] if d.exists()]

        maven_classes_exist = maven_classes_dir.exists()
        gradle_classes_exist = bool(gradle_classes_dirs_to_check)

        if maven_classes_exist and gradle_classes_exist:
            maven_class_files = set(p.relative_to(maven_classes_dir) for p in maven_classes_dir.rglob('*.class'))
            gradle_class_files_combined = set()
            for gcd in gradle_classes_dirs_to_check:
                 gradle_class_files_combined.update(p.relative_to(gcd) for p in gcd.rglob('*.class'))

            if maven_class_files == gradle_class_files_combined:
                results["classes_comparison_status"] = "Match"
                results["classes_details"] = f"{len(maven_class_files)} .class files"
            else:
                results["classes_comparison_status"] = "Mismatch"
                m_only = len(maven_class_files - gradle_class_files_combined)
                g_only = len(gradle_class_files_combined - maven_class_files)
                results["classes_details"] = f"M-only: {m_only}, G-only: {g_only}. Total M: {len(maven_class_files)}, G: {len(gradle_class_files_combined)}"
        elif maven_classes_exist:
            results["classes_comparison_status"] = "Maven Only"
            results["classes_details"] = f"{len(list(maven_classes_dir.rglob('*.class')))} .class files"
        elif gradle_classes_exist:
            results["classes_comparison_status"] = "Gradle Only"
            total_gradle_classes = 0
            for gcd in gradle_classes_dirs_to_check:
                total_gradle_classes += len(list(gcd.rglob('*.class')))
            results["classes_details"] = f"{total_gradle_classes} .class files"
        else:
            results["classes_comparison_status"] = "None Found"
    else:
        results["classes_comparison_status"] = "N/A (No build/target dirs)"


    # 3. Compare test reports
    if maven_target_dir.exists() or gradle_build_dir.exists():
        maven_test_reports_dir = maven_target_dir / 'surefire-reports'
        gradle_test_reports_dir = gradle_build_dir / 'reports' / 'tests' / 'test'

        maven_reports_exist = maven_test_reports_dir.exists()
        gradle_reports_exist = gradle_test_reports_dir.exists()

        if maven_reports_exist and gradle_reports_exist:
            maven_test_xml_count = len(list(maven_test_reports_dir.glob('TEST-*.xml')))
            gradle_test_xml_count = len(list(gradle_test_reports_dir.glob('TEST-*.xml')))
            if maven_test_xml_count == gradle_test_xml_count and maven_test_xml_count > 0:
                results["test_reports_status"] = "Match"
                results["test_reports_details"] = f"{maven_test_xml_count} XML reports"
            elif maven_test_xml_count > 0 or gradle_test_xml_count > 0:
                results["test_reports_status"] = "Mismatch"
                results["test_reports_details"] = f"Maven XMLs: {maven_test_xml_count}, Gradle XMLs: {gradle_test_xml_count}"
            else:
                results["test_reports_status"] = "None Found"
                results["test_reports_details"] = "No XML reports in expected locations."
        elif maven_reports_exist:
            results["test_reports_status"] = "Maven Only"
            results["test_reports_details"] = f"{len(list(maven_test_reports_dir.glob('TEST-*.xml')))} XML reports"
        elif gradle_reports_exist:
            results["test_reports_status"] = "Gradle Only"
            results["test_reports_details"] = f"{len(list(gradle_test_reports_dir.glob('TEST-*.xml')))} XML reports"
        else:
            results["test_reports_status"] = "None Found"
    else:
        results["test_reports_status"] = "N/A (No build/target dirs)"

    return results

def format_results_as_table(all_project_results):
    """Formats the collected results into a string table."""
    if not all_project_results:
        return "No project data to display."

    # Define column headers and their typical max widths (can be dynamic)
    headers = ["Project Path", "Maven Target", "Gradle Build", "Artifacts", "Artifact Details", "Classes", "Classes Details", "Test Reports", "Test Report Details", "Notes"]
    # Estimate column widths (can be calculated based on data for perfect fit)
    # For simplicity, using fixed widths or basing on header length + some padding
    col_widths = {
        "Project Path": 40,
        "Maven Target": 12,
        "Gradle Build": 12,
        "Artifacts": 25, # Status
        "Artifact Details": 30,
        "Classes": 15,   # Status
        "Classes Details": 30,
        "Test Reports": 15, # Status
        "Test Report Details": 30,
        "Notes": 40
    }

    # Create header row
    header_row = " | ".join(f"{h:<{col_widths.get(h, len(h))}}" for h in headers)
    separator_row = "-+-".join("-" * col_widths.get(h, len(h)) for h in headers)

    table_str = header_row + "\n" + separator_row + "\n"

    # Create data rows
    for res in all_project_results:
        row_data = [
            str(res.get("project_path", "N/A"))[:col_widths["Project Path"]-1], # Truncate if too long
            str(res.get("maven_target_exists", "N/A")),
            str(res.get("gradle_build_exists", "N/A")),
            str(res.get("artifact_comparison_status", "N/A")),
            str(res.get("artifact_details", ""))[:col_widths["Artifact Details"]-1],
            str(res.get("classes_comparison_status", "N/A")),
            str(res.get("classes_details", ""))[:col_widths["Classes Details"]-1],
            str(res.get("test_reports_status", "N/A")),
            str(res.get("test_reports_details", ""))[:col_widths["Test Report Details"]-1],
            (", ".join(res.get("overall_notes", [])))[:col_widths["Notes"]-1]
        ]
        table_str += " | ".join(f"{str(data):<{col_widths.get(headers[i], len(str(data)))}}" for i, data in enumerate(row_data)) + "\n"

    return table_str

def main():
    base_search_path_str = input("Enter the root path to search for projects (or a single project path): ")
    if not os.path.isdir(base_search_path_str):
        print(f"Error: Path '{base_search_path_str}' is not a valid directory.")
        return

    base_search_path = Path(base_search_path_str)
    all_results = []

    # Check if the base path itself is a project
    is_maven_at_root = (base_search_path / 'pom.xml').exists()
    is_gradle_at_root = (base_search_path / 'build.gradle').exists() or \
                        (base_search_path / 'build.gradle.kts').exists()

    if is_maven_at_root or is_gradle_at_root:
        print(f"Analyzing project directly at: {base_search_path}")
        maven_target_dir = base_search_path / 'target'
        gradle_build_dir = base_search_path / 'build'

        if not maven_target_dir.exists() and not gradle_build_dir.exists():
             print(f"  WARNING: Neither 'target' (Maven) nor 'build' (Gradle) directory exists in {base_search_path}.")
             print("  Ensure you have built the project with both Maven (e.g., 'mvn clean package') and")
             print("  Gradle (e.g., './gradlew clean build') for a meaningful comparison.")
        elif not maven_target_dir.exists():
            print(f"  INFO: Maven 'target' directory not found in {base_search_path}. Run Maven build.")
        elif not gradle_build_dir.exists():
            print(f"  INFO: Gradle 'build' directory not found in {base_search_path}. Run Gradle build.")

        # Even if one is missing, we can report on what's present
        project_comparison_data = compare_outputs(base_search_path, maven_target_dir, gradle_build_dir)
        all_results.append(project_comparison_data)
        # Print intermediate summary for this one project
        print(f"  Artifacts: {project_comparison_data['artifact_comparison_status']} - {project_comparison_data['artifact_details']}")
        print(f"  Classes: {project_comparison_data['classes_comparison_status']} - {project_comparison_data['classes_details']}")
        print(f"  Test Reports: {project_comparison_data['test_reports_status']} - {project_comparison_data['test_reports_details']}")
        for note in project_comparison_data['overall_notes']:
            print(f"  Note: {note}")


    # Option to also scan for sub-projects/modules
    scan_recursively = input("Do you want to scan recursively for projects within subdirectories? (y/n): ").strip().lower()
    if scan_recursively == 'y':
        print(f"\nScanning for projects recursively under '{base_search_path}'...")
        # Exclude the root if it was already processed to avoid double counting if it's also a module container
        # This simple check might need refinement for complex multi-module setups.
        discovered_projects = [p for p in find_project_roots(base_search_path) if p['path'] != base_search_path]

        if not discovered_projects and not all_results: # if root wasn't a project and no sub-projects found
            print(f"No Maven or Gradle projects found under '{base_search_path}'.")
            return
        elif not discovered_projects and all_results: # if root was a project but no sub-projects
             print(f"No additional sub-projects found under '{base_search_path}'.")
        else:
            print(f"Found {len(discovered_projects)} potential sub-project(s).")


        for proj_info in discovered_projects:
            proj_path = proj_info['path']
            print(f"\nAnalyzing project at: {proj_path} (Type: {proj_info['type']})")

            maven_target_dir = proj_path / 'target'
            gradle_build_dir = proj_path / 'build'

            if not maven_target_dir.exists() and not gradle_build_dir.exists():
                print(f"  WARNING: Neither 'target' nor 'build' directory exists in {proj_path}.")
                print("  Build with both Maven and Gradle for comparison.")
            elif not maven_target_dir.exists() and (proj_path / 'pom.xml').exists():
                 print(f"  INFO: Maven 'target' directory not found in {proj_path}. Run Maven build.")
            elif not gradle_build_dir.exists() and \
                 ((proj_path / 'build.gradle').exists() or (proj_path / 'build.gradle.kts').exists()):
                 print(f"  INFO: Gradle 'build' directory not found in {proj_path}. Run Gradle build.")

            comparison_data = compare_outputs(proj_path, maven_target_dir, gradle_build_dir)
            all_results.append(comparison_data)
            # Print intermediate summary
            print(f"  Artifacts: {comparison_data['artifact_comparison_status']} - {comparison_data['artifact_details']}")
            print(f"  Classes: {comparison_data['classes_comparison_status']} - {comparison_data['classes_details']}")
            print(f"  Test Reports: {comparison_data['test_reports_status']} - {comparison_data['test_reports_details']}")
            for note in comparison_data['overall_notes']:
                print(f"  Note: {note}")

    if not all_results:
        print("No project data was collected to generate a summary table.")
        return

    # --- Generate and print summary table ---
    print("\n\n--- Build Comparison Summary ---")
    summary_table_str = format_results_as_table(all_results)
    print(summary_table_str)

    # --- Output to text file ---
    save_to_file = input("Save this summary to a text file? (y/n): ").strip().lower()
    if save_to_file == 'y':
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_filename = f"build_comparison_summary_{timestamp}.txt"
        output_filename = input(f"Enter filename (default: {default_filename}): ").strip()
        if not output_filename:
            output_filename = default_filename
        
        try:
            with open(output_filename, 'w') as f:
                f.write(f"Build Comparison Summary - Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"Searched Path: {base_search_path_str}\n\n")
                f.write(summary_table_str)
            print(f"Summary saved to '{output_filename}'")
        except IOError as e:
            print(f"Error saving file: {e}")

if __name__ == '__main__':
    main()