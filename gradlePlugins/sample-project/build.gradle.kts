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

// Example configuration for a library with multiple Java versions
asgard {
    java8 = true
    java17 = true
    java21 = true
    buildType = "library"
}
