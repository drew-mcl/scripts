import os
import re
import subprocess
import argparse
from collections import defaultdict, namedtuple

# Data structures to hold dependency information
DeclaredDependency = namedtuple('DeclaredDependency', ['group', 'name', 'version', 'config', 'is_project'])
ResolvedDependency = namedtuple('ResolvedDependency', ['group', 'name', 'version', 'config', 'depth', 'is_project', 'children'])
ImportedClass = namedtuple('ImportedClass', ['package', 'class_name', 'full_import'])

# --- Configuration ---
GRADLEW_CMD = './gradlew' # Or 'gradlew.bat' on Windows
# Common Java/Kotlin packages to ignore (too generic or part of JDK)
IGNORED_PACKAGES_PREFIXES = [
    'java.', 'javax.', 'kotlin.', 'kotlinx.', 'android.', 'androidx.annotation.'
]
# Match common dependency declaration patterns in build.gradle.kts
# e.g., implementation("group:name:version"), api(project(":path"))
DEPENDENCY_DECLARATION_REGEX = re.compile(
    r'^\s*(api|implementation|compileOnly|runtimeOnly|testImplementation|androidTestImplementation)\s*'
    r'(?:\(\s*project\s*\(\s*[\'"](.*?)[\'"]\s*\)\s*\)|'  # Project dependency
    r'\(?[\'"]([a-zA-Z0-9._-]+):([a-zA-Z0-9._-]+):?([a-zA-Z0-9._\-\+\[\],]+)?[\'"]\)?)' # Artifact dependency
)

# Match lines from 'gradle dependencies' output
# Example: +--- org.jetbrains.kotlin:kotlin-stdlib:1.8.0
# Example: |    +--- org.jetbrains.annotations:annotations:13.0
# Example: \--- project :another-module
GRADLE_DEPENDENCY_LINE_REGEX = re.compile(
    r'^([|\\+ ]*[\\+\-]{3})\s*(project\s+[:\w.-]+|[\w.-]+:[\w.-]+:[\w.-]+(?: -> [\w.-]+)?(?: \(\*\))?)'
)
GRADLE_PROJECT_DEPENDENCY_REGEX = re.compile(r'project ([:\w.-]+)')
GRADLE_ARTIFACT_DEPENDENCY_REGEX = re.compile(r'([\w.-]+):([\w.-]+):([\w.-]+)(?: -> ([\w.-]+))?')


class GradleModule:
    """Represents a Gradle module/subproject."""
    def __init__(self, path, name):
        self.path = path # Path from root project
        self.name = name # e.g., :submodule
        self.build_file_path = os.path.join(path, 'build.gradle.kts') # Assuming kts
        self.src_main_java_path = os.path.join(path, 'src', 'main', 'java')
        self.src_main_kotlin_path = os.path.join(path, 'src', 'main', 'kotlin')
        self.declared_dependencies = [] # List of DeclaredDependency
        self.resolved_dependencies = {} # Config -> List of ResolvedDependency
        self.imported_classes = set() # Set of ImportedClass
        self.public_api_signatures = set() # Set of package/class names used in public APIs

    def __repr__(self):
        return f"<GradleModule {self.name}>"

