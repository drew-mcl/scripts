import os
from pathlib import Path
from datetime import datetime

def find_project_roots(search_path):
    """Finds potential Maven or Gradle project roots."""
    project_roots = []
    for root, dirs, files in os.walk(search_path):
        if 'build' in dirs: dirs.remove('build')
        if 'target' in dirs: dirs.remove('target')
        if '.git' in dirs: dirs.remove('.git')
        if 'node_modules' in dirs: dirs.remove('node_modules')


        is_maven = 'pom.xml' in files
        is_gradle = 'build.gradle' in files or 'build.gradle.kts' in files

        if is_maven or is_gradle:
            project_type = []
            if is_maven: project_type.append("Maven")
            if is_gradle: project_type.append("Gradle")
            project_roots.append({'path': Path(root), 'type': '/'.join(project_type)})
    return project_roots

def determine_overall_status(results, pom_exists, gradle_build_file_exists):
    """Determines a single overall status string based on comparison results."""
    # Basic existence checks first
    if not pom_exists and not gradle_build_file_exists:
        return "Config Issue: No pom.xml or build.gradle"
    
    maven_built = results["maven_target_exists"] == "Yes"
    gradle_built = results["gradle_build_exists"] == "Yes"

    if pom_exists and not maven_built and gradle_build_file_exists and not gradle_built:
        return "Not Built (Maven & Gradle)"
    if pom_exists and not maven_built and not gradle_build_file_exists: # Only Maven project defined, not built
        return "Maven Output Missing"
    if gradle_build_file_exists and not gradle_built and not pom_exists: # Only Gradle project defined, not built
        return "Gradle Output Missing"
    if pom_exists and not maven_built:
        return "Maven Output Missing"
    if gradle_build_file_exists and not gradle_built:
        return "Gradle Output Missing"
    
    # If both are expected to be built and are present
    if pom_exists and gradle_build_file_exists and maven_built and gradle_built:
        if (results["artifact_comparison_status"] == "Match" and
            results["classes_comparison_status"] == "Match" and
            results["test_reports_status"] == "Match"):
            return "OK - Match"
        
        statuses_indicating_differences = ["Mismatch", "Partial (Size)", "Maven Only", "Gradle Only"]
        if (results["artifact_comparison_status"] in statuses_indicating_differences or
            results["classes_comparison_status"] in statuses_indicating_differences or
            results["test_reports_status"] in statuses_indicating_differences):
            return "Differences Found"
        
        # If statuses are "None Found" for artifacts/classes/tests but builds exist, it's still a difference
        if (results["artifact_comparison_status"] == "None Found" or
            results["classes_comparison_status"] == "None Found" or
            results["test_reports_status"] == "None Found"):
             # Check if any had content on the other side
            if (results["artifact_comparison_status"] == "Maven Only" or results["artifact_comparison_status"] == "Gradle Only" or
                results["classes_comparison_status"] == "Maven Only" or results["classes_comparison_status"] == "Gradle Only" or
                results["test_reports_status"] == "Maven Only" or results["test_reports_status"] == "Gradle Only"):
                return "Differences Found (Content Missing)"
            else:
                return "Outputs Seem Empty"


    return "Check Details" # Fallback for unhandled cases

