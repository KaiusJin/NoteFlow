package com.noteflow;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableScheduling;

@SpringBootApplication
@EnableScheduling
public class NoteflowApiApplication {
    public static void main(String[] args) {
        SpringApplication.run(NoteflowApiApplication.class, args);
    }
}
