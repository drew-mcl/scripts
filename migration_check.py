import os
from pathlib import Path
from datetime import datetime
import zipfile # For inspecting JAR/WAR contents

def find_project_roots(search_path):
    """Finds potential Maven or Gradle project roots.
       Filters to only include those with BOTH pom.xml and build.gradle[.kts]."""
    project_roots_to_analyze = []
    processed_paths = set()

    for root, dirs, files in os.walk(search_path):
        # Prune common build/VCS/dependency directories
        for d in ['.git', '.svn', '.hg', 'node_modules', 'target', 'build', '__pycache__', '.venv', 'venv', 'dist', 'out']:
            if d in dirs:
                dirs.remove(d)

        current_path = Path(root).resolve()
        if current_path in processed_paths:
            continue
        processed_paths.add(current_path)

        has_pom = 'pom.xml' in files
        has_gradle_kts = 'build.gradle.kts' in files
        has_gradle_groovy = 'build.gradle' in files
        has_gradle = has_gradle_kts or has_gradle_groovy

        if has_pom and has_gradle:
            project_roots_to_analyze.append({
                'path': current_path,
                'name': current_path.name # Use directory name as module name
            })
        elif current_path == Path(search_path).resolve() and (has_pom or has_gradle): # Root path itself
            print(f"Skipping root path: {current_path.name} (at {current_path}) - requires both pom.xml and build.gradle[.kts] for analysis.")
        elif (has_pom or has_gradle) and current_path != Path(search_path).resolve(): # A sub-directory
            print(f"Skipping module: {current_path.name} (at {current_path}) - requires both pom.xml and build.gradle[.kts] for analysis.")
            
    # Ensure the base search path itself is considered if it qualifies and wasn't picked up by walk
    # (os.walk doesn't yield the top directory itself in the loop if it's the starting point and contains files directly)
    # This logic is now integrated into the loop with processed_paths and path comparison.
    # The initial check of base_search_path can be removed if os.walk is guaranteed to cover it
    # For simplicity, the above loop with resolved paths and processed_paths should handle it.

    return project_roots_to_analyze


def compare_archive_contents(maven_archive_path, gradle_archive_path):
    """Compares the internal file lists of two archive (JAR/WAR) files."""
    comparison = {
        "match": False,
        "maven_only_files": set(),
        "gradle_only_files": set(),
        "error": None,
        "maven_files_count": 0,
        "gradle_files_count": 0
    }
    try:
        with zipfile.ZipFile(maven_archive_path, 'r') as maven_zip, \
             zipfile.ZipFile(gradle_archive_path, 'r') as gradle_zip:

            maven_files = set(info.filename for info in maven_zip.infolist())
            gradle_files = set(info.filename for info in gradle_zip.infolist())
            
            comparison["maven_files_count"] = len(maven_files)
            comparison["gradle_files_count"] = len(gradle_files)

            if maven_files == gradle_files:
                comparison["match"] = True
            else:
                comparison["match"] = False
                comparison["maven_only_files"] = maven_files - gradle_files
                comparison["gradle_only_files"] = gradle_files - maven_files
    except FileNotFoundError as e:
        comparison["error"] = f"Archive not found: {e.filename}"
    except zipfile.BadZipFile as e:
        comparison["error"] = f"Corrupt archive: {e}" # Error refers to the archive that failed
    except Exception as e:
        comparison["error"] = f"Error comparing archives: {str(e)}"
    return comparison


