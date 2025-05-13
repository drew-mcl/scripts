import os
from pathlib import Path
from datetime import datetime
import zipfile

def find_project_roots(search_path):
    """Finds project roots containing BOTH pom.xml and build.gradle[.kts]."""
    project_roots_to_analyze = []
    abs_search_path = Path(search_path).resolve()
    # Use a set to keep track of paths we've decided to add or skip, to avoid redundant checks/messages.
    handled_paths = set()

    for root, dirs, files in os.walk(abs_search_path, topdown=True):
        current_path = Path(root).resolve()

        if current_path in handled_paths: # If already handled (e.g. as a root that was also walked), skip deeper walk from here.
            dirs[:] = [] # Don't descend further from an already handled path
            continue

        # Prune common directories to speed up and avoid irrelevant checks
        original_dirs_len = len(dirs)
        dirs[:] = [d for d in dirs if d not in ['.git', '.svn', '.hg', 'node_modules', 'target', 'build', '__pycache__', '.venv', 'venv', 'dist', 'out', '.idea', '.vscode']]
        
        has_pom = 'pom.xml' in files
        has_gradle = 'build.gradle.kts' in files or 'build.gradle' in files

        if has_pom and has_gradle:
            project_roots_to_analyze.append({
                'path': current_path,
                'name': current_path.name
            })
            handled_paths.add(current_path)
        elif (has_pom or has_gradle): # Has one but not both
            # Only print skip message if we haven't already handled/skipped this path.
            # This is mainly for the top-level or significant sub-modules.
            # Don't print for every single directory that might have one build file.
            # Heuristic: if it's the search path itself, or a direct child of a processed/search path.
            if current_path == abs_search_path or current_path.parent in handled_paths or current_path.parent == abs_search_path :
                 print(f"Skipping: {current_path.name} (at {current_path}) - requires both pom.xml and build.gradle[.kts].")
            handled_paths.add(current_path) # Mark as handled so we don't re-evaluate or print again

    # If the initial search_path itself was a candidate but skipped, the message would be printed above.
    # If it was a valid project, it would be added.
    return project_roots_to_analyze


def compare_archive_contents(maven_archive_path, gradle_archive_path):
    """Compares internal files of two archives, distinguishing core vs. maven metadata."""
    comparison = {
        "core_match": False,
        "maven_only_core_files": set(),
        "gradle_only_core_files": set(),
        "maven_only_metadata_files": set(),
        "gradle_only_metadata_files": set(),
        "error": None,
        "maven_core_files_count": 0,
        "gradle_core_files_count": 0,
        "maven_metadata_files_count": 0,
        "gradle_metadata_files_count": 0
    }
    try:
        with zipfile.ZipFile(maven_archive_path, 'r') as maven_zip, \
             zipfile.ZipFile(gradle_archive_path, 'r') as gradle_zip:

            maven_all_files = set(info.filename for info in maven_zip.infolist() if not info.is_dir())
            gradle_all_files = set(info.filename for info in gradle_zip.infolist() if not info.is_dir())

            maven_core_files = {f for f in maven_all_files if not f.startswith("META-INF/maven/")}
            gradle_core_files = {f for f in gradle_all_files if not f.startswith("META-INF/maven/")}

            maven_metadata_files = maven_all_files - maven_core_files
            gradle_metadata_files = gradle_all_files - gradle_core_files

            comparison["maven_core_files_count"] = len(maven_core_files)
            comparison["gradle_core_files_count"] = len(gradle_core_files)
            comparison["maven_metadata_files_count"] = len(maven_metadata_files)
            comparison["gradle_metadata_files_count"] = len(gradle_metadata_files)

            if maven_core_files == gradle_core_files:
                comparison["core_match"] = True
            else:
                comparison["core_match"] = False
                comparison["maven_only_core_files"] = maven_core_files - gradle_core_files
                comparison["gradle_only_core_files"] = gradle_core_files - maven_core_files
            
            comparison["maven_only_metadata_files"] = maven_metadata_files - gradle_metadata_files
            comparison["gradle_only_metadata_files"] = gradle_metadata_files - maven_metadata_files

    except FileNotFoundError as e:
        comparison["error"] = f"Archive not found: {e.filename}"
    except zipfile.BadZipFile as e:
        # Try to identify which archive is bad if possible, otherwise generic message
        bad_archive_path = ""
        try:
            with zipfile.ZipFile(maven_archive_path, 'r') as test_zip: pass
        except zipfile.BadZipFile:
            bad_archive_path = maven_archive_path.name
        try:
            with zipfile.ZipFile(gradle_archive_path, 'r') as test_zip: pass
        except zipfile.BadZipFile:
            bad_archive_path = gradle_archive_path.name
        comparison["error"] = f"Corrupt archive {bad_archive_path if bad_archive_path else '(unknown)'}: {e}"
    except Exception as e:
        comparison["error"] = f"Error comparing archives: {str(e)}"
    return comparison