def find_gradle_modules(root_dir):
    """Finds all Gradle modules (projects with build.gradle.kts or settings.gradle)"""
    modules = []
    # Add root project
    if os.path.exists(os.path.join(root_dir, 'build.gradle.kts')) or \
       os.path.exists(os.path.join(root_dir, 'build.gradle')):
        modules.append(GradleModule(root_dir, os.path.basename(root_dir) or "root"))

    if os.path.exists(os.path.join(root_dir, 'settings.gradle.kts')) or \
       os.path.exists(os.path.join(root_dir, 'settings.gradle')):
        # A more robust way would be to parse settings.gradle(.kts) 'include' statements
        # For simplicity, we scan for directories with build files.
        for dirpath, dirnames, filenames in os.walk(root_dir):
            if dirpath == root_dir: # Skip root, already added
                # Prune common build/vendor directories
                dirnames[:] = [d for d in dirnames if d not in ['.gradle', 'build', 'gradle', '.idea', 'vendor', 'node_modules']]
                continue

            if 'build.gradle.kts' in filenames or 'build.gradle' in filenames:
                # Construct module name relative to root_dir
                relative_path = os.path.relpath(dirpath, root_dir)
                module_name = ':' + relative_path.replace(os.sep, ':')
                if module_name == ":": # Should be root, but handle if logic changes
                    module_name = os.path.basename(root_dir)

                # Check if it's the root module path itself
                if dirpath != root_dir:
                     modules.append(GradleModule(dirpath, module_name))
                # Prevent descending into sub-project's sub-projects if settings.gradle defines them flat
                # For now, simple traversal. A more robust solution parses settings.gradle.
    if not modules and (os.path.exists(os.path.join(root_dir, 'build.gradle.kts')) or \
                        os.path.exists(os.path.join(root_dir, 'build.gradle'))):
        # Single module project
        modules.append(GradleModule(root_dir, "root"))

    print(f"Found modules: {[m.name for m in modules]}")
    return modules


def run_gradle_dependencies(module_path, module_name):
    """Runs './gradlew :module:dependencies' and returns the output."""
    # If module_name is 'root', run dependencies for the root project
    # Otherwise, construct the task name like :submodule:dependencies
    task_name = f"{module_name}:dependencies" if module_name != "root" and not module_name.startswith(':') else "dependencies"
    if module_name != "root" and not module_name.startswith(':'): # ensure leading colon for subprojects
        task_name = f":{module_name}:dependencies"
    elif module_name == "root": # For root project, just 'dependencies'
        task_name = "dependencies"


    cmd = [GRADLEW_CMD, task_name, '--configuration', 'compileClasspath', '--configuration', 'runtimeClasspath', '--configuration', 'apiElements', '--configuration', 'implementationElements', '--configuration', 'compileOnlyClasspath']
    # Add more configurations as needed: testCompileClasspath, etc.
    print(f"Running: {' '.join(cmd)} in {module_path}")
    try:
        # Run from the root project directory for context
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=os.path.dirname(module_path) if module_name != "root" else module_path)
        stdout, stderr = process.communicate(timeout=180) # 180 seconds timeout
        if process.returncode != 0:
            print(f"Error running Gradle for {module_name}:")
            print(stderr)
            return None
        return stdout
    except subprocess.TimeoutExpired:
        print(f"Timeout running Gradle for {module_name}.")
        process.kill()
        return None
    except FileNotFoundError:
        print(f"Error: '{GRADLEW_CMD}' not found. Ensure you are in the root of a Gradle project.")
        return None
    except Exception as e:
        print(f"An unexpected error occurred while running gradle: {e}")
        return None