def determine_overall_status(results):
    """Determines a single overall status string based on comparison results."""
    # Assumes both pom and gradle files exist due to pre-filtering.

    maven_target_exists = results["maven_target_exists"] == "Yes"
    gradle_build_exists = results["gradle_build_exists"] == "Yes"

    if not maven_target_exists and not gradle_build_exists:
        return "Not Built (Maven & Gradle)"
    if not maven_target_exists:
        return "Maven Output Missing"
    if not gradle_build_exists:
        return "Gradle Output Missing"

    # If both are built, check for differences
    if (results["artifact_comparison_status"].startswith("Match") and # "Match" or "Match (Content)"
        results["classes_comparison_status"] == "Match" and
        results["test_reports_status"] == "Match"):
        return "OK - Match"

    # Any specific mismatch or content difference means differences found
    if (results["artifact_comparison_status"] not in ["Match", "Match (Content)", "N/A", "Not Built", "None Found (Both)"] or
        results["classes_comparison_status"] not in ["Match", "N/A", "Not Built", "None Found (Both)"] or
        results["test_reports_status"] not in ["Match", "N/A", "Not Built", "None Found (Both)"]):
        return "Differences Found"
    
    if (results["artifact_comparison_status"] == "None Found (Both)" and
        results["classes_comparison_status"] == "None Found (Both)" and
        results["test_reports_status"] == "None Found (Both)"):
        return "Outputs Empty (Both)"
    
    return "Check Details" # Fallback

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
        results["overall_notes"].append("Neither 'target' (Maven) nor 'build' (Gradle) directory found.")
        for key in ["artifact_comparison_status", "classes_comparison_status", "test_reports_status"]:
            results[key] = "Not Built"
        results["overall_status"] = determine_overall_status(results)
        return results

    # --- Artifact Comparison ---
    if maven_target_dir.exists() or gradle_build_dir.exists():
        maven_artifacts_paths = []
        if maven_target_dir.exists():
            maven_artifacts_paths = list(maven_target_dir.glob('*.jar')) + list(maven_target_dir.glob('*.war'))

        gradle_artifacts_paths = []
        gradle_libs_dir = gradle_build_dir / 'libs'
        if gradle_libs_dir.exists():
            gradle_artifacts_paths = list(gradle_libs_dir.glob('*.jar')) + list(gradle_libs_dir.glob('*.war'))

        maven_artifact_names = sorted([p.name for p in maven_artifacts_paths])
        gradle_artifact_names = sorted([p.name for p in gradle_artifacts_paths])
        
        results["artifact_details"] = f"Maven artifacts: {maven_artifact_names or 'None'}. Gradle artifacts: {gradle_artifact_names or 'None'}."

        if not maven_artifacts_paths and not gradle_artifacts_paths:
            results["artifact_comparison_status"] = "None Found (Both)"
        elif not maven_artifacts_paths:
            results["artifact_comparison_status"] = "Gradle Only"
        elif not gradle_artifacts_paths:
            results["artifact_comparison_status"] = "Maven Only"
        elif maven_artifact_names == gradle_artifact_names:
            results["artifact_comparison_status"] = "Match (Names)" # Initial status
            all_content_matches = True
            for i in range(len(maven_artifacts_paths)):
                m_path = maven_artifacts_paths[i]
                g_path = gradle_artifacts_paths[i] # Assumes names (and therefore order) are identical
                
                content_comp = compare_archive_contents(m_path, g_path)
                content_comp_summary = {
                    "archive_name": m_path.name,
                    "content_match": content_comp["match"],
                    "error": content_comp["error"],
                    "maven_files_count": content_comp["maven_files_count"],
                    "gradle_files_count": content_comp["gradle_files_count"],
                    "maven_only_files": sorted(list(content_comp["maven_only_files"])),
                    "gradle_only_files": sorted(list(content_comp["gradle_only_files"]))
                }
                results["artifacts_content_comparison"].append(content_comp_summary)
                if content_comp["error"]:
                    results["artifact_comparison_status"] = "Error Comparing Content"
                    all_content_matches = False
                    results["overall_notes"].append(f"Error comparing content of {m_path.name}: {content_comp['error']}")
                    break 
                if not content_comp["match"]:
                    all_content_matches = False
            
            if all_content_matches and results["artifact_comparison_status"] == "Match (Names)":
                results["artifact_comparison_status"] = "Match (Content)"
            elif results["artifact_comparison_status"] == "Match (Names)": # Implies some content mismatch if not error
                 results["artifact_comparison_status"] = "Content Mismatch"

        else: # Artifact names differ
            results["artifact_comparison_status"] = "Structure Mismatch (Names)"
    else: # This case should not be hit if one of the build dirs exists.
        results["artifact_comparison_status"] = "Not Built"


    # --- Compiled Classes Comparison --- (Largely unchanged)
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
        elif maven_classes_exist:
            results["classes_comparison_status"] = "Maven Only"
            results["classes_details"] = f"{len(list(maven_classes_dir.rglob('*.class')))} .class files"
        elif gradle_classes_exist:
            results["classes_comparison_status"] = "Gradle Only"
            total_gradle_classes = sum(len(list(gcd.rglob('*.class'))) for gcd in gradle_classes_dirs_to_check)
            results["classes_details"] = f"{total_gradle_classes} .class files"
        else:
            results["classes_comparison_status"] = "None Found (Both)"
    else:
        results["classes_comparison_status"] = "Not Built"

    # --- Test Reports Comparison --- (Largely unchanged)
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
            elif maven_test_xml_count > 0 or gradle_test_xml_count > 0 :
                results["test_reports_status"] = "Mismatch"
                results["test_reports_details"] = f"Maven XMLs: {maven_test_xml_count}, Gradle XMLs: {gradle_test_xml_count}"
            else:
                results["test_reports_status"] = "None Found (Both)"
                results["test_reports_details"] = "No XML reports in expected locations."
        elif maven_reports_exist:
            results["test_reports_status"] = "Maven Only"
            results["test_reports_details"] = f"{len(list(maven_test_reports_dir.glob('TEST-*.xml')))} XML reports"
        elif gradle_reports_exist:
            results["test_reports_status"] = "Gradle Only"
            results["test_reports_details"] = f"{len(list(gradle_test_reports_dir.glob('TEST-*.xml')))} XML reports"
        else:
            results["test_reports_status"] = "None Found (Both)"
    else:
         results["test_reports_status"] = "Not Built"

    results["overall_status"] = determine_overall_status(results)
    return results