def determine_overall_status(results):
    maven_target_exists = results["maven_target_exists"] == "Yes"
    gradle_build_exists = results["gradle_build_exists"] == "Yes"

    if not maven_target_exists and not gradle_build_exists: return "Not Built (Maven & Gradle)"
    if not maven_target_exists: return "Maven Output Missing"
    if not gradle_build_exists: return "Gradle Output Missing"

    # Start with assuming OK, then look for issues.
    is_ok = True
    # Check artifacts: Only "Match (Core Content)" is considered a full pass for this check.
    # Other "Match" statuses like "Match (Names)" are intermediate and mean content wasn't fully verified or matched.
    if results["artifact_comparison_status"] != "Match (Core Content)":
        # If it's any other status that doesn't imply a full content match (excluding informational/non-issue statuses)
        if results["artifact_comparison_status"] not in ["N/A", "Not Built", "None Found (Both)"]:
            is_ok = False
    
    if results["classes_comparison_status"] != "Match":
        if results["classes_comparison_status"] not in ["N/A", "Not Built", "None Found (Both)"]:
            is_ok = False
            
    if results["test_reports_status"] != "Match":
        if results["test_reports_status"] not in ["N/A", "Not Built", "None Found (Both)"]:
            is_ok = False

    if is_ok:
        # If there were notes about metadata differences, qualify the OK status
        if any("META-INF/maven/ content" in note for note in results.get("overall_notes", [])):
            return "OK - Match (Meta Differs)"
        return "OK - Match"

    # If not perfectly OK, determine why
    if (results["artifact_comparison_status"] == "None Found (Both)" and
        results["classes_comparison_status"] == "None Found (Both)" and
        results["test_reports_status"] == "None Found (Both)"):
        return "Outputs Empty (Both)"
        
    return "Differences Found"


