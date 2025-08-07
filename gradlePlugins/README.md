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
    
    // Code quality (default: false)
    enableCodeQuality = true   // Enables PMD, Checkstyle, and JaCoCo
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

// Note: For application type, you need to manually configure the main class
// The plugin will log the required configuration during build
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

## Features

### Multi-Java Version Support
- **Single Version**: Creates a standard JAR (e.g., `project-name-1.0.0.jar`)
- **Multiple Versions**: Creates versioned JARs (e.g., `project-name-1.0.0-java8.jar`, `project-name-1.0.0-java17.jar`)

### Code Quality Support
When `enableCodeQuality = true` is set, the plugin automatically configures:
- **PMD**: Static code analysis with custom ruleset
- **Checkstyle**: Code style enforcement
- **JaCoCo**: Code coverage reporting

### Test Configuration
For Java 17 projects, the plugin provides guidance for test configuration including:
- `--add-opens` flags for module access
- System properties for consistent test execution

## Current Limitations

Due to Gradle API compatibility constraints with Java 17, the following limitations apply:

1. **Application Main Class**: For `buildType = "application"`, the main class must be configured manually in the build script. The plugin will log the required configuration during build.

2. **Test Configuration**: Java 17 test configuration (JVM args and system properties) must be added manually to the build script. The plugin provides guidance during build.

3. **Code Quality Rules**: PMD and Checkstyle rules are provided as defaults but can be customized by placing custom rule files in `gradle/pmd/` and `gradle/checkstyle/` directories.

These limitations are being addressed in future versions of the plugin.
