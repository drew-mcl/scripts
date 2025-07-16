// build.gradle.kts  (root)
import groovy.json.JsonOutput
import org.gradle.api.DefaultTask
import org.gradle.api.artifacts.ProjectDependency
import org.gradle.api.tasks.*
import org.gradle.kotlin.dsl.*

/* ──────────────────────────────────────────────────────────────────────────
   Cacheable task: ./gradlew exportDeps
   Writes build/projects.json that the Go depgraph tool consumes
   ────────────────────────────────────────────────────────────────────────── */
@CacheableTask
abstract class ExportDepsTask : DefaultTask() {

    @get:OutputFile
    abstract val output: RegularFileProperty

    /* build scripts participate in up-to-date & cache key */
    @get:InputFiles
    @PathSensitive(PathSensitivity.RELATIVE)
    val buildScripts = project.layout.files(
        sequence {
            yield(project.rootProject.buildFile)
            project.subprojects.forEach { yield(it.buildFile) }
        }.toList()
    )

    /* if you ever want an explicit deployable flag, add it to inputs */
    @get:Input
    val deployableDirs = project.provider { "apps/" }   // cache busts if you rename

    @TaskAction
    fun generate() {
        val graph = mutableMapOf<String, Map<String, Any?>>()

        /* class-path scopes we care about */
        val confNames = listOf(
            "api",
            "implementation",
            "compileOnly",
            "runtimeOnly",
            "testImplementation"
        )

        project.subprojects.forEach { p ->
            val relDir = project.rootDir.toPath()
                .relativize(p.projectDir.toPath())
                .toString()
                .replace('\\', '/')           // Windows → POSIX

            /** AUTO-deployable if it’s under apps/ */
            val isDeployable = relDir.startsWith("apps/")

            /* collect unique project deps across all configs */
            val deps = confNames
                .mapNotNull { p.configurations.findByName(it) }
                .flatMap { cfg ->
                    cfg.allDependencies.withType(ProjectDependency::class)
                }
                .map { it.dependencyProject.path }
                .toSet()
                .sorted()

            graph[p.path] = mapOf(
                "projectDir"   to relDir,
                "dependencies" to deps,
                "deployable"   to isDeployable
            )
        }

        val pretty = JsonOutput.prettyPrint(JsonOutput.toJson(graph))
        output.get().asFile.apply {
            parentFile.mkdirs()
            writeText(pretty)
        }
        logger.lifecycle("🔹 wrote {}", output.get().asFile.relativeTo(project.rootDir))
    }
}

/* register the task; output lives in build/projects.json */
tasks.register<ExportDepsTask>("exportDeps") {
    group = "ci"
    description = "Serialises inter-project dependency graph for CI tooling"
    output.set(layout.buildDirectory.file("projects.json"))
}