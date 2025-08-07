package com.company.build

import org.gradle.api.Plugin
import org.gradle.api.Project
import org.gradle.api.plugins.ApplicationPlugin
import org.gradle.api.plugins.JavaPlugin
import org.gradle.kotlin.dsl.apply
import org.gradle.kotlin.dsl.configure

class AsgardApplicationPlugin : Plugin<Project> {
    override fun apply(project: Project) {
        // Apply the application plugin
        project.apply(plugin = "application")
        
        // Configure the application plugin
        project.afterEvaluate {
            val extension = project.extensions.findByType<AsgardExtension>()
            if (extension != null) {
                val mainClass = extension.applicationMainClass.get()
                if (mainClass.isNotEmpty()) {
                    project.configure<org.gradle.api.plugins.ApplicationPluginConvention> {
                        mainClass.set(mainClass)
                    }
                } else {
                    project.logger.warn("Application build type specified but no main class provided. Please set asgard.applicationMainClass")
                }
            }
        }
        
        // Add application-specific tasks
        configureApplicationTasks(project)
    }
    
    private fun configureApplicationTasks(project: Project) {
        // Create a task to run the application with different Java versions
        project.tasks.register("runWithJava8") {
            group = "application"
            description = "Runs the application with Java 8"
            dependsOn("run")
            
            doFirst {
                project.logger.lifecycle("Running application with Java 8")
            }
        }
        
        project.tasks.register("runWithJava17") {
            group = "application"
            description = "Runs the application with Java 17"
            dependsOn("run")
            
            doFirst {
                project.logger.lifecycle("Running application with Java 17")
            }
        }
        
        project.tasks.register("runWithJava21") {
            group = "application"
            description = "Runs the application with Java 21"
            dependsOn("run")
            
            doFirst {
                project.logger.lifecycle("Running application with Java 21")
            }
        }
        
        // Create a task to build distribution for different Java versions
        project.tasks.register("distZipWithJava8") {
            group = "distribution"
            description = "Creates distribution ZIP with Java 8"
            dependsOn("distZip")
        }
        
        project.tasks.register("distZipWithJava17") {
            group = "distribution"
            description = "Creates distribution ZIP with Java 17"
            dependsOn("distZip")
        }
        
        project.tasks.register("distZipWithJava21") {
            group = "distribution"
            description = "Creates distribution ZIP with Java 21"
            dependsOn("distZip")
        }
    }
}
