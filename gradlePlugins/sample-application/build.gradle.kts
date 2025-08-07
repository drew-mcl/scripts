plugins {
    id("com.company.build.asgard-java")
}

group = "com.example"
version = "1.0.0"

repositories {
    mavenCentral()
}

dependencies {
    testImplementation("junit:junit:4.13.2")
}

// Example configuration for an application
asgard {
    java17 = true
    buildType = "application"
    applicationMainClass = "com.example.SampleApplication"
}