def parse_gradle_dependencies_output(output):
    """
    Parses the output of the 'gradle dependencies' task.
    This is a simplified parser and might need adjustments based on Gradle versions.
    Returns a dictionary: {config_name: [ResolvedDependency_root_nodes]}
    """
    dependencies_by_config = defaultdict(list)
    current_config = None
    # Stores the parent for the current depth. stack[-1] is the current parent.
    # Each element is (depth_indicator_len, dependency_node)
    parent_stack = []

    if not output:
        return dependencies_by_config

    lines = output.splitlines()
    for line in lines:
        line = line.rstrip()
        if not line.strip(): # Skip empty lines
            continue

        # Detect configuration headers (e.g., "compileClasspath - Compile classpath for main.")
        if not GRADLE_DEPENDENCY_LINE_REGEX.match(line) and not line.startswith(" ") and (" - " in line or line.endswith("Classpath")):
            # Heuristic: if it doesn't look like a dependency line and contains " - " or ends with "Classpath", it's likely a config header
            current_config = line.split(" - ")[0].strip()
            if not current_config: # handle cases like "debugCompileClasspath"
                current_config = line.strip()
            parent_stack = [] # Reset parent stack for new configuration
            # print(f"Parsing configuration: {current_config}")
            continue

        if not current_config: # Skip lines before the first configuration
            continue

        match = GRADLE_DEPENDENCY_LINE_REGEX.match(line)
        if match:
            depth_indicator = match.group(1)
            dep_string = match.group(2).strip()

            is_project = False
            group, name, version, resolved_version = None, None, None, None

            proj_match = GRADLE_PROJECT_DEPENDENCY_REGEX.fullmatch(dep_string)
            if proj_match:
                is_project = True
                name = proj_match.group(1) # e.g., :utils
                group = "project" # Convention for project dependencies
                version = "local"
            else:
                art_match = GRADLE_ARTIFACT_DEPENDENCY_REGEX.fullmatch(dep_string)
                if art_match:
                    group, name, version, resolved_version_maybe = art_match.groups()
                    version = resolved_version_maybe if resolved_version_maybe else version # Use resolved version if present
                else:
                    # print(f"Could not parse dependency string: {dep_string} in config {current_config}")
                    continue # Skip unparseable lines

            # Calculate depth based on the length of the prefix (e.g., "|    ")
            # This is a bit heuristic. A more robust parser would be better.
            current_depth = len(depth_indicator.replace(" ","")) # Rough depth

            node = ResolvedDependency(group, name, version, current_config, current_depth, is_project, [])

            # Adjust parent_stack based on current_depth
            # Pop from stack if current depth is less than or equal to the depth of items on stack
            while parent_stack and parent_stack[-1][0] >= current_depth:
                parent_stack.pop()

            if not parent_stack: # This is a root dependency for the current configuration
                dependencies_by_config[current_config].append(node)
            else: # This is a child of parent_stack[-1]
                parent_node = parent_stack[-1][1]
                parent_node.children.append(node)

            parent_stack.append((current_depth, node))
    return dependencies_by_config


def parse_build_gradle_kts(file_path):
    """Parses 'build.gradle.kts' to find dependency declarations."""
    declared_deps = []
    if not os.path.exists(file_path):
        print(f"Warning: Build file not found: {file_path}")
        return declared_deps

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                match = DEPENDENCY_DECLARATION_REGEX.search(line)
                if match:
                    config = match.group(1)
                    project_path = match.group(2) # For project(':path')
                    group, name, version = match.group(3), match.group(4), match.group(5)

                    if project_path:
                        # This is a project dependency
                        dep_name = project_path.strip(':') # Store as 'path' not ':path'
                        declared_deps.append(DeclaredDependency("project", dep_name, "local", config, True))
                    elif group and name: # Version can be null for BOMs or platform
                        declared_deps.append(DeclaredDependency(group, name, version if version else " unspecified", config, False))
    except Exception as e:
        print(f"Error parsing build file {file_path}: {e}")
    return declared_deps


