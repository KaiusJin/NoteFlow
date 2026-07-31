package com.noteflow.library;

public class ConcurrentNoteEditException extends RuntimeException {
    public ConcurrentNoteEditException() {
        super("Note changed since it was loaded; reload it before saving");
    }
}
