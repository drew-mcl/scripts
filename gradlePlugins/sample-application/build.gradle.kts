plugins {
    id("com.company.build.asgard-java")
}

group = "com.example"
version = "1.0.0"

repositories {
    mavenLocal()
    mavenCentral()
    gradlePluginPortal()
}

dependencies {
    testImplementation(libs.junit)
}

// Example configuration for an application
asgard {
    java17 = true
    java8 = true
    buildType = "application"
    applicationMainClass = "com.example.SampleApplication"
    enableCodeQuality = true
    nativeTools = listOf("journal", "uexe")
}


