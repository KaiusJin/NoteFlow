package com.noteflow.tasks;

/** Starts a demand-driven worker runtime when asynchronous work is queued. */
public interface WorkerJobLauncher {
    boolean configured();

    String coordinationKey();

    void launch();
}