def scan_java_kotlin_files(src_dirs):
    """Scans Java/Kotlin files for import statements."""
    imports = set() # Set of ImportedClass
    # Regex to capture package and class from import statements
    # Handles simple imports, wildcard imports, and static imports (partially)
    import_regex = re.compile(r'^\s*import\s+(static\s+)?([\w.]+)(?:\.([\w*]+))?\s*;')

    for src_dir in src_dirs:
        if not os.path.exists(src_dir):
            continue
        for root, _, files in os.walk(src_dir):
            for file in files:
                if file.endswith('.java') or file.endswith('.kt'):
                    file_path = os.path.join(root, file)
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            for line in f:
                                match = import_regex.match(line)
                                if match:
                                    full_import_path = match.group(2)
                                    class_or_wildcard = match.group(3) if match.group(3) else '*' # if no specific class, assume wildcard for package

                                    # Skip very generic packages
                                    if any(full_import_path.startswith(p) for p in IGNORED_PACKAGES_PREFIXES):
                                        continue

                                    # Split package and class if class_or_wildcard is not '*'
                                    # For 'a.b.c.D', package is 'a.b.c', class is 'D'
                                    # For 'a.b.c.*', package is 'a.b.c', class is '*'
                                    package_parts = full_import_path.split('.')
                                    imported_package = full_import_path
                                    imported_class_name = class_or_wildcard

                                    if class_or_wildcard != '*' and '.' not in full_import_path : # e.g. import Foo -> class Foo, package empty (implicitly current)
                                        imported_package = "" # Or determine from file's package declaration
                                        imported_class_name = full_import_path
                                    elif class_or_wildcard == '*':
                                        imported_package = full_import_path
                                        imported_class_name = '*'
                                    # else: # a.b.C or a.b.*
                                        # This logic might need refinement for cases like `import a.b.Outer.InnerClass`
                                        # The regex currently puts `Outer.InnerClass` into `class_or_wildcard` if `full_import_path` is `a.b`
                                        # For simplicity, we'll use full_import_path as the package for now.
                                        # A proper Java/Kotlin parser would be more accurate.

                                    imports.add(ImportedClass(imported_package, imported_class_name, f"{full_import_path}.{class_or_wildcard if class_or_wildcard else ''}".strip('.')))
                    except Exception as e:
                        print(f"Error reading file {file_path}: {e}")
    return imports

def get_public_api_signatures(src_dirs):
    """
    Placeholder: Scans Java/Kotlin files for public API signatures.
    This is a complex task and would ideally use a proper parser (e.g., ANTLR, javaparser, PSI).
    For this script, we'll use a very naive regex approach or skip it.
    A simple heuristic: look for types used in public/protected methods/fields.
    Returns a set of package names or fully qualified class names.
    """
    # Naive example: find types in public method signatures.
    # public_api_regex = re.compile(r'public\s+(?:static\s+|final\s+)*([\w.<>\[\]]+)\s+\w+\s*\([^)]*\)')
    # This is too simplistic to be reliable.
    # For now, this function will return an empty set.
    # A more advanced implementation would parse the AST.
    return set()


