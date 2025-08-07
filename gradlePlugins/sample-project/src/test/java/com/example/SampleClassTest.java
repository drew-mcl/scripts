package com.example;

import org.junit.Test;
import static org.junit.Assert.*;

/**
 * Test class for SampleClass.
 */
public class SampleClassTest {
    
    @Test
    public void testGetGreeting() {
        SampleClass sample = new SampleClass();
        String greeting = sample.getGreeting();
        assertEquals("Hello from Asgard Java Plugin!", greeting);
    }
}
