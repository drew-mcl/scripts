import os
from pathlib import Path
from datetime import datetime

def find_project_roots(search_path):
    """Finds potential Maven or Gradle project roots.
       A directory is considered a potential project root if it contains
       a pom.xml or a build.gradle/build.gradle.kts file.
       The decision to analyze it further (e.g. requiring both) is made later.
    """
    project_roots = []
    for root, dirs, files in os.walk(search_path):
        # Prune common directories to speed up scanning and avoid irrelevant matches
        for d in ['.git', 'node_modules', 'target', 'build', '.idea', '.vscode', '__pycache__']:
            if d in dirs:
                dirs.remove(d)

        is_maven = 'pom.xml' in files
        is_gradle = 'build.gradle' in files or 'build.gradle.kts' in files

        if is_maven or is_gradle: # Found a directory that's at least one type
            project_type_details = []
            if is_maven: project_type_details.append("pom.xml")
            if 'build.gradle' in files: project_type_details.append("build.gradle")
            if 'build.gradle.kts' in files: project_type_details.append("build.gradle.kts")
            
            project_roots.append({
                'path': Path(root),
                'has_maven': is_maven,
                'has_gradle': is_gradle,
                'files_found': project_type_details
            })
    return project_roots

def determine_overall_status(results, pom_exists, gradle_build_file_exists):
    """Determines a single overall status string based on comparison results."""
    # This function assumes that pom_exists and gradle_build_file_exists are TRUE
    # for any project that reaches this stage, due to pre-filtering.

    maven_built = results["maven_target_exists"] == "Yes"
    gradle_built = results["gradle_build_exists"] == "Yes"

    if not maven_built and not gradle_built:
        return "Not Built (Maven & Gradle)"
    if not maven_built:
        return "Maven Output Missing"
    if not gradle_built:
        return "Gradle Output Missing"
    
    # If both are built
    if (results["artifact_comparison_status"] == "Match" and
        results["classes_comparison_status"] == "Match" and
        results["test_reports_status"] == "Match"):
        # Further check for artifact size mismatches if primary status is Match
        if "Partial (Size)" in results["artifact_details"]: # Check original detail string
             return "OK (Artifact Size Differs)"
        return "OK - Match"
    
    statuses_indicating_differences = ["Mismatch", "Partial (Size)", "Maven Only", "Gradle Only"]
    if (results["artifact_comparison_status"] in statuses_indicating_differences or
        results["classes_comparison_status"] in statuses_indicating_differences or
        results["test_reports_status"] in statuses_indicating_differences):
        return "Differences Found"
    
    if (results["artifact_comparison_status"] == "None Found (Both)" or
        results["classes_comparison_status"] == "None Found (Both)" or
        results["test_reports_status"] == "None Found (Both)"):
        return "Outputs Seem Empty"

    return "Check Details" # Fallback

