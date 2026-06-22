package com.noteflow.users;

import java.util.UUID;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

@Service
public class DevUserService {
    private final UUID devUserId;
    private final UserAccountRepository users;

    public DevUserService(@Value("${noteflow.dev.user-id}") UUID devUserId, UserAccountRepository users) {
        this.devUserId = devUserId;
        this.users = users;
    }

    public UUID currentUserId() {
        users.findById(devUserId)
            .orElseGet(() -> users.save(new UserAccount(devUserId, "dev@noteflow.local", "Development User")));
        return devUserId;
    }
}
