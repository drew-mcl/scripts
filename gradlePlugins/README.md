# Asgard Java Plugin

A Gradle plugin that provides a standardized build configuration for Java projects with support for multiple Java versions and application types.

## Architecture

The plugin is designed with a modular architecture:

- **AsgardJavaPlugin**: Main plugin that handles core Java functionality and multi-version builds
- **AsgardApplicationPlugin**: Extension plugin for application-specific functionality
- **MultiJavaVersionPlugin**: Handles building JARs for multiple Java versions
- **AsgardExtension**: Configuration interface for all plugin settings

This design allows for clean separation of concerns and easy extension of functionality.

## Features

- **Multiple Java Version Support**: Build JARs for Java 8, 17, and 21
- **Application Type Support**: Configure as library or application
- **Standardized Build Process**: Compile, test, and publish
- **Flexible Configuration**: Easy configuration through extension

## Usage

### Basic Usage

```kotlin
plugins {
    id("com.company.build.asgard-java")
}

asgard {
    java17 = true
    java8 = true
    applicationType = "library"
}
```

### Configuration Options

```kotlin
asgard {
    // Java version support (default: java17 = true, others = false)
    java8 = true    // Build JAR for Java 8
    java17 = true   // Build JAR for Java 17 (default)
    java21 = true   // Build JAR for Java 21
    
    // Build type (default: "library")
    buildType = "application"  // or "library"
    applicationMainClass = "com.example.MainClass"  // Required for application type
}
```

### Example Configurations

#### Library with Java 17 only (default)
```kotlin
plugins {
    id("com.company.build.asgard-java")
}
```

#### Library with multiple Java versions
```kotlin
plugins {
    id("com.company.build.asgard-java")
}

asgard {
    java8 = true
    java17 = true
    java21 = true
}
```

#### Application with specific main class
```kotlin
plugins {
    id("com.company.build.asgard-java")
}

asgard {
    buildType = "application"
    applicationMainClass = "com.example.MyApplication"
    java17 = true
}
```

## Tasks

The plugin provides the following tasks:

### Core Tasks
- `buildAllJavaVersions` - Builds JARs for all configured Java versions
- `jarJava8` - Creates JAR for Java 8
- `jarJava17` - Creates JAR for Java 17
- `jarJava21` - Creates JAR for Java 21
- `copyAllJars` - Copies all JARs to `build/libs` with version naming

### Application Tasks (when buildType = "application")
- `runWithJava8` - Runs the application with Java 8
- `runWithJava17` - Runs the application with Java 17
- `runWithJava21` - Runs the application with Java 21
- `distZipWithJava8` - Creates distribution ZIP with Java 8
- `distZipWithJava17` - Creates distribution ZIP with Java 17
- `distZipWithJava21` - Creates distribution ZIP with Java 21

## Publishing

The plugin automatically configures Maven publishing for the project. JARs are published with appropriate classifiers for each Java version.

## Build Output

When multiple Java versions are enabled, the plugin creates separate JARs:
- `project-name-java8.jar`
- `project-name-java17.jar`
- `project-name-java21.jar`

Each JAR includes the Java version in its manifest for identification.
