import os
from pathlib import Path
from datetime import datetime
import zipfile

def find_project_roots(search_path):
    """Finds project roots containing BOTH pom.xml and build.gradle[.kts]."""
    project_roots_to_analyze = []
    abs_search_path = Path(search_path).resolve()
    handled_paths = set() # To avoid re-processing or re-printing skip messages for the same path

    for root, dirs, files in os.walk(abs_search_path, topdown=True):
        current_path = Path(root).resolve()

        if current_path in handled_paths:
            dirs[:] = [] # Don't descend further from an already handled/skipped path
            continue

        # Prune common directories to speed up and avoid irrelevant checks
        dirs[:] = [d for d in dirs if d not in ['.git', '.svn', '.hg', 'node_modules', 'target', 'build', '__pycache__', '.venv', 'venv', 'dist', 'out', '.idea', '.vscode']]
        
        has_pom = 'pom.xml' in files
        has_gradle = 'build.gradle.kts' in files or 'build.gradle' in files

        if has_pom and has_gradle:
            # Ensure not to add duplicates if symlinks or unusual structures cause re-visits
            if not any(p['path'] == current_path for p in project_roots_to_analyze):
                project_roots_to_analyze.append({
                    'path': current_path,
                    'name': current_path.name
                })
            handled_paths.add(current_path)
        elif (has_pom or has_gradle): # Has one but not both
            # Print skip message only for the top-level search path or direct children 
            # of already identified valid projects, to reduce noise.
            is_root_or_direct_relevant_child = (
                current_path == abs_search_path or 
                current_path.parent == abs_search_path or
                any(current_path.parent == p['path'] for p in project_roots_to_analyze if p['path'] != current_path) # check parent against already added valid projects
            )
            if is_root_or_direct_relevant_child:
                 print(f"Skipping: {current_path.name} (at {current_path}) - requires both pom.xml and build.gradle[.kts].")
            handled_paths.add(current_path) 
            
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
    except zipfile.BadZipFile: # Simpler bad zip file handling
        comparison["error"] = f"Corrupt archive detected (Maven: {maven_archive_path.name}, Gradle: {gradle_archive_path.name}). Check files manually."
    except Exception as e:
        comparison["error"] = f"Error comparing archives ({maven_archive_path.name} vs {gradle_archive_path.name}): {str(e)}"
    return comparison

def determine_overall_status(results):
    maven_target_exists = results["maven_target_exists"] == "Yes"
    gradle_build_exists = results["gradle_build_exists"] == "Yes"

    if not maven_target_exists and not gradle_build_exists: return "Not Built (Maven & Gradle)"
    if not maven_target_exists: return "Maven Output Missing"
    if not gradle_build_exists: return "Gradle Output Missing"

    is_ok_core = True
    # Artifacts: Only "Match (Core Content)" is a full pass. Others indicate issues or further checks needed.
    if results["artifact_comparison_status"] != "Match (Core Content)":
        # These are definitive non-match statuses for core content or structure
        if results["artifact_comparison_status"] in ["Core Content Mismatch", "Structure Mismatch (Names)", 
                                                   "Maven Only", "Gradle Only"] or \
           results["artifact_comparison_status"].startswith("Error Comparing Content"):
            is_ok_core = False
        # "None Found (Both)" and "Not Built" are handled by initial checks or imply no content to compare
    
    if results["classes_comparison_status"] != "Match":
        if results["classes_comparison_status"] not in ["N/A", "Not Built", "None Found (Both)"]:
            is_ok_core = False
            
    if results["test_reports_status"] != "Match":
        if results["test_reports_status"] not in ["N/A", "Not Built", "None Found (Both)"]:
            is_ok_core = False

    if is_ok_core:
        gradle_has_unexpected_metadata = any(
            "Gradle artifact" in note and "contains META-INF/maven/ files" in note 
            for note in results.get("overall_notes", [])
        )
        if gradle_has_unexpected_metadata:
            return "OK - Match (Gradle Has Meta)" 
        return "OK - Match"

    if (results["artifact_comparison_status"] == "None Found (Both)" and
        results["classes_comparison_status"] == "None Found (Both)" and
        results["test_reports_status"] == "None Found (Both)"):
        return "Outputs Empty (Both)"
        
    return "Differences Found"