def compare_outputs(project_path, maven_target_dir, gradle_build_dir):
    results = {
        "project_path": str(project_path.name),
        "full_project_path": str(project_path),
        "maven_target_exists": "No",
        "gradle_build_exists": "No",
        "artifact_comparison_status": "N/A", "artifact_details": "", "artifacts_content_comparison": [],
        "classes_comparison_status": "N/A", "classes_details": "",
        "test_reports_status": "N/A", "test_reports_details": "",
        "overall_notes": [],
        "overall_status": "Pending"
    }

    results["maven_target_exists"] = "Yes" if maven_target_dir.exists() else "No"
    results["gradle_build_exists"] = "Yes" if gradle_build_dir.exists() else "No"

    if results["maven_target_exists"] == "No" and results["gradle_build_exists"] == "No":
        for key in ["artifact_comparison_status", "classes_comparison_status", "test_reports_status"]:
            results[key] = "Not Built"
        results["overall_status"] = determine_overall_status(results)
        return results

    # --- Artifact Comparison ---
    if maven_target_dir.exists() or gradle_build_dir.exists():
        maven_artifacts_paths = []
        if maven_target_dir.exists():
            maven_artifacts_paths = sorted(list(maven_target_dir.glob('*.jar')) + list(maven_target_dir.glob('*.war')), key=lambda p: p.name)

        gradle_artifacts_paths = []
        gradle_libs_dir = gradle_build_dir / 'libs'
        if gradle_libs_dir.exists():
            gradle_artifacts_paths = sorted(list(gradle_libs_dir.glob('*.jar')) + list(gradle_libs_dir.glob('*.war')), key=lambda p: p.name)

        maven_artifact_names = [p.name for p in maven_artifacts_paths]
        gradle_artifact_names = [p.name for p in gradle_artifacts_paths]
        
        results["artifact_details"] = f"Maven artifacts: {maven_artifact_names or 'None'}. Gradle artifacts: {gradle_artifact_names or 'None'}."

        if not maven_artifacts_paths and not gradle_artifacts_paths:
            results["artifact_comparison_status"] = "None Found (Both)"
        elif not maven_artifacts_paths:
            results["artifact_comparison_status"] = "Gradle Only"
        elif not gradle_artifacts_paths:
            results["artifact_comparison_status"] = "Maven Only"
        elif maven_artifact_names == gradle_artifact_names:
            # Base status on names matching, will be refined by content.
            results["artifact_comparison_status"] = "Match (Names)" 
            all_archives_core_content_matched = True # Assume true until a mismatch or error
            any_archive_had_metadata_differences = False

            for m_path, g_path in zip(maven_artifacts_paths, gradle_artifacts_paths):
                content_comp = compare_archive_contents(m_path, g_path)
                # Store full comparison details for the report
                content_comp_summary = {
                    "archive_name": m_path.name,
                    "content_core_match": content_comp["core_match"], # Key for overall status
                    "error": content_comp["error"],
                    "maven_core_files_count": content_comp["maven_core_files_count"],
                    "gradle_core_files_count": content_comp["gradle_core_files_count"],
                    "maven_only_core_files": sorted(list(content_comp["maven_only_core_files"])),
                    "gradle_only_core_files": sorted(list(content_comp["gradle_only_core_files"])),
                    "maven_metadata_files_count": content_comp["maven_metadata_files_count"],
                    "gradle_metadata_files_count": content_comp["gradle_metadata_files_count"],
                    "maven_only_metadata_files": sorted(list(content_comp["maven_only_metadata_files"])),
                    "gradle_only_metadata_files": sorted(list(content_comp["gradle_only_metadata_files"]))
                }
                results["artifacts_content_comparison"].append(content_comp_summary)

                if content_comp["error"]:
                    results["artifact_comparison_status"] = f"Error Comparing Content ({m_path.name})"
                    all_archives_core_content_matched = False
                    results["overall_notes"].append(f"Artifact Error ({m_path.name}): {content_comp['error']}")
                    break # Stop further artifact content checks on error
                
                if not content_comp["core_match"]:
                    all_archives_core_content_matched = False
                
                if content_comp["maven_only_metadata_files"] or content_comp["gradle_only_metadata_files"]:
                    any_archive_had_metadata_differences = True
            
            # Refine status based on content checks, if no error occurred during comparison
            if "Error Comparing Content" not in results["artifact_comparison_status"]:
                if all_archives_core_content_matched:
                    results["artifact_comparison_status"] = "Match (Core Content)"
                    if any_archive_had_metadata_differences:
                        results["overall_notes"].append(f"Note: Differences found in META-INF/maven/ content for one or more archives.")
                        # The primary status remains "Match (Core Content)". The overall_status function will handle this.
                else:
                    results["artifact_comparison_status"] = "Core Content Mismatch"
        else: 
            results["artifact_comparison_status"] = "Structure Mismatch (Names)"
    else: # Should ideally not be hit if one build dir exists and we are in this block
        results["artifact_comparison_status"] = "Not Built"


    # --- Compiled Classes Comparison ---
    if maven_target_dir.exists() or gradle_build_dir.exists():
        maven_classes_dir = maven_target_dir / 'classes'
        gradle_class_locs = ['java/main', 'kotlin/main', 'scala/main', 'groovy/main'] # Common source sets
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
                results["classes_details"] = f"{len(maven_class_files)} .class file(s)"
            else:
                results["classes_comparison_status"] = "Mismatch"
                m_only_count = len(maven_class_files - gradle_class_files_combined)
                g_only_count = len(gradle_class_files_combined - maven_class_files)
                results["classes_details"] = (f"M-total: {len(maven_class_files)}, G-total: {len(gradle_class_files_combined)}. "
                                              f"M-only: {m_only_count}, G-only: {g_only_count}.")
        elif maven_classes_exist:
            results["classes_comparison_status"] = "Maven Only"
            results["classes_details"] = f"{len(list(maven_classes_dir.rglob('*.class')))} .class file(s)"
        elif gradle_classes_exist:
            results["classes_comparison_status"] = "Gradle Only"
            total_gradle_classes = sum(len(list(gcd.rglob('*.class'))) for gcd in gradle_classes_dirs_to_check)
            results["classes_details"] = f"{total_gradle_classes} .class file(s)"
        else: # Neither found, though parent build/target might exist
            results["classes_comparison_status"] = "None Found (Both)"
    else: # One or both of target/build doesn't exist
        results["classes_comparison_status"] = "Not Built"


    # --- Test Reports Comparison ---
    if maven_target_dir.exists() or gradle_build_dir.exists():
        maven_test_reports_dir = maven_target_dir / 'surefire-reports'
        gradle_test_reports_dir = gradle_build_dir / 'reports' / 'tests' / 'test'
        
        maven_reports_exist = maven_test_reports_dir.exists()
        gradle_reports_exist = gradle_test_reports_dir.exists()

        if maven_reports_exist and gradle_reports_exist:
            m_xml = len(list(maven_test_reports_dir.glob('TEST-*.xml')))
            g_xml = len(list(gradle_test_reports_dir.glob('TEST-*.xml')))
            if m_xml == g_xml and m_xml > 0:
                results["test_reports_status"] = "Match"; results["test_reports_details"] = f"{m_xml} XML report(s)"
            elif m_xml > 0 or g_xml > 0: # Some reports exist but counts differ or one side is zero
                results["test_reports_status"] = "Mismatch"; results["test_reports_details"] = f"Maven XMLs: {m_xml}, Gradle XMLs: {g_xml}"
            else: # Both report dirs exist, but no XMLs found (could be other formats or no tests)
                results["test_reports_status"] = "None Found (Both)"
                results["test_reports_details"] = "No XML reports in expected locations."
        elif maven_reports_exist:
            results["test_reports_status"] = "Maven Only"
            results["test_reports_details"] = f"{len(list(maven_test_reports_dir.glob('TEST-*.xml')))} XML report(s)"
        elif gradle_reports_exist:
            results["test_reports_status"] = "Gradle Only"
            results["test_reports_details"] = f"{len(list(gradle_test_reports_dir.glob('TEST-*.xml')))} XML report(s)"
        else: # Neither specific report dir found
            results["test_reports_status"] = "None Found (Both)"
    else: # One or both of target/build doesn't exist
         results["test_reports_status"] = "Not Built"

    results["overall_status"] = determine_overall_status(results)
    return results