def compare_outputs(project_path, maven_target_dir, gradle_build_dir):
    results = {
        "project_path": str(project_path.name),
        "full_project_path": str(project_path),
        "maven_target_exists": "N/A",
        "gradle_build_exists": "N/A",
        "artifact_comparison_status": "N/A", "artifact_details": "",
        "classes_comparison_status": "N/A", "classes_details": "",
        "test_reports_status": "N/A", "test_reports_details": "",
        "overall_notes": [],
        "overall_status": "Pending" # Will be set at the end
    }

    pom_exists = (project_path / 'pom.xml').exists()
    gradle_build_file_exists = (project_path / 'build.gradle').exists() or \
                               (project_path / 'build.gradle.kts').exists()

    results["maven_target_exists"] = "Yes" if maven_target_dir.exists() else "No"
    results["gradle_build_exists"] = "Yes" if gradle_build_dir.exists() else "No"

    # Initial check if neither build system seems to be configured
    if not pom_exists and not gradle_build_file_exists:
        results["overall_status"] = "Config Issue: No pom/gradle"
        for key in ["artifact_comparison_status", "classes_comparison_status", "test_reports_status"]:
            results[key] = "No Build Files"
        return results

    if results["maven_target_exists"] == "No" and results["gradle_build_exists"] == "No":
        if pom_exists and gradle_build_file_exists: results["overall_notes"].append("Neither 'target' nor 'build' dir found for configured project.")
        elif pom_exists: results["overall_notes"].append("Maven 'target' dir not found.")
        elif gradle_build_file_exists: results["overall_notes"].append("Gradle 'build' dir not found.")
        
        # Set N/A for all comparisons if both build outputs are missing
        status_if_unbuilt = "Not Built" if (pom_exists or gradle_build_file_exists) else "No Build Files"
        for key in ["artifact_comparison_status", "classes_comparison_status", "test_reports_status"]:
            results[key] = status_if_unbuilt
        results["overall_status"] = determine_overall_status(results, pom_exists, gradle_build_file_exists)
        return results

    # --- Artifact Comparison ---
    # (Logic from previous script, ensure it considers pom_exists and gradle_build_file_exists for relevance)
    if (maven_target_dir.exists() and pom_exists) or (gradle_build_dir.exists() and gradle_build_file_exists):
        maven_artifacts = []
        if maven_target_dir.exists() and pom_exists:
            maven_artifacts = list(maven_target_dir.glob('*.jar')) + list(maven_target_dir.glob('*.war'))
        
        gradle_artifacts = []
        gradle_libs_dir = gradle_build_dir / 'libs'
        if gradle_libs_dir.exists() and gradle_build_file_exists:
            gradle_artifacts = list(gradle_libs_dir.glob('*.jar')) + list(gradle_libs_dir.glob('*.war'))

        maven_artifact_names = sorted([a.name for a in maven_artifacts])
        gradle_artifact_names = sorted([a.name for a in gradle_artifacts])

        # Both have artifacts or one side is expected to have them
        if (maven_artifact_names and gradle_artifact_names) or \
           (maven_artifact_names and pom_exists and not gradle_build_file_exists) or \
           (gradle_artifact_names and gradle_build_file_exists and not pom_exists) or \
           (pom_exists and gradle_build_file_exists): # Both configured, compare even if one list is empty

            if maven_artifact_names and gradle_artifact_names:
                if maven_artifact_names == gradle_artifact_names:
                    results["artifact_comparison_status"] = "Match"
                    results["artifact_details"] = f"{len(maven_artifact_names)} artifact(s): {', '.join(maven_artifact_names)}"
                    # Size check (optional, can be verbose)
                    size_mismatches = []
                    for ma_name in maven_artifact_names:
                        ma = next((a for a in maven_artifacts if a.name == ma_name), None)
                        ga = next((a for a in gradle_artifacts if a.name == ma_name), None)
                        if ma and ga and ma.stat().st_size != ga.stat().st_size:
                            size_mismatches.append(f"{ma_name} (M:{ma.stat().st_size}, G:{ga.stat().st_size})")
                    if size_mismatches:
                        results["artifact_comparison_status"] = "Partial (Size)"
                        results["artifact_details"] += f" -- Size mismatches: {', '.join(size_mismatches)}"
                else:
                    results["artifact_comparison_status"] = "Mismatch"
                    results["artifact_details"] = f"Maven: {maven_artifact_names}. Gradle: {gradle_artifact_names}"
            elif maven_artifact_names and pom_exists: # Only Maven produced artifacts (and was expected to)
                results["artifact_comparison_status"] = "Maven Only"
                results["artifact_details"] = f"Maven: {maven_artifact_names}"
                if gradle_build_file_exists: results["overall_notes"].append("Gradle produced no primary artifacts.")
            elif gradle_artifact_names and gradle_build_file_exists: # Only Gradle produced artifacts
                results["artifact_comparison_status"] = "Gradle Only"
                results["artifact_details"] = f"Gradle: {gradle_artifact_names}"
                if pom_exists: results["overall_notes"].append("Maven produced no primary artifacts.")
            elif pom_exists and gradle_build_file_exists: # Both configured, neither produced artifacts
                results["artifact_comparison_status"] = "None Found (Both)"
                results["artifact_details"] = "No primary artifacts in expected locations for either."
            elif pom_exists: # Only maven configured, no artifacts
                results["artifact_comparison_status"] = "None Found (Maven)"
            elif gradle_build_file_exists: # Only gradle configured, no artifacts
                 results["artifact_comparison_status"] = "None Found (Gradle)"

        else: # Cases where one side might not be configured, so "None Found" might be expected for that side
            if pom_exists and not maven_artifacts: results["artifact_comparison_status"] = "None Found (Maven)"
            elif gradle_build_file_exists and not gradle_artifacts: results["artifact_comparison_status"] = "None Found (Gradle)"
            else: results["artifact_comparison_status"] = "N/A"

    else: # No relevant output dirs or build files for this comparison
        if pom_exists or gradle_build_file_exists: results["artifact_comparison_status"] = "Not Built"
        else: results["artifact_comparison_status"] = "No Build Files"
    
    # --- Compiled Classes Comparison --- (Similar refined logic)
    if (maven_target_dir.exists() and pom_exists) or (gradle_build_dir.exists() and gradle_build_file_exists):
        maven_classes_dir = maven_target_dir / 'classes'
        gradle_class_locs = ['java/main', 'kotlin/main', 'scala/main', 'groovy/main']
        gradle_classes_dirs_to_check = [gradle_build_dir / 'classes' / loc for loc in gradle_class_locs if (gradle_build_dir / 'classes' / loc).exists()]

        maven_classes_exist_and_relevant = maven_classes_dir.exists() and pom_exists
        gradle_classes_exist_and_relevant = bool(gradle_classes_dirs_to_check) and gradle_build_file_exists

        if maven_classes_exist_and_relevant and gradle_classes_exist_and_relevant:
            maven_class_files = set(p.relative_to(maven_classes_dir) for p in maven_classes_dir.rglob('*.class'))
            gradle_class_files_combined = set()
            for gcd in gradle_classes_dirs_to_check:
                 gradle_class_files_combined.update(p.relative_to(gcd) for p in gcd.rglob('*.class'))

            if maven_class_files == gradle_class_files_combined:
                results["classes_comparison_status"] = "Match"
                results["classes_details"] = f"{len(maven_class_files)} .class files"
            else:
                results["classes_comparison_status"] = "Mismatch"
                m_only_count = len(maven_class_files - gradle_class_files_combined)
                g_only_count = len(gradle_class_files_combined - maven_class_files)
                results["classes_details"] = (f"M-total: {len(maven_class_files)}, G-total: {len(gradle_class_files_combined)}. "
                                              f"M-only: {m_only_count}, G-only: {g_only_count}.")
        elif maven_classes_exist_and_relevant:
            results["classes_comparison_status"] = "Maven Only"
            results["classes_details"] = f"{len(list(maven_classes_dir.rglob('*.class')))} .class files"
        elif gradle_classes_exist_and_relevant:
            results["classes_comparison_status"] = "Gradle Only"
            total_gradle_classes = sum(len(list(gcd.rglob('*.class'))) for gcd in gradle_classes_dirs_to_check)
            results["classes_details"] = f"{total_gradle_classes} .class files"
        elif pom_exists and gradle_build_file_exists : # Both configured but no classes
            results["classes_comparison_status"] = "None Found (Both)"
        elif pom_exists: results["classes_comparison_status"] = "None Found (Maven)"
        elif gradle_build_file_exists: results["classes_comparison_status"] = "None Found (Gradle)"
        else: results["classes_comparison_status"] = "N/A"

    else:
        if pom_exists or gradle_build_file_exists: results["classes_comparison_status"] = "Not Built"
        else: results["classes_comparison_status"] = "No Build Files"

    # --- Test Reports Comparison --- (Similar refined logic)
    if (maven_target_dir.exists() and pom_exists) or (gradle_build_dir.exists() and gradle_build_file_exists):
        maven_test_reports_dir = maven_target_dir / 'surefire-reports'
        gradle_test_reports_dir = gradle_build_dir / 'reports' / 'tests' / 'test'

        maven_reports_exist_and_relevant = maven_test_reports_dir.exists() and pom_exists
        gradle_reports_exist_and_relevant = gradle_test_reports_dir.exists() and gradle_build_file_exists

        if maven_reports_exist_and_relevant and gradle_reports_exist_and_relevant:
            maven_test_xml_count = len(list(maven_test_reports_dir.glob('TEST-*.xml')))
            gradle_test_xml_count = len(list(gradle_test_reports_dir.glob('TEST-*.xml')))
            if maven_test_xml_count == gradle_test_xml_count and maven_test_xml_count > 0:
                results["test_reports_status"] = "Match"
                results["test_reports_details"] = f"{maven_test_xml_count} XML reports"
            elif maven_test_xml_count > 0 or gradle_test_xml_count > 0 :
                results["test_reports_status"] = "Mismatch"
                results["test_reports_details"] = f"Maven XMLs: {maven_test_xml_count}, Gradle XMLs: {gradle_test_xml_count}"
            else:
                results["test_reports_status"] = "None Found (Both)"
                results["test_reports_details"] = "No XML reports in expected locations."
        elif maven_reports_exist_and_relevant:
            results["test_reports_status"] = "Maven Only"
            results["test_reports_details"] = f"{len(list(maven_test_reports_dir.glob('TEST-*.xml')))} XML reports"
        elif gradle_reports_exist_and_relevant:
            results["test_reports_status"] = "Gradle Only"
            results["test_reports_details"] = f"{len(list(gradle_test_reports_dir.glob('TEST-*.xml')))} XML reports"
        elif pom_exists and gradle_build_file_exists:
            results["test_reports_status"] = "None Found (Both)"
        elif pom_exists: results["test_reports_status"] = "None Found (Maven)"
        elif gradle_build_file_exists: results["test_reports_status"] = "None Found (Gradle)"
        else: results["test_reports_status"] = "N/A"
    else:
        if pom_exists or gradle_build_file_exists: results["test_reports_status"] = "Not Built"
        else: results["test_reports_status"] = "No Build Files"

    results["overall_status"] = determine_overall_status(results, pom_exists, gradle_build_file_exists)
    return results


