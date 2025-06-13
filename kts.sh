// ---------- imports ----------
import com.google.gson.GsonBuilder
import org.gradle.api.DefaultTask
import org.gradle.api.artifacts.ProjectDependency
import org.gradle.api.provider.MapProperty
import org.gradle.api.tasks.*
import java.io.Serializable
import java.nio.file.Files
import java.nio.file.StandardOpenOption

// ---------- data class ----------
data class ProjectNode(
    val projectDir: String,
    val dependencies: List<String>
) : Serializable           // ← CC & build-cache need this

// ---------- task ----------
@CacheableTask             // enables local & remote build-cache
abstract class DependencyGraphTask : DefaultTask() {

    // inputs -----------------------------------------------------------------
    @get:Input
    abstract val graph: MapProperty<String, ProjectNode>   // frozen model

    @get:InputFiles
    @get:PathSensitive(PathSensitivity.RELATIVE)
    val buildScripts = project.objects.fileCollection().from(
        project.rootProject.allprojects.map { it.buildFile } +
        listOf(
            project.rootProject.file("settings.gradle.kts"),
            project.rootProject.file("gradle.properties")
        )
    )

    // output -----------------------------------------------------------------
    @get:OutputFile
    val output = project.layout.buildDirectory.file("dependency-graph.json")

    // action -----------------------------------------------------------------
    @TaskAction
    fun writeJson() {
        val gson = GsonBuilder().setPrettyPrinting().create()
        Files.createDirectories(output.get().asFile.parentFile.toPath())
        Files.writeString(
            output.get().asFile.toPath(),
            gson.toJson(graph.get()),
            StandardOpenOption.CREATE,
            StandardOpenOption.TRUNCATE_EXISTING,
            StandardOpenOption.WRITE
        )
        logger.lifecycle("Dependency graph written to ${output.get().asFile}")
    }
}

// ---------- task registration (configuration phase) ----------
tasks.register<DependencyGraphTask>("exportDependencyGraph") {

    group = "ci"
    description = "Exports { :projectPath -> { projectDir, [direct project paths] } }"

    val rootDir = project.rootProject.projectDir

    // build the immutable graph while the model is still mutable
    val snapshot = project.rootProject.allprojects.associate { p ->
        val directDeps = p.configurations.flatMap { cfg ->
            cfg.dependencies.withType(ProjectDependency::class.java)
        }.map { it.dependencyProject.path }.distinct()

        p.path to ProjectNode(
            p.projectDir.relativeTo(rootDir).invariantSeparatorsPath,
            directDeps
        )
    }

    graph.putAll(snapshot)   // make it task input (CC-safe)
}