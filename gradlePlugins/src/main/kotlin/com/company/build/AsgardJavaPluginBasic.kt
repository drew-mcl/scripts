package com.company.build

import org.gradle.api.Plugin
import org.gradle.api.Project
import org.gradle.api.plugins.JavaPlugin
import org.gradle.api.plugins.JavaPluginExtension
import org.gradle.jvm.toolchain.JavaLanguageVersion
import org.gradle.kotlin.dsl.apply
import org.gradle.kotlin.dsl.configure
import org.gradle.kotlin.dsl.create

class AsgardJavaPluginBasic : Plugin<Project> {
    override fun apply(project: Project) {
        // Apply core plugins
        project.apply(plugin = "java")
        project.apply(plugin = "maven-publish")
        
        // Create and configure the extension
        val extension = project.extensions.create<AsgardExtension>("asgard")
        
        // Set default values
        extension.java8.convention(false)
        extension.java17.convention(true)
        extension.java21.convention(false)
        extension.buildType.convention("library")
        extension.applicationMainClass.convention("")
        extension.enableCodeQuality.convention(false)
        extension.nativeTools.convention(project.objects.listProperty(String::class.java).empty())
        
        // Configure Java toolchain based on settings
        project.afterEvaluate {
            configureJavaToolchain(project, extension)
            configureBuildType(project, extension)
            configureMultiJavaVersionBuild(project, extension)
            configureTestSettings(project, extension)
            configureNativeTools(project, extension)
            
            // Apply code quality plugin if enabled
            if (extension.enableCodeQuality.get()) {
                project.plugins.apply(AsgardCodeQualityPlugin::class.java)
            }
        }
    }
    
    private fun configureJavaToolchain(project: Project, extension: AsgardExtension) {
        project.configure<JavaPluginExtension> {
            // Default to Java 17
            toolchain {
                languageVersion.set(JavaLanguageVersion.of(17))
            }
        }
    }
    
    private fun configureBuildType(project: Project, extension: AsgardExtension) {
        project.afterEvaluate {
            val buildType = extension.buildType.get()
            
            when (buildType) {
                "application" -> {
                    // Apply the application plugin
                    project.apply(plugin = "application")
                    val mainClass = extension.applicationMainClass.get()
                    if (mainClass.isNotEmpty()) {
                        // Log the main class for manual configuration
                        project.logger.lifecycle("Application plugin applied. Please configure mainClass in your build script:")
                        project.logger.lifecycle("application { mainClass.set(\"$mainClass\") }")
                    }
                }
                "library" -> {
                    // Apply the java-library plugin for better library support
                    project.apply(plugin = "java-library")
                }
                else -> {
                    project.logger.warn("Unknown build type: $buildType. Using default library configuration.")
                    project.apply(plugin = "java-library")
                }
            }
        }
    }
    
    private fun configureMultiJavaVersionBuild(project: Project, extension: AsgardExtension) {
        val javaVersions = mutableListOf<Int>()
        
        if (extension.java8.get()) javaVersions.add(8)
        if (extension.java17.get()) javaVersions.add(17)
        if (extension.java21.get()) javaVersions.add(21)
        
        // If no specific versions are set, default to Java 17
        if (javaVersions.isEmpty()) {
            javaVersions.add(17)
        }
        
        // Configure the main jar task based on version configuration
        if (javaVersions.size == 1) {
            // Single version - use the main jar task
            val version = javaVersions.first()
            project.tasks.named("jar", org.gradle.api.tasks.bundling.Jar::class.java).configure {
                archiveClassifier.set("")
                manifest {
                    attributes(mapOf(
                        "Java-Version" to version.toString(),
                        "Created-By" to "Asgard Build Plugin"
                    ))
                }
            }
        } else {
            // Multiple versions - create separate JARs with version suffixes
            javaVersions.forEach { version ->
                project.tasks.register("jarJava${version}", org.gradle.api.tasks.bundling.Jar::class.java) {
                    group = "build"
                    description = "Creates a JAR for Java ${version}"
                    
                    archiveClassifier.set("java${version}")
                    
                    dependsOn("compileJava")
                    from(project.file("build/classes/java/main"))
                    
                    // Add Java version to manifest
                    manifest {
                        attributes(mapOf(
                            "Java-Version" to version.toString(),
                            "Created-By" to "Asgard Build Plugin"
                        ))
                    }
                }
            }
            
            // Disable the main jar task when multiple versions are specified
            project.tasks.named("jar", org.gradle.api.tasks.bundling.Jar::class.java).configure {
                enabled = false
            }
            
            // Make the individual jar tasks part of the build
            project.tasks.named("build").configure {
                dependsOn(javaVersions.map { "jarJava${it}" })
            }
        }
    }
    
    private fun configureTestSettings(project: Project, extension: AsgardExtension) {
        // Java 17 specific configuration
        if (extension.java17.get()) {
            project.logger.lifecycle("Java 17 enabled. Consider adding the following to your test configuration:")
            project.logger.lifecycle("""
                test {
                    jvmArgs(
                        "--add-opens=java.base/java.lang=ALL-UNNAMED",
                        "--add-opens=java.base/java.lang.reflect=ALL-UNNAMED",
                        "--add-opens=java.base/java.io=ALL-UNNAMED",
                        "--add-opens=java.base/java.util=ALL-UNNAMED",
                        "--add-opens=java.base/java.util.concurrent=ALL-UNNAMED",
                        "--add-opens=java.base/java.nio=ALL-UNNAMED",
                        "--add-opens=java.base/java.net=ALL-UNNAMED",
                        "--add-opens=java.base/java.text=ALL-UNNAMED",
                        "--add-opens=java.base/java.time=ALL-UNNAMED",
                        "--add-opens=java.base/java.math=ALL-UNNAMED"
                    )
                    systemProperty("java.awt.headless", "true")
                    systemProperty("file.encoding", "UTF-8")
                    systemProperty("user.timezone", "UTC")
                }
            """.trimIndent())
        }
    }
    
    private fun configureNativeTools(project: Project, extension: AsgardExtension) {
        val nativeTools = extension.nativeTools.get()
        
        if (nativeTools.isNotEmpty()) {
            // Create native tools directory
            val nativeDir = project.file("build/native")
            nativeDir.mkdirs()
            
            // Configure dependencies for native tools
            project.configurations.create("nativeTools") {
                isCanBeResolved = true
                isCanBeConsumed = false
            }
            
            // Add native tools to distribution if this is an application
            if (extension.buildType.get() == "application") {
                // Only configure distribution tasks if they exist
                project.tasks.findByName("distTar")?.let { distTar ->
                    distTar.dependsOn("copyNativeTools")
                }
                
                project.tasks.findByName("distZip")?.let { distZip ->
                    distZip.dependsOn("copyNativeTools")
                }
                
                // Create task to copy native tools to distribution
                project.tasks.register("copyNativeTools", org.gradle.api.tasks.Copy::class.java) {
                    group = "distribution"
                    description = "Copies native tools to distribution"
                    
                    from(nativeDir)
                    into("${project.buildDir}/tmp/dist/native")
                    
                    doFirst {
                        project.logger.lifecycle("Copying native tools to distribution...")
                    }
                }
            }
        }
    }
}
