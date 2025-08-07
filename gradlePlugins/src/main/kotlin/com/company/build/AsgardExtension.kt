package com.company.build

import org.gradle.api.provider.Property

interface AsgardExtension {
    val java8: Property<Boolean>
    val java17: Property<Boolean>
    val java21: Property<Boolean>
    val buildType: Property<String>
    val applicationMainClass: Property<String>
}