def compare_outputs(project_path, maven_target_dir, gradle_build_dir):
    results = {
        "project_path": str(project_path.name),
        "full_project_path": str(project_path),
        "maven_target_exists": "No", # Default to No, update if found
        "gradle_build_exists": "No", # Default to No, update if found
        "artifact_comparison_status": "N/A", "artifact_details": "",
        "classes_comparison_status": "N/A", "classes_details": "",
        "test_reports_status": "N/A", "test_reports_details": "",
        "overall_notes": [],
        "overall_status": "Pending"
    }

    # These are guaranteed by the calling function's filter
    pom_exists = True 
    gradle_build_file_exists = True

    results["maven_target_exists"] = "Yes" if maven_target_dir.exists() else "No"
    results["gradle_build_exists"] = "Yes" if gradle_build_dir.exists() else "No"

    if results["maven_target_exists"] == "No" and results["gradle_build_exists"] == "No":
        results["overall_notes"].append("Neither 'target' nor 'build' dir found.")
        for key in ["artifact_comparison_status", "classes_comparison_status", "test_reports_status"]:
            results[key] = "Not Built"
        results["overall_status"] = determine_overall_status(results, pom_exists, gradle_build_file_exists)
        return results

    # --- Artifact Comparison ---
    maven_artifacts = []
    if maven_target_dir.exists(): # pom_exists is True
        maven_artifacts = list(maven_target_dir.glob('*.jar')) + list(maven_target_dir.glob('*.war'))
    
    gradle_artifacts = []
    gradle_libs_dir = gradle_build_dir / 'libs'
    if gradle_libs_dir.exists(): # gradle_build_file_exists is True
        gradle_artifacts = list(gradle_libs_dir.glob('*.jar')) + list(gradle_libs_dir.glob('*.war'))

    maven_artifact_names = sorted([a.name for a in maven_artifacts])
    gradle_artifact_names = sorted([a.name for a in gradle_artifacts])

    if maven_artifact_names and gradle_artifact_names:
        if maven_artifact_names == gradle_artifact_names:
            results["artifact_comparison_status"] = "Match"
            results["artifact_details"] = f"{len(maven_artifact_names)} artifact(s): {', '.join(maven_artifact_names)}"
            size_mismatches = []
            for ma_name in maven_artifact_names:
                ma = next((a for a in maven_artifacts if a.name == ma_name), None)
                ga = next((a for a in gradle_artifacts if a.name == ma_name), None)
                if ma and ga and ma.stat().st_size != ga.stat().st_size:
                    size_mismatches.append(f"{ma_name} (M:{ma.stat().st_size}, G:{ga.stat().st_size})")
            if size_mismatches:
                results["artifact_comparison_status"] = "Partial (Size)" # Status indicates size issue
                results["artifact_details"] += f" -- Size mismatches: {', '.join(size_mismatches)}" # Detail includes names
        else:
            results["artifact_comparison_status"] = "Mismatch"
            results["artifact_details"] = f"Maven: {maven_artifact_names}. Gradle: {gradle_artifact_names}"
    elif maven_artifact_names: # Maven has, Gradle does not (but Gradle build file exists)
        results["artifact_comparison_status"] = "Maven Only"
        results["artifact_details"] = f"Maven: {maven_artifact_names}"
        results["overall_notes"].append("Gradle produced no primary artifacts.")
    elif gradle_artifact_names: # Gradle has, Maven does not
        results["artifact_comparison_status"] = "Gradle Only"
        results["artifact_details"] = f"Gradle: {gradle_artifact_names}"
        results["overall_notes"].append("Maven produced no primary artifacts.")
    else: # Neither has artifacts, despite both build files present
        results["artifact_comparison_status"] = "None Found (Both)"
        results["artifact_details"] = "No primary artifacts in expected locations for either."

    # --- Compiled Classes Comparison ---
    maven_classes_dir = maven_target_dir / 'classes'
    gradle_class_locs = ['java/main', 'kotlin/main', 'scala/main', 'groovy/main'] # Common locations
    gradle_classes_dirs_to_check = [gradle_build_dir / 'classes' / loc for loc in gradle_class_locs if (gradle_build_dir / 'classes' / loc).exists()]

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
            m_only_count = len(maven_class_files - gradle_class_files_combined)
            g_only_count = len(gradle_class_files_combined - maven_class_files)
            results["classes_details"] = (f"M-total: {len(maven_class_files)}, G-total: {len(gradle_class_files_combined)}. "
                                          f"M-only: {m_only_count}, G-only: {g_only_count}.")
    elif maven_classes_exist:
        results["classes_comparison_status"] = "Maven Only"
        results["classes_details"] = f"{len(list(maven_classes_dir.rglob('*.class')))} .class files"
    elif gradle_classes_exist:
        results["classes_comparison_status"] = "Gradle Only"
        total_gradle_classes = sum(len(list(gcd.rglob('*.class'))) for gcd in gradle_classes_dirs_to_check)
        results["classes_details"] = f"{total_gradle_classes} .class files"
    else: # Neither has classes
        results["classes_comparison_status"] = "None Found (Both)"

    # --- Test Reports Comparison ---
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
        elif maven_test_xml_count > 0 or gradle_test_xml_count > 0 :
            results["test_reports_status"] = "Mismatch"
            results["test_reports_details"] = f"Maven XMLs: {maven_test_xml_count}, Gradle XMLs: {gradle_test_xml_count}"
        else:
            results["test_reports_status"] = "None Found (Both)" # Dirs exist, no XMLs
            results["test_reports_details"] = "No XML reports in expected locations."
    elif maven_reports_exist:
        results["test_reports_status"] = "Maven Only"
        results["test_reports_details"] = f"{len(list(maven_test_reports_dir.glob('TEST-*.xml')))} XML reports"
    elif gradle_reports_exist:
        results["test_reports_status"] = "Gradle Only"
        results["test_reports_details"] = f"{len(list(gradle_test_reports_dir.glob('TEST-*.xml')))} XML reports"
    else: # Neither report dir found
        results["test_reports_status"] = "None Found (Both)"

    results["overall_status"] = determine_overall_status(results, pom_exists, gradle_build_file_exists)
    return results