def generate_detailed_file_report(all_project_results, search_path_str):
    """Generates a structured text report for file output."""
    if not all_project_results:
        return "No projects matching criteria to report."

    report_lines = [
        "========================================================================",
        " Maven to Gradle Build Comparison Report",
        "========================================================================",
        f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Searched Path: {search_path_str}\n"
    ]

    for res in all_project_results:
        report_lines.append("------------------------------------------------------------------------")
        report_lines.append(f"Project: {res['project_path']}")
        report_lines.append(f"Full Path: {res['full_project_path']}")
        report_lines.append(f"Overall Status: {res['overall_status']}")
        report_lines.append("------------------------------------------------------------------------\n")

        report_lines.append(f"  Maven Target Dir Exists: {res['maven_target_exists']}")
        report_lines.append(f"  Gradle Build Dir Exists: {res['gradle_build_exists']}\n")

        # Artifacts Section
        report_lines.append("  Artifacts:")
        report_lines.append(f"    Status: {res['artifact_comparison_status']}")
        report_lines.append(f"    Details: {res['artifact_details']}")
        if res.get("artifacts_content_comparison"):
            for content_comp in res["artifacts_content_comparison"]:
                report_lines.append(f"    Comparison for '{content_comp['archive_name']}':")
                if content_comp["error"]:
                    report_lines.append(f"      ERROR: {content_comp['error']}")
                    continue
                report_lines.append(f"      Content Match: {'Yes' if content_comp['content_match'] else 'No'}")
                report_lines.append(f"      Maven Archive Files: {content_comp['maven_files_count']}")
                report_lines.append(f"      Gradle Archive Files: {content_comp['gradle_files_count']}")
                if not content_comp["content_match"]:
                    if content_comp["maven_only_files"]:
                        report_lines.append("      Files in Maven archive ONLY:")
                        for f in content_comp["maven_only_files"][:10]: # Limit output for brevity
                            report_lines.append(f"        - {f}")
                        if len(content_comp["maven_only_files"]) > 10:
                            report_lines.append(f"        ... and {len(content_comp['maven_only_files']) - 10} more.")
                    if content_comp["gradle_only_files"]:
                        report_lines.append("      Files in Gradle archive ONLY:")
                        for f in content_comp["gradle_only_files"][:10]: # Limit output
                            report_lines.append(f"        - {f}")
                        if len(content_comp["gradle_only_files"]) > 10:
                            report_lines.append(f"        ... and {len(content_comp['gradle_only_files']) - 10} more.")
        report_lines.append("") # Newline

        # Compiled Classes Section
        report_lines.append("  Compiled Classes:")
        report_lines.append(f"    Status: {res['classes_comparison_status']}")
        report_lines.append(f"    Details: {res['classes_details']}\n")

        # Test Reports Section
        report_lines.append("  Test Reports:")
        report_lines.append(f"    Status: {res['test_reports_status']}")
        report_lines.append(f"    Details: {res['test_reports_details']}\n")

        if res["overall_notes"]:
            report_lines.append("  Overall Notes:")
            for note in res["overall_notes"]:
                report_lines.append(f"    - {note}")
            report_lines.append("")
        
        report_lines.append("========================================================================")

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
            report_content = generate_detailed_file_report(all_results_data, str(base_search_path_resolved))
            with open(output_filename, 'w', encoding='utf-8') as f:
                f.write(report_content)
            print(f"Detailed report saved to '{output_filename}'")
        except IOError as e:
            print(f"Error saving file: {e}")
    else:
        print("Report not saved to file.")

if __name__ == '__main__':
    main()