class DependencyAnalyzer:
    def __init__(self, module: GradleModule):
        self.module = module
        self.report = {
            "module_name": module.name,
            "unused_dependencies": [],
            "api_vs_implementation": [],
            "implementation_vs_compileonly": [],
            "transitively_used_but_not_declared": [],
            "notes": []
        }

    def _is_dependency_used(self, dep_group, dep_name):
        """Checks if a dependency (group, name) is likely used based on imports."""
        # This is a heuristic. It checks if any imported package starts with the dependency's group
        # or a combination of group.name. More sophisticated matching might be needed.
        # e.g., for "org.jetbrains.kotlin:kotlin-stdlib", check for "kotlin." or "org.jetbrains.kotlin."
        for imp_class in self.module.imported_classes:
            # Check group.name, e.g., com.fasterxml.jackson.core
            if imp_class.package.startswith(f"{dep_group}.{dep_name}"):
                return True
            # Check group, e.g., org.jetbrains
            if imp_class.package.startswith(dep_group):
                return True
            # Check if imported class name itself is part of the dep_name (less reliable)
            # if dep_name in imp_class.class_name or dep_name in imp_class.package:
            # return True
        return False

    def _find_resolved_dependency(self, group, name, config_substring=None):
        """Finds a resolved dependency by group and name across relevant configurations."""
        for config_name, root_deps in self.module.resolved_dependencies.items():
            if config_substring and config_substring not in config_name.lower():
                continue # Skip if config_substring is specified and doesn't match

            queue = list(root_deps)
            visited = set()
            while queue:
                res_dep = queue.pop(0)
                if (res_dep.group, res_dep.name) in visited:
                    continue
                visited.add((res_dep.group, res_dep.name))

                if res_dep.group == group and res_dep.name == name:
                    return res_dep
                queue.extend(res_dep.children)
        return None

    def analyze(self):
        print(f"\n--- Analyzing module: {self.module.name} ---")

        # 1. Check for unused dependencies
        for decl_dep in self.module.declared_dependencies:
            if decl_dep.is_project: # Skip project dependencies for "unused" check here, handle separately
                continue
            if "test" in decl_dep.config.lower(): # Skip test dependencies for this basic usage check
                continue

            # Find the corresponding resolved dependency to ensure it's not just a BOM or platform
            # Resolved dependencies are what actually get used.
            # We check if the *declared* one is used.
            is_used = self._is_dependency_used(decl_dep.group, decl_dep.name)
            if not is_used:
                # Double check if it's a BOM (no version, or 'platform') or if it pulls in used transitives
                # This is tricky. A simple check: if it has no version, it might be a BOM.
                is_bom_like = not decl_dep.version or "platform" in decl_dep.version.lower()

                # A more robust check: see if any of its *transitive* dependencies are used.
                # This requires looking up decl_dep in the resolved tree.
                resolved_direct_dep = self._find_resolved_dependency(decl_dep.group, decl_dep.name)
                transitive_used = False
                if resolved_direct_dep:
                    queue = list(resolved_direct_dep.children)
                    visited_transitive = set()
                    while queue:
                        trans_dep = queue.pop(0)
                        if (trans_dep.group, trans_dep.name) in visited_transitive:
                            continue
                        visited_transitive.add((trans_dep.group, trans_dep.name))
                        if self._is_dependency_used(trans_dep.group, trans_dep.name):
                            transitive_used = True
                            break
                        queue.extend(trans_dep.children)

                if not transitive_used and not is_bom_like:
                    self.report["unused_dependencies"].append(
                        f"{decl_dep.config} {decl_dep.group}:{decl_dep.name}:{decl_dep.version or 'N/A'} - Potentially unused."
                    )

        # 2. API vs Implementation
        # If a dependency is 'api', check if its types are exposed in the module's public API.
        # This is hard to do accurately without full AST parsing.
        # Heuristic: if 'api' and not obviously used in public signatures (if we had them), suggest 'implementation'.
        # For now, we'll just list 'api' dependencies as items to review.
        for decl_dep in self.module.declared_dependencies:
            if decl_dep.config == "api":
                is_used = self._is_dependency_used(decl_dep.group, decl_dep.name) # Basic usage check
                if not is_used: # If not even used privately, it's also unused.
                     # Already handled by unused check, but good to note.
                    pass # self.report["api_vs_implementation"].append(f"api {decl_dep.group}:{decl_dep.name} - Also seems unused. Consider removing or using 'implementation'.")
                else:
                    # A proper check would involve: self.module.public_api_signatures
                    # If we had public_api_signatures:
                    # if not any(api_sig.startswith(decl_dep.group) for api_sig in self.module.public_api_signatures):
                    #    self.report["api_vs_implementation"].append(f"api {decl_dep.group}:{decl_dep.name} - Consider 'implementation' if not exposed in public API.")
                    # else:
                    self.report["api_vs_implementation"].append(
                        f"api {decl_dep.group}:{decl_dep.name}:{decl_dep.version or 'N/A'} - Review: Is this required on the compile classpath of consumers?"
                    )


        # 3. Implementation vs CompileOnly
        # If a dependency is 'implementation' but only used for annotations or tools not needed at runtime.
        # Heuristic: if it contains 'annotation', 'processor', 'checker', etc. in its name.
        # Or if its imports are only used in contexts that are stripped at compile time.
        for decl_dep in self.module.declared_dependencies:
            if decl_dep.config == "implementation":
                # Heuristic based on name
                name_lower = decl_dep.name.lower()
                if any(keyword in name_lower for keyword in ["annotation", "processor", "checker", "lombok", "jetbrain-annotations"]):
                    self.report["implementation_vs_compileonly"].append(
                        f"implementation {decl_dep.group}:{decl_dep.name}:{decl_dep.version or 'N/A'} - Consider 'compileOnly' if only used for annotations/compile-time processing."
                    )
                # A more robust check would be to see if the imported classes are only used in annotation sites
                # or if the dependency is a known annotation processor.

        # 4. Transitively used but not declared (suggest making direct)
        # Iterate through all resolved dependencies. If a transitive one is imported, suggest making it direct.
        # This helps make dependencies more explicit and resilient to changes in transitive versions.
        all_declared_tuples = {(d.group, d.name) for d in self.module.declared_dependencies}

        for config_name, root_deps in self.module.resolved_dependencies.items():
            if "test" in config_name.lower(): continue # Skip test scope for this suggestion

            queue = []
            for root_dep in root_deps: # Iterate over direct dependencies in this config
                queue.extend(root_dep.children) # Start with their children (transitive)

            visited_transitive = set()
            while queue:
                trans_dep = queue.pop(0)
                if (trans_dep.group, trans_dep.name) in visited_transitive or trans_dep.is_project:
                    continue
                visited_transitive.add((trans_dep.group, trans_dep.name))

                # If this transitive dependency is used AND not already declared directly
                if self._is_dependency_used(trans_dep.group, trans_dep.name) and \
                   (trans_dep.group, trans_dep.name) not in all_declared_tuples:
                    self.report["transitively_used_but_not_declared"].append(
                        f"{trans_dep.group}:{trans_dep.name}:{trans_dep.version} (transitive via {config_name}) - Heavily used. Consider declaring directly."
                    )
                queue.extend(trans_dep.children)

        # 5. Inter-project dependencies
        for decl_dep in self.module.declared_dependencies:
            if decl_dep.is_project:
                # Example check: if a project is 'api', it should be genuinely needed by consumers.
                # If it's 'implementation project(":foo")', ensure ':foo' isn't leaking types that
                # would require consumers to also depend on ':foo'.
                # This overlaps with api_vs_implementation logic.
                if decl_dep.config == "api":
                     self.report["notes"].append(
                        f"Inter-project: 'api project(\":{decl_dep.name}\")' - Ensure this project's API is truly needed by consumers of '{self.module.name}'."
                    )
                # Could also check if the project dependency is actually used via imports.
                # Project imports might look like `import com.example.anothermodule.MyClass`
                # The `dep_name` for project is like `anothermodule`.
                # We'd need to map project names to their typical root packages. This is complex.
                # For now, just a note.


    def get_report(self):
        return self.report