def generate_summary_table_for_file(all_project_results):
    """Generates a summary table string for the file report."""
    if not all_project_results:
        return "No projects matching criteria to summarize."

    headers = ["Project", "Overall Status", "Artifacts (Core)", "Classes", "Tests"] # Clarified Artifacts column
    
    table_data_rows = []
    for res in all_project_results:
        row_data = {
            "Project": res.get("project_path", "N/A"),
            "Overall Status": res.get("overall_status", "N/A"),
            "Artifacts (Core)": res.get("artifact_comparison_status", "N/A"), # This status now reflects core content
            "Classes": res.get("classes_comparison_status", "N/A"),
            "Tests": res.get("test_reports_status", "N/A")
        }
        table_data_rows.append(row_data)

    col_widths = {header: len(header) for header in headers}
    for row_dict in table_data_rows:
        for header_name in headers:
            col_widths[header_name] = max(col_widths[header_name], len(str(row_dict.get(header_name, ""))))
    
    padding = 2
    for header_name in col_widths: col_widths[header_name] += padding

    header_row_str = " | ".join([f"{h:<{col_widths[h]}}" for h in headers])
    separator_row_str = "-+-".join(["-" * col_widths[h] for h in headers])
    
    data_rows_str_list = []
    for row_dict in table_data_rows:
        cells = [f"{str(row_dict.get(h, '')):<{col_widths[h]}}" for h in headers]
        data_rows_str_list.append(" | ".join(cells))

    return header_row_str + "\n" + separator_row_str + "\n" + "\n".join(data_rows_str_list)


