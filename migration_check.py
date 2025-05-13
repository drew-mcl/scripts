import os
from pathlib import Path
from datetime import datetime
import zipfile

def find_project_roots(search_path):
    """Finds project roots containing BOTH pom.xml and build.gradle[.kts]."""
    project_roots_to_analyze = []
    # Resolve search_path to handle relative paths correctly
    abs_search_path = Path(search_path).resolve()

    for root, dirs, files in os.walk(abs_search_path):
        current_path = Path(root).resolve()

        # Prune common directories
        for d in ['.git', '.svn', '.hg', 'node_modules', 'target', 'build', '__pycache__', '.venv', 'venv', 'dist', 'out', '.idea', '.vscode']:
            if d in dirs:
                dirs.remove(d)

        has_pom = 'pom.xml' in files
        has_gradle_kts = 'build.gradle.kts' in files
        has_gradle_groovy = 'build.gradle' in files
        has_gradle = has_gradle_kts or has_gradle_groovy

        if has_pom and has_gradle:
            # Check for duplicates before adding
            is_duplicate = any(p['path'] == current_path for p in project_roots_to_analyze)
            if not is_duplicate:
                project_roots_to_analyze.append({
                    'path': current_path,
                    'name': current_path.name
                })
        elif current_path == abs_search_path and (has_pom or has_gradle) and not (has_pom and has_gradle) :
            # Only print skip message for the explicitly given root path if it doesn't meet criteria
             print(f"Skipping root path: {current_path.name} (at {current_path}) - requires both pom.xml and build.gradle[.kts] for analysis.")
        elif (has_pom or has_gradle) and current_path != abs_search_path and not (has_pom and has_gradle):
             # Print skip for subdirectories that don't meet criteria
             print(f"Skipping module: {current_path.name} (at {current_path}) - requires both pom.xml and build.gradle[.kts] for analysis.")
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
            
            # Compare metadata files separately
            comparison["maven_only_metadata_files"] = maven_metadata_files - gradle_metadata_files
            comparison["gradle_only_metadata_files"] = gradle_metadata_files - maven_metadata_files

    except FileNotFoundError as e:
        comparison["error"] = f"Archive not found: {e.filename}"
    except zipfile.BadZipFile as e:
        comparison["error"] = f"Corrupt archive (check which one manually): {e}"
    except Exception as e:
        comparison["error"] = f"Error comparing archives: {str(e)}"
    return comparison

