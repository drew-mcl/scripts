package com.company.build

import org.gradle.api.Plugin
import org.gradle.api.Project
import org.gradle.api.plugins.ApplicationPlugin
import org.gradle.api.plugins.JavaPlugin
import org.gradle.api.plugins.JavaPluginExtension
import org.gradle.api.tasks.testing.Test
import org.gradle.jvm.toolchain.JavaLanguageVersion
import org.gradle.kotlin.dsl.apply
import org.gradle.kotlin.dsl.configure
import org.gradle.kotlin.dsl.create
import org.gradle.kotlin.dsl.withType

class AsgardJavaPlugin : Plugin<Project> {
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
        
        // Apply multi-Java version plugin
        project.plugins.apply(MultiJavaVersionPlugin::class.java)
        
        // Configure Java toolchain based on settings
        project.afterEvaluate {
            configureJavaToolchain(project, extension)
            configureBuildType(project, extension)
            configurePublishing(project)
        }
    }
    
    private fun configureJavaToolchain(project: Project, extension: AsgardExtension) {
        project.configure<JavaPluginExtension> {
            // Default to Java 17
            toolchain {
                languageVersion.set(JavaLanguageVersion.of(17))
            }
        }
        
        // Configure test tasks to use the same toolchain
        project.tasks.withType<Test> {
            useJUnitPlatform()
        }
    }
    
    private fun configureBuildType(project: Project, extension: AsgardExtension) {
        project.afterEvaluate {
            val buildType = extension.buildType.get()
            
            when (buildType) {
                "application" -> {
                    // Apply the application plugin
                    project.plugins.apply(AsgardApplicationPlugin::class.java)
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
    
    private fun configurePublishing(project: Project) {
        project.publishing {
            publications {
                create<org.gradle.api.publish.maven.MavenPublication>("maven") {
                    from(project.components["java"])
                }
            }
        }
    }
}