def format_results_as_table_for_file(all_project_results):
    """Formats the collected results into a string table for file output, with dynamic column widths."""
    if not all_project_results:
        return "No project data to display."

    headers = ["Project", "Full Path", "M Target", "G Build", "Artifacts", "Art. Details", "Classes", "Cls. Details", "Tests", "Test Details", "Overall Status", "Notes"]
    
    # Data mapping from result keys to header names
    field_map = {
        "Project": "project_path",
        "Full Path": "full_project_path",
        "M Target": "maven_target_exists",
        "G Build": "gradle_build_exists",
        "Artifacts": "artifact_comparison_status",
        "Art. Details": "artifact_details",
        "Classes": "classes_comparison_status",
        "Cls. Details": "classes_details",
        "Tests": "test_reports_status",
        "Test Details": "test_reports_details",
        "Overall Status": "overall_status",
        "Notes": lambda r: ", ".join(r.get("overall_notes", []))
    }

    # Prepare data for width calculation and final output
    table_data_rows = []
    for res_dict in all_project_results:
        row_data = {}
        for header_name in headers:
            data_key_or_func = field_map.get(header_name)
            raw_data_val = ""
            if callable(data_key_or_func):
                raw_data_val = data_key_or_func(res_dict)
            elif isinstance(data_key_or_func, str):
                raw_data_val = str(res_dict.get(data_key_or_func, "N/A"))
            row_data[header_name] = raw_data_val
        table_data_rows.append(row_data)

    # Calculate dynamic column widths
    col_widths = {header: len(header) for header in headers} # Initialize with header lengths
    for row_dict in table_data_rows:
        for header_name in headers:
            col_widths[header_name] = max(col_widths[header_name], len(str(row_dict.get(header_name, ""))))
    
    # Add padding
    padding = 2
    for header_name in col_widths:
        col_widths[header_name] += padding

    # Create header row string
    header_row_list = [f"{h:<{col_widths[h]}}" for h in headers]
    header_row_str = " | ".join(header_row_list)
    separator_row_str = "-+-".join("-" * col_widths[h] for h in headers)

    # Create data rows string
    data_rows_str_list = []
    for row_dict in table_data_rows:
        row_cells = [f"{str(row_dict.get(h, '')):<{col_widths[h]}}" for h in headers]
        data_rows_str_list.append(" | ".join(row_cells))

    return header_row_str + "\n" + separator_row_str + "\n" + "\n".join(data_rows_str_list)