def compare_outputs(project_path, maven_target_dir, gradle_build_dir):
    results = {
        "project_path": str(project_path.name),
        "full_project_path": str(project_path),
        "maven_target_exists": "No", "gradle_build_exists": "No",
        "artifact_comparison_status": "N/A", "artifact_details": "", "artifacts_content_comparison": [],
        "classes_comparison_status": "N/A", "classes_details": "",
        "test_reports_status": "N/A", "test_reports_details": "",
        "overall_notes": [], "overall_status": "Pending"
    }

    results["maven_target_exists"] = "Yes" if maven_target_dir.exists() else "No"
    results["gradle_build_exists"] = "Yes" if gradle_build_dir.exists() else "No"

    if results["maven_target_exists"] == "No" and results["gradle_build_exists"] == "No":
        for key in ["artifact_comparison_status", "classes_comparison_status", "test_reports_status"]: results[key] = "Not Built"
        results["overall_status"] = determine_overall_status(results); return results

    # --- Artifact Comparison ---
    if maven_target_dir.exists() or gradle_build_dir.exists(): # Check if there's anything to compare
        maven_artifacts_paths = sorted(list(maven_target_dir.glob('*.jar')) + list(maven_target_dir.glob('*.war')), key=lambda p: p.name) if maven_target_dir.exists() else []
        gradle_libs_dir = gradle_build_dir / 'libs'
        gradle_artifacts_paths = sorted(list(gradle_libs_dir.glob('*.jar')) + list(gradle_libs_dir.glob('*.war')), key=lambda p: p.name) if gradle_libs_dir.exists() else []

        maven_artifact_names = [p.name for p in maven_artifacts_paths]
        gradle_artifact_names = [p.name for p in gradle_artifacts_paths]
        results["artifact_details"] = f"Maven artifacts: {maven_artifact_names or 'None'}. Gradle artifacts: {gradle_artifact_names or 'None'}."

        if not maven_artifacts_paths and not gradle_artifacts_paths: results["artifact_comparison_status"] = "None Found (Both)"
        elif not maven_artifacts_paths and gradle_artifacts_paths : results["artifact_comparison_status"] = "Gradle Only"
        elif maven_artifacts_paths and not gradle_artifacts_paths: results["artifact_comparison_status"] = "Maven Only"
        elif maven_artifact_names == gradle_artifact_names:
            results["artifact_comparison_status"] = "Match (Names)" # Default if names match, content check refines this
            all_archives_core_content_matched = True
            any_gradle_produced_unexpected_metadata = False # Specifically if Gradle produces META-INF/maven

            for m_path, g_path in zip(maven_artifacts_paths, gradle_artifacts_paths):
                content_comp = compare_archive_contents(m_path, g_path)
                content_comp_summary = {
                    "archive_name": m_path.name, "content_core_match": content_comp["core_match"],
                    "error": content_comp["error"],
                    "maven_core_files_count": content_comp["maven_core_files_count"], "gradle_core_files_count": content_comp["gradle_core_files_count"],
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
                    all_archives_core_content_matched = False # Error means we can't confirm match
                    results["overall_notes"].append(f"Artifact Error ({m_path.name}): {content_comp['error']}")
                    break 
                if not content_comp["core_match"]: all_archives_core_content_matched = False
                if content_comp["gradle_metadata_files_count"] > 0: any_gradle_produced_unexpected_metadata = True # Flag if Gradle puts anything in META-INF/maven
            
            if "Error Comparing Content" not in results["artifact_comparison_status"]: # If no error stopped checks
                if all_archives_core_content_matched:
                    results["artifact_comparison_status"] = "Match (Core Content)"
                    # Note about Gradle's metadata is handled below and influences overall_status
                else:
                    results["artifact_comparison_status"] = "Core Content Mismatch"
            
            if any_gradle_produced_unexpected_metadata:
                 results["overall_notes"].append(f"Note: Gradle artifact(s) contain META-INF/maven/ files. Review if intended.")

        else: results["artifact_comparison_status"] = "Structure Mismatch (Names)"
    else: results["artifact_comparison_status"] = "Not Built" # Should be covered by initial check

    # --- Compiled Classes & Test Reports ---
    # (Using shortened version for brevity in this diff, actual code is longer and mostly unchanged here)
    maven_classes_dir = maven_target_dir / 'classes'
    gradle_class_locs = ['java/main', 'kotlin/main', 'scala/main', 'groovy/main'] 
    gradle_classes_dirs_to_check = [gradle_build_dir / 'classes' / loc for loc in gradle_class_locs if (gradle_build_dir / 'classes' / loc).exists()]
    maven_classes_exist = maven_classes_dir.exists() and results["maven_target_exists"] == "Yes"
    gradle_classes_exist = bool(gradle_classes_dirs_to_check) and results["gradle_build_exists"] == "Yes"

    if maven_classes_exist and gradle_classes_exist:
        maven_class_files = set(p.relative_to(maven_classes_dir) for p in maven_classes_dir.rglob('*.class'))
        gradle_class_files_combined = set()
        for gcd in gradle_classes_dirs_to_check:
                gradle_class_files_combined.update(p.relative_to(gcd) for p in gcd.rglob('*.class'))
        if maven_class_files == gradle_class_files_combined:
            results["classes_comparison_status"] = "Match"; results["classes_details"] = f"{len(maven_class_files)} .class file(s)"
        else:
            results["classes_comparison_status"] = "Mismatch"
            m_only_count = len(maven_class_files - gradle_class_files_combined); g_only_count = len(gradle_class_files_combined - maven_class_files)
            results["classes_details"] = f"M-total: {len(maven_class_files)}, G-total: {len(gradle_class_files_combined)}. M-only: {m_only_count}, G-only: {g_only_count}."
    elif maven_classes_exist: results["classes_comparison_status"] = "Maven Only"; results["classes_details"] = f"{len(list(maven_classes_dir.rglob('*.class')))} .class file(s)"
    elif gradle_classes_exist: results["classes_comparison_status"] = "Gradle Only"; total_gradle_classes = sum(len(list(gcd.rglob('*.class'))) for gcd in gradle_classes_dirs_to_check); results["classes_details"] = f"{total_gradle_classes} .class file(s)"
    elif results["maven_target_exists"] == "Yes" and results["gradle_build_exists"] == "Yes": results["classes_comparison_status"] = "None Found (Both)" # Both built but no classes
    else: results["classes_comparison_status"] = "Not Built" # One or both not built

    maven_test_reports_dir = maven_target_dir / 'surefire-reports'; gradle_test_reports_dir = gradle_build_dir / 'reports' / 'tests' / 'test'
    maven_reports_exist = maven_test_reports_dir.exists() and results["maven_target_exists"] == "Yes"
    gradle_reports_exist = gradle_test_reports_dir.exists() and results["gradle_build_exists"] == "Yes"
    if maven_reports_exist and gradle_reports_exist:
        m_xml = len(list(maven_test_reports_dir.glob('TEST-*.xml'))); g_xml = len(list(gradle_test_reports_dir.glob('TEST-*.xml')))
        if m_xml == g_xml and m_xml > 0: results["test_reports_status"] = "Match"; results["test_reports_details"] = f"{m_xml} XML report(s)"
        elif m_xml > 0 or g_xml > 0: results["test_reports_status"] = "Mismatch"; results["test_reports_details"] = f"Maven XMLs: {m_xml}, Gradle XMLs: {g_xml}"
        else: results["test_reports_status"] = "None Found (Both)"; results["test_reports_details"] = "No XML reports."
    elif maven_reports_exist: results["test_reports_status"] = "Maven Only"; results["test_reports_details"] = f"{len(list(maven_test_reports_dir.glob('TEST-*.xml')))} XML report(s)"
    elif gradle_reports_exist: results["test_reports_status"] = "Gradle Only"; results["test_reports_details"] = f"{len(list(gradle_test_reports_dir.glob('TEST-*.xml')))} XML report(s)"
    elif results["maven_target_exists"] == "Yes" and results["gradle_build_exists"] == "Yes": results["test_reports_status"] = "None Found (Both)"
    else: results["test_reports_status"] = "Not Built"
    
    results["overall_status"] = determine_overall_status(results)
    return results

def generate_summary_table_for_file(all_project_results):
    if not all_project_results: return "No projects matching criteria to summarize."
    headers = ["Project", "Overall Status", "Artifacts (Core)", "Classes", "Tests"]
    table_data_rows = []
    for res in all_project_results:
        table_data_rows.append({
            "Project": res.get("project_path", "N/A"),
            "Overall Status": res.get("overall_status", "N/A"),
            "Artifacts (Core)": res.get("artifact_comparison_status", "N/A"),
            "Classes": res.get("classes_comparison_status", "N/A"),
            "Tests": res.get("test_reports_status", "N/A")
        })
    col_widths = {h: len(h) for h in headers}
    for row in table_data_rows:
        for h_name in headers: col_widths[h_name] = max(col_widths[h_name], len(str(row.get(h_name, ""))))
    for h_name in col_widths: col_widths[h_name] += 2
    header_str = " | ".join([f"{h:<{col_widths[h]}}" for h in headers])
    sep_str = "-+-".join(["-" * col_widths[h] for h in headers])
    row_strs = [" | ".join([f"{str(row.get(h, '')):<{col_widths[h]}}" for h in headers]) for row in table_data_rows]
    return header_str + "\n" + sep_str + "\n" + "\n".join(row_strs)

def generate_detailed_sections_for_file(all_project_results):
    report_lines = ["\n\n", "-----------------------------------", " DETAILED PER-MODULE BREAKDOWN ", "-----------------------------------", ""]
    for res in all_project_results:
        report_lines.extend([
            "========================================================================",
            f"Project: {res['project_path']}",
            f"Full Path: {res['full_project_path']}",
            f"Overall Status: {res['overall_status']}",
            "------------------------------------------------------------------------\n",
            f"  Maven Target Dir Exists: {res['maven_target_exists']}",
            f"  Gradle Build Dir Exists: {res['gradle_build_exists']}\n",
            "  Artifacts:",
            f"    Core Content Status: {res['artifact_comparison_status']}",
            f"    Build Outputs Found: {res['artifact_details']}"
        ])
        if res.get("artifacts_content_comparison"):
            for content_comp in res["artifacts_content_comparison"]:
                report_lines.append(f"    Content Comparison for '{content_comp['archive_name']}':")
                if content_comp["error"]:
                    report_lines.append(f"      ERROR: {content_comp['error']}")
                    continue
                
                report_lines.extend([
                    f"      Core Content Match: {'Yes' if content_comp['content_core_match'] else 'No'}",
                    f"        Maven Core Files Count: {content_comp['maven_core_files_count']}",
                    f"        Gradle Core Files Count: {content_comp['gradle_core_files_count']}"
                ])
                if not content_comp["content_core_match"]:
                    if content_comp["maven_only_core_files"]:
                        report_lines.append("        CORE files found in Maven archive ONLY (expected to be in Gradle too):")
                        for f in content_comp["maven_only_core_files"][:15]: report_lines.append(f"          - {f}")
                        if len(content_comp["maven_only_core_files"]) > 15: report_lines.append(f"          ... and {len(content_comp['maven_only_core_files']) - 15} more.")
                    if content_comp["gradle_only_core_files"]:
                        report_lines.append("        CORE files found in Gradle archive ONLY (unexpectedly):")
                        for f in content_comp["gradle_only_core_files"][:15]: report_lines.append(f"          - {f}")
                        if len(content_comp["gradle_only_core_files"]) > 15: report_lines.append(f"          ... and {len(content_comp['gradle_only_core_files']) - 15} more.")
                
                # META-INF/maven/ specific reporting
                report_lines.append(f"      (Info) Maven archive contained {content_comp['maven_metadata_files_count']} file(s) in META-INF/maven/.")
                if content_comp['gradle_metadata_files_count'] > 0:
                    report_lines.append(f"      (WARNING) Gradle archive contained {content_comp['gradle_metadata_files_count']} file(s) in META-INF/maven/:")
                    # List files in Gradle's META-INF/maven that were NOT in Maven's META-INF/maven (most relevant warning)
                    if content_comp["gradle_only_metadata_files"]:
                        report_lines.append("        Unexpected META-INF/maven/ files in Gradle archive (i.e., not found in Maven's corresponding metadata):")
                        for f in content_comp["gradle_only_metadata_files"][:10]: report_lines.append(f"          - {f}")
                        if len(content_comp["gradle_only_metadata_files"]) > 10: report_lines.append(f"          ... and {len(content_comp['gradle_only_metadata_files']) - 10} more.")
                elif content_comp['maven_metadata_files_count'] > 0: # Maven had them, Gradle correctly did not.
                    report_lines.append(f"      (Info) Gradle archive correctly contained 0 files in META-INF/maven/.")
        report_lines.append("")

        report_lines.extend([
            "  Compiled Classes:",
            f"    Status: {res['classes_comparison_status']}",
            f"    Details: {res['classes_details']}\n",
            "  Test Reports:",
            f"    Status: {res['test_reports_status']}",
            f"    Details: {res['test_reports_details']}\n"
        ])
        if res["overall_notes"]:
            report_lines.append("  Overall Notes:")
            for note in res["overall_notes"]: report_lines.append(f"    - {note}") # Includes notes about Gradle having META-INF/maven
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
        print(f"Module: {proj_name:<40} ...analyzing...", end="\r", flush=True)
        comparison_data = compare_outputs(proj_path, proj_path / 'target', proj_path / 'build')
        all_results_data.append(comparison_data)
        print(f"Module: {proj_name:<40} | Status: {comparison_data['overall_status']}")


    if not all_results_data: print("\nNo data collected from matching projects."); return

    if input("\nSave detailed comparison report to a text file? (y/n): ").strip().lower() == 'y':
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = input(f"Enter filename (default: build_comparison_report_{ts}.txt): ").strip() or f"build_comparison_report_{ts}.txt"
        try:
            header_lines = [
                "========================================================================",
                "          Maven to Gradle Build Comparison Report         ",
                "========================================================================",
                f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                f"Searched Path: {str(base_search_path_resolved)}\n",
                "Methodology: Compares outputs from Maven and Gradle builds for modules with both 'pom.xml' and 'build.gradle[.kts]'.",
                "Artifact comparison distinguishes core application content from 'META-INF/maven/' metadata.",
                "The absence of 'META-INF/maven/' in Gradle output (if present in Maven) is generally the expected outcome.\n",
                "-----------------------------------", "           SUMMARY TABLE           ", "-----------------------------------",
            ]
            summary_table_str = generate_summary_table_for_file(all_results_data)
            detailed_sections_str = generate_detailed_sections_for_file(all_results_data)
            
            full_report_content = "\n".join(header_lines) + "\n" + \
                                  summary_table_str + "\n" + \
                                  detailed_sections_str

            with open(fname, 'w', encoding='utf-8') as f:
                f.write(full_report_content)
            print(f"Detailed report saved to '{fname}'")
        except IOError as e: print(f"Error saving file: {e}")
    else: print("Report not saved.")

if __name__ == '__main__':
    main()