def determine_overall_status(results):
    maven_target_exists = results["maven_target_exists"] == "Yes"
    gradle_build_exists = results["gradle_build_exists"] == "Yes"

    if not maven_target_exists and not gradle_build_exists: return "Not Built (Maven & Gradle)"
    if not maven_target_exists: return "Maven Output Missing"
    if not gradle_build_exists: return "Gradle Output Missing"

    is_ok = True
    if not results["artifact_comparison_status"].startswith("Match (Core Content)"):
        if results["artifact_comparison_status"] not in ["N/A", "Not Built", "None Found (Both)", "Match (Names)"]: # Match (Names) needs further check
             is_ok = False
    if results["classes_comparison_status"] != "Match":
        if results["classes_comparison_status"] not in ["N/A", "Not Built", "None Found (Both)"]:
            is_ok = False
    if results["test_reports_status"] != "Match":
        if results["test_reports_status"] not in ["N/A", "Not Built", "None Found (Both)"]:
            is_ok = False
    
    # If artifact names matched, but content comparison hasn't happened or failed, it's not "OK" yet
    if results["artifact_comparison_status"] == "Match (Names)" and not results.get("artifacts_content_comparison"):
        is_ok = False # Needs content check to be fully OK
    elif results["artifact_comparison_status"] == "Match (Names)" and results.get("artifacts_content_comparison"):
        # If names matched, check if all compared archives had core_match = True
        for comp in results["artifacts_content_comparison"]:
            if not comp.get("content_core_match", False): # Check for new key
                is_ok = False
                break


    if is_ok: return "OK - Match"

    # Refined Difference detection
    if (results["artifact_comparison_status"] not in ["Match (Core Content)", "N/A", "Not Built", "None Found (Both)"] or
        results["classes_comparison_status"] not in ["Match", "N/A", "Not Built", "None Found (Both)"] or
        results["test_reports_status"] not in ["Match", "N/A", "Not Built", "None Found (Both)"]):
        # Check if the artifact status is just about metadata or name mismatch when core content is fine
        if results["artifact_comparison_status"] == "Match (Core Content with Meta Diff)":
            # If only other things match, this could still be OK-ish, or "Differences Found (Metadata)"
            # For now, any reported artifact diff beyond pure content match is a difference
            pass # let it fall through to "Differences Found" if other things are also different

        return "Differences Found"

    if (results["artifact_comparison_status"] == "None Found (Both)" and
        results["classes_comparison_status"] == "None Found (Both)" and
        results["test_reports_status"] == "None Found (Both)"):
        return "Outputs Empty (Both)"
    
    return "Check Details"


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
            results["artifact_comparison_status"] = "Match (Names)" # Initial status
            all_core_content_matches = True
            any_metadata_differences = False

            for m_path, g_path in zip(maven_artifacts_paths, gradle_artifacts_paths):
                content_comp = compare_archive_contents(m_path, g_path)
                content_comp_summary = {
                    "archive_name": m_path.name,
                    "content_core_match": content_comp["core_match"],
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
                    all_core_content_matches = False
                    results["overall_notes"].append(f"Error comparing content of {m_path.name}: {content_comp['error']}")
                    break 
                if not content_comp["core_match"]:
                    all_core_content_matches = False
                if content_comp["maven_only_metadata_files"] or content_comp["gradle_only_metadata_files"]:
                    any_metadata_differences = True
            
            if all_core_content_matches:
                if any_metadata_differences:
                    results["artifact_comparison_status"] = "Match (Core Content with Meta Diff)"
                else:
                    results["artifact_comparison_status"] = "Match (Core Content)"
            elif results["artifact_comparison_status"] == "Match (Names)": # Implies some core content mismatch if not error
                 results["artifact_comparison_status"] = "Core Content Mismatch"
        else: 
            results["artifact_comparison_status"] = "Structure Mismatch (Names)"
    else:
        results["artifact_comparison_status"] = "Not Built"


    # --- Compiled Classes Comparison ---
    if maven_target_dir.exists() or gradle_build_dir.exists():
        maven_classes_dir = maven_target_dir / 'classes'
        gradle_class_locs = ['java/main', 'kotlin/main', 'scala/main', 'groovy/main']
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
        elif maven_classes_exist: results["classes_comparison_status"] = "Maven Only"
        elif gradle_classes_exist: results["classes_comparison_status"] = "Gradle Only"
        else: results["classes_comparison_status"] = "None Found (Both)"
    else: results["classes_comparison_status"] = "Not Built"

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
                results["test_reports_status"] = "Match"; results["test_reports_details"] = f"{m_xml} XMLs"
            elif m_xml > 0 or g_xml > 0:
                results["test_reports_status"] = "Mismatch"; results["test_reports_details"] = f"M XMLs: {m_xml}, G XMLs: {g_xml}"
            else: results["test_reports_status"] = "None Found (Both)"
        elif maven_reports_exist: results["test_reports_status"] = "Maven Only"
        elif gradle_reports_exist: results["test_reports_status"] = "Gradle Only"
        else: results["test_reports_status"] = "None Found (Both)"
    else: results["test_reports_status"] = "Not Built"

    results["overall_status"] = determine_overall_status(results)
    return results

def generate_summary_table_for_file(all_project_results):
    """Generates a summary table string for the file report."""
    if not all_project_results:
        return "No projects matching criteria to summarize."

    headers = ["Project", "Overall Status", "Artifacts", "Classes", "Tests"]
    
    table_data_rows = []
    for res in all_project_results:
        row_data = {
            "Project": res.get("project_path", "N/A"),
            "Overall Status": res.get("overall_status", "N/A"),
            "Artifacts": res.get("artifact_comparison_status", "N/A"),
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
        report_lines.append(f"    Comparison Status: {res['artifact_comparison_status']}")
        report_lines.append(f"    Build Outputs: {res['artifact_details']}") # Lists found JARs/WARs
        if res.get("artifacts_content_comparison"):
            for content_comp in res["artifacts_content_comparison"]:
                report_lines.append(f"    Content Comparison for '{content_comp['archive_name']}':")
                if content_comp["error"]:
                    report_lines.append(f"      ERROR: {content_comp['error']}")
                    continue
                
                report_lines.append(f"      Core Content Match: {'Yes' if content_comp['content_core_match'] else 'No'}")
                report_lines.append(f"      Maven Core Files: {content_comp['maven_core_files_count']}")
                report_lines.append(f"      Gradle Core Files: {content_comp['gradle_core_files_count']}")
                if not content_comp["content_core_match"]:
                    if content_comp["maven_only_core_files"]:
                        report_lines.append("      Core files in Maven archive ONLY:")
                        for f in content_comp["maven_only_core_files"][:15]: report_lines.append(f"        - {f}")
                        if len(content_comp["maven_only_core_files"]) > 15: report_lines.append(f"        ... and {len(content_comp['maven_only_core_files']) - 15} more.")
                    if content_comp["gradle_only_core_files"]:
                        report_lines.append("      Core files in Gradle archive ONLY:")
                        for f in content_comp["gradle_only_core_files"][:15]: report_lines.append(f"        - {f}")
                        if len(content_comp["gradle_only_core_files"]) > 15: report_lines.append(f"        ... and {len(content_comp['gradle_only_core_files']) - 15} more.")
                
                report_lines.append(f"      Maven META-INF/maven/ Files: {content_comp['maven_metadata_files_count']}")
                report_lines.append(f"      Gradle META-INF/maven/ Files: {content_comp['gradle_metadata_files_count']}")
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

        # Compiled Classes, Test Reports, Overall Notes sections (similar to before)
        report_lines.append("  Compiled Classes:")
        report_lines.append(f"    Status: {res['classes_comparison_status']}")
        report_lines.append(f"    Details: {res['classes_details']}\n")

        report_lines.append("  Test Reports:")
        report_lines.append(f"    Status: {res['test_reports_status']}")
        report_lines.append(f"    Details: {res['test_reports_details']}\n")

        if res["overall_notes"]:
            report_lines.append("  Overall Notes:")
            for note in res["overall_notes"]:
                report_lines.append(f"    - {note}")
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

        comparison_data = compare_outputs(proj_path, maven_target_dir, gradle_build_dir)
        all_results_data.append(comparison_data)

        print(f"Module: {proj_name:<40} | Status: {comparison_data['overall_status']}")

    if not all_results_data:
        print("\nNo data collected from matching projects.")
        return

    save_to_file = input("\nSave detailed comparison report to a text file? (y/n): ").strip().lower()
    if save_to_file == 'y':
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_filename = f"build_comparison_report_{timestamp}.txt"
        output_filename = input(f"Enter filename (default: {default_filename}): ").strip()
        if not output_filename: output_filename = default_filename

        try:
            report_header = [
                "========================================================================",
                "          Maven to Gradle Build Comparison Report         ",
                "========================================================================",
                f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                f"Searched Path: {str(base_search_path_resolved)}\n",
                "-----------------------------------",
                "           SUMMARY TABLE           ",
                "-----------------------------------",
            ]
            summary_table_str = generate_summary_table_for_file(all_results_data)
            detailed_sections_str = generate_detailed_sections_for_file(all_results_data)
            
            full_report_content = "\n".join(report_header) + "\n" + \
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