import os
import filecmp # Keep for potential future deeper comparisons, though not used in current output logic
from pathlib import Path
from datetime import datetime

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
        "project_path": str(project_path.name), # Shorter for display, full path can be base
        "full_project_path": str(project_path),
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
        results["overall_notes"].append("Neither 'target' nor 'build' dir found.")
        # Set N/A for all comparisons if both build outputs are missing
        for key in ["artifact_comparison_status", "classes_comparison_status", "test_reports_status"]:
            results[key] = "No Dirs"
        return results
    
    has_pom = (project_path / 'pom.xml').exists()
    has_gradle_build_file = (project_path / 'build.gradle').exists() or \
                            (project_path / 'build.gradle.kts').exists()

    # --- Artifact Comparison ---
    if (maven_target_dir.exists() and has_pom) or (gradle_build_dir.exists() and has_gradle_build_file):
        maven_artifacts = []
        if maven_target_dir.exists() and has_pom:
            maven_artifacts = list(maven_target_dir.glob('*.jar')) + list(maven_target_dir.glob('*.war'))
        
        gradle_artifacts = []
        gradle_libs_dir = gradle_build_dir / 'libs'
        if gradle_libs_dir.exists() and has_gradle_build_file:
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
                    results["artifact_comparison_status"] = "Partial (Size)" # More concise status
                    results["artifact_details"] += f" -- Size mismatches: {', '.join(size_mismatches)}"
            else:
                results["artifact_comparison_status"] = "Mismatch"
                results["artifact_details"] = f"Maven: {maven_artifact_names}. Gradle: {gradle_artifact_names}"
        elif maven_artifact_names:
            results["artifact_comparison_status"] = "Maven Only"
            results["artifact_details"] = f"Maven: {maven_artifact_names}"
        elif gradle_artifact_names:
            results["artifact_comparison_status"] = "Gradle Only"
            results["artifact_details"] = f"Gradle: {gradle_artifact_names}"
        else:
            results["artifact_comparison_status"] = "None Found"
            results["artifact_details"] = "No primary artifacts in expected locations."
    elif not has_pom and not has_gradle_build_file: # Neither pom nor gradle file
        results["artifact_comparison_status"] = "No Build Files"
    elif not maven_target_dir.exists() and not gradle_build_dir.exists(): # Build files exist, but no output dirs
        results["artifact_comparison_status"] = "No Output Dirs"
    else: # One output dir might exist but corresponding build file might not - indicates incomplete setup
        results["artifact_comparison_status"] = "Partial Dirs"


    # --- Compiled Classes Comparison ---
    if (maven_target_dir.exists() and has_pom) or (gradle_build_dir.exists() and has_gradle_build_file):
        maven_classes_dir = maven_target_dir / 'classes'
        # Common Gradle class output locations
        gradle_class_locs = ['java/main', 'kotlin/main', 'scala/main', 'groovy/main'] # Add others if needed
        gradle_classes_dirs_to_check = [gradle_build_dir / 'classes' / loc for loc in gradle_class_locs if (gradle_build_dir / 'classes' / loc).exists()]

        maven_classes_exist = maven_classes_dir.exists() and has_pom
        gradle_classes_exist = bool(gradle_classes_dirs_to_check) and has_gradle_build_file

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
                if m_only_count > 0: results["overall_notes"].append(f"{m_only_count} classes in Maven only.")
                if g_only_count > 0: results["overall_notes"].append(f"{g_only_count} classes in Gradle only.")

        elif maven_classes_exist:
            results["classes_comparison_status"] = "Maven Only"
            results["classes_details"] = f"{len(list(maven_classes_dir.rglob('*.class')))} .class files"
        elif gradle_classes_exist:
            results["classes_comparison_status"] = "Gradle Only"
            total_gradle_classes = sum(len(list(gcd.rglob('*.class'))) for gcd in gradle_classes_dirs_to_check)
            results["classes_details"] = f"{total_gradle_classes} .class files"
        else:
            results["classes_comparison_status"] = "None Found"
    # ... (similar logic as artifacts for No Build Files, No Output Dirs)
    elif not has_pom and not has_gradle_build_file:
        results["classes_comparison_status"] = "No Build Files"
    elif not maven_target_dir.exists() and not gradle_build_dir.exists():
        results["classes_comparison_status"] = "No Output Dirs"
    else:
        results["classes_comparison_status"] = "Partial Dirs"


    # --- Test Reports Comparison ---
    if (maven_target_dir.exists() and has_pom) or (gradle_build_dir.exists() and has_gradle_build_file):
        maven_test_reports_dir = maven_target_dir / 'surefire-reports'
        gradle_test_reports_dir = gradle_build_dir / 'reports' / 'tests' / 'test'

        maven_reports_exist = maven_test_reports_dir.exists() and has_pom
        gradle_reports_exist = gradle_test_reports_dir.exists() and has_gradle_build_file

        if maven_reports_exist and gradle_reports_exist:
            maven_test_xml_count = len(list(maven_test_reports_dir.glob('TEST-*.xml')))
            gradle_test_xml_count = len(list(gradle_test_reports_dir.glob('TEST-*.xml')))
            if maven_test_xml_count == gradle_test_xml_count and maven_test_xml_count > 0:
                results["test_reports_status"]