def generate_detailed_sections_for_file(all_project_results):
    """Generates the detailed per-module sections for the file report."""
    report_lines = ["\n\n", "-----------------------------------", " DETAILED PER-MODULE BREAKDOWN ", "-----------------------------------", ""]

    for res in all_project_results:
        report_lines.append("========================================================================")
        report_lines.append(f"Project: {res['project_path']}")
        report_lines.append(f"Full Path: {res['full_project_path']}")
        report_lines.append(f"Overall Status: {res['overall_status']}")
        report_lines.append("------------------------------------------------------------------------\n")

        report_lines.append(f"  Maven Target Dir Exists: {res['maven_target_exists']}")
        report_lines.append(f"  Gradle Build Dir Exists: {res['gradle_build_exists']}\n")

        # Artifacts Section
        report_lines.append("  Artifacts:")
        report_lines.append(f"    Core Content Status: {res['artifact_comparison_status']}") # This is the primary status now
        report_lines.append(f"    Build Outputs Found: {res['artifact_details']}") 
        if res.get("artifacts_content_comparison"):
            for content_comp in res["artifacts_content_comparison"]:
                report_lines.append(f"    Content Comparison for '{content_comp['archive_name']}':")
                if content_comp["error"]:
                    report_lines.append(f"      ERROR: {content_comp['error']}")
                    continue
                
                report_lines.append(f"      Core Content Match: {'Yes' if content_comp['content_core_match'] else 'No'}")
                report_lines.append(f"      Maven Core Files Count: {content_comp['maven_core_files_count']}")
                report_lines.append(f"      Gradle Core Files Count: {content_comp['gradle_core_files_count']}")
                if not content_comp["content_core_match"]:
                    if content_comp["maven_only_core_files"]:
                        report_lines.append("      CORE files in Maven archive ONLY:")
                        for f in content_comp["maven_only_core_files"][:15]: report_lines.append(f"        - {f}")
                        if len(content_comp["maven_only_core_files"]) > 15: report_lines.append(f"        ... and {len(content_comp['maven_only_core_files']) - 15} more.")
                    if content_comp["gradle_only_core_files"]:
                        report_lines.append("      CORE files in Gradle archive ONLY:")
                        for f in content_comp["gradle_only_core_files"][:15]: report_lines.append(f"        - {f}")
                        if len(content_comp["gradle_only_core_files"]) > 15: report_lines.append(f"        ... and {len(content_comp['gradle_only_core_files']) - 15} more.")
                
                # Always report metadata counts and differences if any
                report_lines.append(f"      Maven META-INF/maven/ Files Count: {content_comp['maven_metadata_files_count']}")
                report_lines.append(f"      Gradle META-INF/maven/ Files Count: {content_comp['gradle_metadata_files_count']}")
                if content_comp["maven_only_metadata_files"] or content_comp["gradle_only_metadata_files"]:
                    report_lines.append("      Differences in META-INF/maven/ content:")
                    if content_comp["maven_only_metadata_files"]:
                        report_lines.append("        In Maven archive ONLY (META-INF/maven/):")
                        for f in content_comp["maven_only_metadata_files"][:10]: report_lines.append(f"          - {f}")
                        if len(content_comp["maven_only_metadata_files"]) > 10: report_lines.append(f"          ... and {len(content_comp['maven_only_metadata_files']) - 10} more.")
                    if content_comp["gradle_only_metadata_files"]:
                        report_lines.append("        In Gradle archive ONLY (META-INF/maven/):")
                        for f in content_comp["gradle_only_metadata_files"][:10]: report_lines.append(f"          - {f}")
                        if len(content_comp["gradle_only_metadata_files"]) > 10: report_lines.append(f"          ... and {len(content_comp['gradle_only_metadata_files']) - 10} more.")
        report_lines.append("")

        report_lines.append("  Compiled Classes:")
        report_lines.append(f"    Status: {res['classes_comparison_status']}")
        report_lines.append(f"    Details: {res['classes_details']}\n")

        report_lines.append("  Test Reports:")
        report_lines.append(f"    Status: {res['test_reports_status']}")
        report_lines.append(f"    Details: {res['test_reports_details']}\n")

        if res["overall_notes"]:
            report_lines.append("  Overall Notes:")
            for note in res["overall_notes"]:
                report_lines.append(f"    - {note}") # Includes notes about metadata diffs now
            report_lines.append("")
        
    return "\n".join(report_lines)