def format_results_as_table_for_file(all_project_results):
    """Formats the collected results into a string table for file output, with dynamic column widths."""
    if not all_project_results:
        return "No project data to display."

    headers = ["Project", "Full Path", "M Target", "G Build", "Artifacts", "Art. Details", "Classes", "Cls. Details", "Tests", "Test Details", "Overall Status", "Notes"]
    
    field_map = {
        "Project": "project_path", "Full Path": "full_project_path",
        "M Target": "maven_target_exists", "G Build": "gradle_build_exists",
        "Artifacts": "artifact_comparison_status", "Art. Details": "artifact_details",
        "Classes": "classes_comparison_status", "Cls. Details": "classes_details",
        "Tests": "test_reports_status", "Test Details": "test_reports_details",
        "Overall Status": "overall_status",
        "Notes": lambda r: ", ".join(r.get("overall_notes", []))
    }

    table_data_rows = []
    for res_dict in all_project_results:
        row_data = {header_name: (field_map[header_name](res_dict) if callable(field_map[header_name])
                                 else str(res_dict.get(field_map[header_name], "N/A")))
                    for header_name in headers}
        table_data_rows.append(row_data)

    col_widths = {header: len(header) for header in headers}
    for row_dict in table_data_rows:
        for header_name in headers:
            col_widths[header_name] = max(col_widths[header_name], len(str(row_dict.get(header_name, ""))))
    
    padding = 2
    for header_name in col_widths: col_widths[header_name] += padding

    header_row_list = [f"{h:<{col_widths[h]}}" for h in headers]
    header_row_str = " | ".join(header_row_list)
    separator_row_str = "-+-".join("-" * col_widths[h] for h in headers)

    data_rows_str_list = [
        " | ".join([f"{str(row_dict.get(h, '')):<{col_widths[h]}}" for h in headers])
        for row_dict in table_data_rows
    ]

    return header_row_str + "\n" + separator_row_str + "\n" + "\n".join(data_rows_str_list)


