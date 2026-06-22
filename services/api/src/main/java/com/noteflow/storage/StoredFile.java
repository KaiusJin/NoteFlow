package com.noteflow.storage;

public record StoredFile(String storagePath, String contentType, long size) {
}