def print_analysis_report(full_report):
    print("\n\n" + "="*50)
    print("Gradle Dependency Analysis Report")
    print("="*50)

    for module_report in full_report:
        print(f"\n--- Module: {module_report['module_name']} ---")

        if module_report["unused_dependencies"]:
            print("\n[!] Potentially Unused Dependencies:")
            for item in module_report["unused_dependencies"]:
                print(f"  - {item}")

        if module_report["api_vs_implementation"]:
            print("\n[?] Review 'api' Dependencies (Consider 'implementation'):")
            for item in module_report["api_vs_implementation"]:
                print(f"  - {item}")

        if module_report["implementation_vs_compileonly"]:
            print("\n[?] Review 'implementation' Dependencies (Consider 'compileOnly'):")
            for item in module_report["implementation_vs_compileonly"]:
                print(f"  - {item}")

        if module_report["transitively_used_but_not_declared"]:
            print("\n[+] Suggestions for Direct Declaration (Currently Transitive but Used):")
            for item in module_report["transitively_used_but_not_declared"]:
                print(f"  - {item}")
        
        if module_report["notes"]:
            print("\n[*] General Notes & Observations:")
            for item in module_report["notes"]:
                print(f"  - {item}")
    
    print("\n" + "="*50)
    print("Analysis Complete.")
    print("Note: This script uses heuristics. Always verify suggestions.")
    print("Considerations for accuracy:")
    print("  - Java/Kotlin import parsing is regex-based and may not capture all usages.")
    print("  - 'Public API' usage for 'api' vs 'implementation' is not deeply analyzed.")
    print("  - BOMs (Bill of Materials) or 'platform' dependencies might be flagged as unused if their purpose is solely to align versions.")