def main():
    base_search_path_str = input("Enter the root path to search for projects: ")
    if not os.path.isdir(base_search_path_str):
        print(f"Error: Path '{base_search_path_str}' is not a valid directory.")
        return

    base_search_path = Path(base_search_path_str)
    all_results_data = []
    
    print(f"\nAnalyzing projects under: {base_search_path}")
    print("Only processing modules that contain BOTH a Maven (pom.xml) and a Gradle (build.gradle/kts) file.")
    print("Important: Ensure such modules have been built with BOTH systems for meaningful comparison.")

    # --- Identify projects to analyze ---
    candidate_projects = []
    # Check the base path itself
    base_has_maven = (base_search_path / 'pom.xml').exists()
    base_has_gradle = (base_search_path / 'build.gradle').exists() or \
                      (base_search_path / 'build.gradle.kts').exists()
    if base_has_maven and base_has_gradle:
        candidate_projects.append({'path': base_search_path, 'name': base_search_path.name, 'is_root': True})
        print(f"Found candidate at root: {base_search_path.name}")

    # Scan recursively if requested
    scan_recursively_input = input("Scan recursively for projects within subdirectories? (y/n): ").strip().lower()
    if scan_recursively_input == 'y':
        print(f"\nScanning recursively under '{base_search_path}'...")
        discovered_roots = find_project_roots(base_search_path)
        for proj_info in discovered_roots:
            # Skip if it's the root path and already added
            if proj_info['path'] == base_search_path and any(p['is_root'] for p in candidate_projects if 'is_root' in p):
                continue

            if proj_info['has_maven'] and proj_info['has_gradle']:
                # Avoid adding duplicates if find_project_roots somehow returns nested paths
                # that are already covered by a higher-level candidate (unlikely with current find_project_roots)
                is_already_candidate_or_subpath = False
                for cp in candidate_projects:
                    if proj_info['path'] == cp['path'] or proj_info['path'].is_relative_to(cp['path']):
                        is_already_candidate_or_subpath = True
                        break
                if not is_already_candidate_or_subpath:
                     candidate_projects.append({'path': proj_info['path'], 'name': proj_info['path'].name})
                     print(f"Found candidate: {proj_info['path'].name} (at ./{proj_info['path'].relative_to(base_search_path)})")
            else:
                print(f"  Skipping: {proj_info['path'].name} (at ./{proj_info['path'].relative_to(base_search_path)}) - does not have both pom.xml and build.gradle/kts. Found: {', '.join(proj_info['files_found'])}")
    
    # Remove duplicates just in case (e.g., if root was added and also found by a shallow recursive scan)
    projects_to_analyze = []
    seen_paths = set()
    for p_info in candidate_projects:
        if p_info['path'] not in seen_paths:
            projects_to_analyze.append(p_info)
            seen_paths.add(p_info['path'])

    if not projects_to_analyze:
        print(f"\nNo modules found under '{base_search_path}' that contain both Maven and Gradle build files.")
        return
    
    print(f"\n--- Processing {len(projects_to_analyze)} identified project(s) ---")
    for proj_info in projects_to_analyze:
        proj_path = proj_info['path']
        proj_name = proj_info.get('name', proj_path.name)

        maven_target_dir = proj_path / 'target'
        gradle_build_dir = proj_path / 'build'
        
        comparison_data = compare_outputs(proj_path, maven_target_dir, gradle_build_dir)
        all_results_data.append(comparison_data)
        
        print(f"Module: {proj_name:<40} | Status: {comparison_data['overall_status']}")

    if not all_results_data:
        print("\nNo data collected from analyzed projects.") # Should not happen if projects_to_analyze was > 0
        return

    save_to_file = input("\nSave full comparison details to a text file? (y/n): ").strip().lower()
    if save_to_file == 'y':
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_filename = f"build_comparison_summary_{timestamp}.txt"
        output_filename = input(f"Enter filename (default: {default_filename}): ").strip()
        if not output_filename: output_filename = default_filename
        
        try:
            with open(output_filename, 'w', encoding='utf-8') as f:
                f.write(f"Build Comparison Summary - Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"Searched Path: {base_search_path_str}\n")
                f.write(f"Filtered for modules with both pom.xml and build.gradle/kts.\n\n")
                file_table_str = format_results_as_table_for_file(all_results_data)
                f.write(file_table_str)
            print(f"Full summary saved to '{output_filename}'")
        except IOError as e:
            print(f"Error saving file: {e}")
    else:
        print("Report not saved to file.")

if __name__ == '__main__':
    main()