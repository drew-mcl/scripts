package com.company.build

import org.junit.Test
import org.junit.Assert.assertTrue

class AsgardJavaPluginTest {
    
    @Test
    fun `plugin class exists`() {
        // Simple test to verify the plugin class can be instantiated
        val plugin = AsgardJavaPluginBasic()
        assertTrue(plugin is AsgardJavaPluginBasic)
    }
}
