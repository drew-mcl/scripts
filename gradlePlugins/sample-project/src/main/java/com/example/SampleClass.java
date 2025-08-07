package com.example;

/**
 * Sample class to demonstrate the Asgard Java plugin functionality.
 */
public class SampleClass {
    
    /**
     * Returns a greeting message.
     * 
     * @return A greeting string
     */
    public String getGreeting() {
        return "Hello from Asgard Java Plugin!";
    }
    
    /**
     * Main method for application type demonstration.
     * 
     * @param args Command line arguments
     */
    public static void main(String[] args) {
        SampleClass sample = new SampleClass();
        System.out.println(sample.getGreeting());
    }
}
