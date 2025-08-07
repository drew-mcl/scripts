package com.company.build

import org.gradle.api.Plugin
import org.gradle.api.Project
import org.gradle.api.artifacts.Configuration
import org.gradle.api.tasks.Copy
import org.gradle.api.tasks.bundling.Jar
import org.gradle.jvm.toolchain.JavaLanguageVersion
import org.gradle.kotlin.dsl.apply
import org.gradle.kotlin.dsl.configure
import org.gradle.kotlin.dsl.register

class MultiJavaVersionPlugin : Plugin<Project> {
    override fun apply(project: Project) {
        project.afterEvaluate {
            val extension = project.extensions.findByType<AsgardExtension>()
            if (extension != null) {
                setupMultiJavaVersionBuild(project, extension)
            }
        }
    }
    
    private fun setupMultiJavaVersionBuild(project: Project, extension: AsgardExtension) {
        val javaVersions = mutableListOf<Int>()
        
        if (extension.java8.get()) javaVersions.add(8)
        if (extension.java17.get()) javaVersions.add(17)
        if (extension.java21.get()) javaVersions.add(21)
        
        // If no specific versions are set, default to Java 17
        if (javaVersions.isEmpty()) {
            javaVersions.add(17)
        }
        
        // Create configurations for each Java version
        val configurations = javaVersions.associateWith { version ->
            project.configurations.create("java${version}RuntimeElements") {
                isCanBeResolved = false
                isCanBeConsumed = true
                attributes {
                    attribute(org.gradle.api.attributes.Attribute.of("org.gradle.jvm.version", String::class.java), version.toString())
                }
            }
        }
        
        // Create JAR tasks for each Java version
        javaVersions.forEach { version ->
            val jarTask = project.tasks.register<Jar>("jarJava${version}") {
                group = "build"
                description = "Creates a JAR for Java ${version}"
                
                archiveClassifier.set("java${version}")
                
                from(project.sourceSets.main.get().output)
                
                // Add Java version to manifest
                manifest {
                    attributes(
                        "Java-Version" to version.toString(),
                        "Created-By" to "Asgard Build Plugin"
                    )
                }
            }
            
            // Make the JAR available for consumption
            configurations[version]?.artifacts {
                add(configurations[version]!!.name, jarTask)
            }
        }
        
        // Create a task to build all versions
        project.tasks.register("buildAllJavaVersions") {
            group = "build"
            description = "Builds JARs for all configured Java versions"
            
            dependsOn(javaVersions.map { "jarJava${it}" })
        }
        
        // Create a task to copy all JARs to a common location
        project.tasks.register<Copy>("copyAllJars") {
            group = "build"
            description = "Copies all Java version JARs to build/libs"
            
            dependsOn(javaVersions.map { "jarJava${it}" })
            
            from(javaVersions.map { project.tasks.named("jarJava${it}") })
            into("${project.buildDir}/libs")
            
            rename { fileName ->
                fileName.replace(".jar", "-${project.version}.jar")
            }
        }
    }
}
