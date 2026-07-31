package com.noteflow.common;

import java.util.List;

public record CursorPage<T>(List<T> items, String nextCursor) {
}
