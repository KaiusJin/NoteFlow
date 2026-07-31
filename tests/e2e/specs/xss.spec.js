const { test, expect } = require("@playwright/test");

test("rich renderer escapes active HTML and unsafe attributes", async ({ page }) => {
  await page.goto("/");
  const rendered = await page.evaluate(async () => {
    const { renderRich } = await import("/rich-renderer.js");
    return renderRich('<img src=x onerror="window.__xss=1"><script>window.__xss=2</script>');
  });
  await page.locator("#view-root").evaluate((node, html) => {
    node.innerHTML = html;
  }, rendered);
  await expect(page.locator("#view-root script")).toHaveCount(0);
  await expect(page.locator("#view-root img")).toHaveCount(0);
  expect(await page.evaluate(() => window.__xss)).toBeUndefined();
});
