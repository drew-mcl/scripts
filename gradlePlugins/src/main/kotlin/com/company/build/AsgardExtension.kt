package com.company.build

import org.gradle.api.provider.Property
import org.gradle.api.provider.ListProperty

interface AsgardExtension {
    val java8: Property<Boolean>
    val java17: Property<Boolean>
    val java21: Property<Boolean>
    val buildType: Property<String>
    val applicationMainClass: Property<String>
    val enableCodeQuality: Property<Boolean>
    val nativeTools: ListProperty<String>
}