def main():
    parser = argparse.ArgumentParser(description="Analyze Gradle project dependencies.")
    parser.add_argument("project_root", nargs="?", default=".", help="Path to the Gradle project root directory (default: current directory).")
    # parser.add_argument("--module", help="Specific module to analyze (e.g., :app or subproject-name). If not set, analyzes all found modules.")
    # Future: add --output-format (json, html)

    args = parser.parse_args()
    project_root = os.path.abspath(args.project_root)

    if not (os.path.exists(os.path.join(project_root, 'settings.gradle.kts')) or \
            os.path.exists(os.path.join(project_root, 'settings.gradle')) or \
            os.path.exists(os.path.join(project_root, 'build.gradle.kts')) or \
            os.path.exists(os.path.join(project_root, 'build.gradle'))):
        print(f"Error: Not a Gradle project root: {project_root}")
        print("Please provide the path to a directory containing settings.gradle[.kts] or a single-module project's build.gradle[.kts].")
        return

    print(f"Starting analysis for project at: {project_root}")
    
    # If a specific module is requested, adjust logic. For now, find all.
    # For simplicity, this version assumes running from root and analyzing modules it finds.
    # A more robust module discovery would parse settings.gradle.
    # For now, we assume subdirectories with build.gradle.kts are modules.
    
    gradle_modules = find_gradle_modules(project_root)
    if not gradle_modules:
        print("No Gradle modules found. Ensure build.gradle.kts or settings.gradle.kts exists.")
        return

    full_analysis_report = []

    for module in gradle_modules:
        print(f"\nProcessing module: {module.name} at {module.path}")

        # 1. Parse build.gradle.kts
        module.declared_dependencies = parse_build_gradle_kts(module.build_file_path)
        print(f"  Found {len(module.declared_dependencies)} declared dependencies in {os.path.basename(module.build_file_path)}.")

        # 2. Run Gradle dependencies task
        # Use module.path for cwd if it's a submodule, project_root if it's the root module
        # The gradle command itself should specify the module context e.g. :submodule:dependencies
        # So, the cwd for subprocess should always be project_root.
        gradle_output = run_gradle_dependencies(project_root, module.name) # module.path was module.name before
        if gradle_output:
            module.resolved_dependencies = parse_gradle_dependencies_output(gradle_output)
            # Count total resolved dependencies for a quick check
            count = sum(len(deps) for deps in module.resolved_dependencies.values())
            print(f"  Parsed {count} root resolved dependencies from 'gradle dependencies' output across various configurations.")
        else:
            print(f"  Skipping dependency resolution for {module.name} due to Gradle error.")
            # Add a partial report indicating failure for this module
            analyzer = DependencyAnalyzer(module) # Still create analyzer to get module name in report
            analyzer.report["notes"].append("Failed to retrieve or parse 'gradle dependencies' output.")
            full_analysis_report.append(analyzer.get_report())
            continue


        # 3. Scan Java/Kotlin files for imports
        src_paths_to_scan = []
        if os.path.exists(module.src_main_java_path):
            src_paths_to_scan.append(module.src_main_java_path)
        if os.path.exists(module.src_main_kotlin_path):
            src_paths_to_scan.append(module.src_main_kotlin_path)
        
        if src_paths_to_scan:
            module.imported_classes = scan_java_kotlin_files(src_paths_to_scan)
            print(f"  Found {len(module.imported_classes)} unique imports in .java/.kt files.")
        else:
            print(f"  No src/main/java or src/main/kotlin found for module {module.name}.")


        # 4. Analyze
        analyzer = DependencyAnalyzer(module)
        analyzer.analyze()
        full_analysis_report.append(analyzer.get_report())

    # 5. Print full report
    print_analysis_report(full_analysis_report)


if __name__ == "__main__":
    main()
