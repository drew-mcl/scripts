package com.company.build

import org.gradle.api.Plugin
import org.gradle.api.Project
import org.gradle.kotlin.dsl.apply
import org.gradle.kotlin.dsl.configure

class AsgardCodeQualityPlugin : Plugin<Project> {
    override fun apply(project: Project) {
        // Apply code quality plugins
        project.apply(plugin = "checkstyle")
        project.apply(plugin = "pmd")
        project.apply(plugin = "jacoco")
        
        project.afterEvaluate {
            configureCheckstyle(project)
            configurePmd(project)
            configureJacoco(project)
        }
    }
    
    private fun configureCheckstyle(project: Project) {
        project.configure<org.gradle.api.plugins.quality.CheckstyleExtension> {
            toolVersion = "10.12.5"
            configFile = project.file("gradle/checkstyle/checkstyle.xml")
        }
        
        // Make checkstyle part of the build
        project.tasks.named("build").configure {
            dependsOn("checkstyleMain", "checkstyleTest")
        }
    }
    
    private fun configurePmd(project: Project) {
        project.configure<org.gradle.api.plugins.quality.PmdExtension> {
            toolVersion = "6.55.0"
            ruleSetFiles = project.files("gradle/pmd/ruleset.xml")
            ruleSets = emptyList() // Use custom ruleset file instead
        }
        
        // Make PMD part of the build
        project.tasks.named("build").configure {
            dependsOn("pmdMain", "pmdTest")
        }
    }
    
    private fun configureJacoco(project: Project) {
        // Configure JaCoCo test task
        project.tasks.named("jacocoTestReport").configure {
            dependsOn("test")
        }
        
        // Make JaCoCo part of the build
        project.tasks.named("build").configure {
            dependsOn("jacocoTestReport")
        }
    }
}
