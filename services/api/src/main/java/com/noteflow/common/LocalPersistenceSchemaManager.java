package com.noteflow.common;

import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.core.annotation.Order;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

/** Migrates the old account-oriented schema to one local workspace. */
@Component
@Order(1000)
public class LocalPersistenceSchemaManager implements ApplicationRunner {
    private final JdbcTemplate jdbc;

    public LocalPersistenceSchemaManager(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    public void run(ApplicationArguments args) {
        jdbc.execute("""
            DO $$
            DECLARE dependency RECORD;
            BEGIN
              IF to_regclass('users') IS NULL THEN RETURN; END IF;
              FOR dependency IN
                SELECT conrelid::regclass AS table_name, conname
                  FROM pg_constraint
                 WHERE contype='f' AND confrelid='users'::regclass
              LOOP
                EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', dependency.table_name, dependency.conname);
              END LOOP;
              DROP TABLE users;
            END $$
            """);
    }
}
