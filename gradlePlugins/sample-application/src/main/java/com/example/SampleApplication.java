package com.example;

/**
 * Sample application class to demonstrate the Asgard Java plugin application functionality.
 */
public class SampleApplication {
    
    /**
     * Main method for the application.
     * 
     * @param args Command line arguments
     */
    public static void main(String[] args) {
        System.out.println("Starting Sample Application...");
        System.out.println("Hello from Asgard Java Plugin Application!");
        
        if (args.length > 0) {
            System.out.println("Arguments provided:");
            for (String arg : args) {
                System.out.println("  - " + arg);
            }
        }
        
        System.out.println("Application completed successfully.");
    }
}