def main():
    base_search_path_str = input("Enter the root path to search for projects (or a single project path): ")
    if not os.path.isdir(base_search_path_str):
        print(f"Error: Path '{base_search_path_str}' is not a valid directory.")
        return

    base_search_path = Path(base_search_path_str)
    all_results_data = []

    print(f"\nAnalyzing project(s) at/under: {base_search_path}")
    print("Important: Ensure projects have been built with BOTH Maven (e.g., 'mvn clean package')")
    print("and Gradle (e.g., './gradlew clean build') for meaningful comparison.")

    projects_to_analyze = []
    is_maven_at_root = (base_search_path / 'pom.xml').exists()
    is_gradle_at_root = (base_search_path / 'build.gradle').exists() or \
                        (base_search_path / 'build.gradle.kts').exists()

    if is_maven_at_root or is_gradle_at_root:
        projects_to_analyze.append({'path': base_search_path, 'type': 'root', 'name': base_search_path.name})

    scan_recursively_input = input("Scan recursively for projects within subdirectories? (y/n): ").strip().lower()
    if scan_recursively_input == 'y':
        print(f"\nScanning recursively under '{base_search_path}'...")
        discovered_projects = find_project_roots(base_search_path)
        for dp_info in discovered_projects:
            # Add if not already added as root, or if it's a distinct module
            if dp_info['path'] != base_search_path or not (is_maven_at_root or is_gradle_at_root) :
                 projects_to_analyze.append({'path': dp_info['path'], 'type': dp_info['type'], 'name': dp_info['path'].name})
        # Remove duplicates if any (e.g. root was also found by recursive scan)
        unique_projects = []
        seen_paths = set()
        for p in projects_to_analyze:
            if p['path'] not in seen_paths:
                unique_projects.append(p)
                seen_paths.add(p['path'])
        projects_to_analyze = unique_projects


    if not projects_to_analyze:
        print(f"No Maven or Gradle projects found to analyze at or under '{base_search_path}'.")
        return
    
    print(f"\n--- Processing {len(projects_to_analyze)} project(s) ---")
    for proj_info in projects_to_analyze:
        proj_path = proj_info['path']
        proj_name = proj_info.get('name', proj_path.name) # Use name if available

        maven_target_dir = proj_path / 'target'
        gradle_build_dir = proj_path / 'build'
        
        comparison_data = compare_outputs(proj_path, maven_target_dir, gradle_build_dir)
        all_results_data.append(comparison_data)
        
        # Print concise status to terminal during processing
        print(f"Module: {proj_name:<40} | Status: {comparison_data['overall_status']}")

    if not all_results_data:
        print("\nNo project data was collected to generate a summary.")
        return

    # --- Output to text file (verbose table) ---
    save_to_file = input("\nSave full comparison details to a text file? (y/n): ").strip().lower()
    if save_to_file == 'y':
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_filename = f"build_comparison_summary_{timestamp}.txt"
        output_filename = input(f"Enter filename (default: {default_filename}): ").strip()
        if not output_filename: output_filename = default_filename
        
        try:
            with open(output_filename, 'w', encoding='utf-8') as f:
                f.write(f"Build Comparison Full Summary - Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"Searched Path: {base_search_path_str}\n\n")
                verbose_summary_table_str = format_results_as_table_for_file(all_results_data)
                f.write(verbose_summary_table_str)
            print(f"Full summary saved to '{output_filename}'")
        except IOError as e:
            print(f"Error saving file: {e}")
    else:
        print("Report not saved to file.")

if __name__ == '__main__':
    main()