def main():
    base_search_path_str = input("Enter the root path to search for projects: ")
    if not os.path.isdir(base_search_path_str):
        print(f"Error: Path '{base_search_path_str}' is not a valid directory.")
        return

    base_search_path_resolved = Path(base_search_path_str).resolve()
    all_results_data = []
    
    print(f"\nAnalyzing projects under: {base_search_path_resolved}")
    print("Will only process modules containing BOTH 'pom.xml' and 'build.gradle[.kts]'.")
    print("Important: Ensure relevant projects have been built with BOTH Maven and Gradle for comparison.")

    projects_to_analyze = find_project_roots(base_search_path_resolved)

    if not projects_to_analyze:
        print(f"\nNo modules found under '{base_search_path_resolved}' that contain BOTH 'pom.xml' and a Gradle build file.")
        return

    print(f"\n--- Processing {len(projects_to_analyze)} project(s) matching criteria ---")
    for proj_to_analyze in projects_to_analyze:
        proj_path = proj_to_analyze['path']
        proj_name = proj_to_analyze['name']

        maven_target_dir = proj_path / 'target'
        gradle_build_dir = proj_path / 'build'

        print(f"Module: {proj_name:<40} ...analyzing...", end="\r") # Indicate activity
        comparison_data = compare_outputs(proj_path, maven_target_dir, gradle_build_dir)
        all_results_data.append(comparison_data)
        # Overwrite the "...analyzing..." with the final status
        print(f"Module: {proj_name:<40} | Status: {comparison_data['overall_status']}")


    if not all_results_data:
        print("\nNo data collected from matching projects.") # Should be rare if projects_to_analyze had items
        return

    save_to_file = input("\nSave detailed comparison report to a text file? (y/n): ").strip().lower()
    if save_to_file == 'y':
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_filename = f"build_comparison_report_{timestamp}.txt"
        output_filename = input(f"Enter filename (default: {default_filename}): ").strip()
        if not output_filename: output_filename = default_filename

        try:
            report_header_lines = [
                "========================================================================",
                "          Maven to Gradle Build Comparison Report         ",
                "========================================================================",
                f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                f"Searched Path: {str(base_search_path_resolved)}\n",
                "Methodology: This report compares outputs from Maven and Gradle builds.",
                "It focuses on modules containing both 'pom.xml' and 'build.gradle[.kts]' files.",
                "Artifact comparison distinguishes core content from 'META-INF/maven/' metadata.\n",
                "-----------------------------------",
                "           SUMMARY TABLE           ",
                "-----------------------------------",
            ]
            summary_table_str = generate_summary_table_for_file(all_results_data)
            detailed_sections_str = generate_detailed_sections_for_file(all_results_data)
            
            full_report_content = "\n".join(report_header_lines) + "\n" + \
                                  summary_table_str + "\n" + \
                                  detailed_sections_str

            with open(output_filename, 'w', encoding='utf-8') as f:
                f.write(full_report_content)
            print(f"Detailed report saved to '{output_filename}'")
        except IOError as e:
            print(f"Error saving file: {e}")
    else:
        print("Report not saved to file.")

if __name__ == '__main__':
    main()