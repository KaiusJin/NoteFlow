const { defineConfig } = require("@playwright/test");

module.exports = defineConfig({
  testDir: "./specs",
  use: { baseURL: "http://127.0.0.1:4173" },
  webServer: {
    command: "python3 -m http.server 4173 --bind 127.0.0.1 --directory ../../apps/web",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: true
  }
});
