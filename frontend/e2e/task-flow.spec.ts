import { expect, test } from "@playwright/test";

const task = {
  task_id: "task-e2e",
  tenant_id: "tenant-e2e",
  status: "completed",
  result: "Grounded portfolio answer",
  report: {
    answer: "Grounded portfolio answer",
    claims: [{ text: "Verified claim", chunk_ids: ["chunk-e2e"] }],
    grounded: true,
    dropped_citations: 0,
    notes: [],
  },
  knowledge_hits: [
    {
      chunk_id: "chunk-e2e",
      score: 0.98,
      snippet: "Verified source material",
      source: { kb_id: "kb-e2e", document_id: "doc-e2e", title: "Source title" },
    },
  ],
  external_sources: [],
  messages: [],
};

test("authenticates, creates a task, and renders a grounded report", async ({ page }) => {
  let created = false;
  let submittedBody: Record<string, unknown> | undefined;
  let submittedAuthorization = "";

  await page.route("**/api/v1/tasks", async (route) => {
    const request = route.request();
    if (request.method() === "POST") {
      created = true;
      submittedBody = request.postDataJSON() as Record<string, unknown>;
      submittedAuthorization = request.headers().authorization ?? "";
      await route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify(task) });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(created ? [task] : []),
    });
  });
  await page.route("**/api/v1/tasks/task-e2e/events/ticket", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ticket: "x".repeat(32), expires_in: 60 }),
    }),
  );
  await page.route("**/api/v1/tasks/task-e2e/events?ticket=*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: 'event: completed\ndata: {"type":"completed"}\n\n',
    }),
  );
  await page.route("**/api/v1/tasks/task-e2e", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(task) }),
  );

  await page.goto("/");
  await page.getByPlaceholder("Bearer token").fill("dev:tenant-e2e:user-e2e:admin");
  await page.getByRole("button", { name: "Save" }).click();

  await expect(page.getByRole("heading", { name: "New Task" })).toBeVisible();
  await page.getByPlaceholder("Ask a research question...").fill("Show grounded evidence");
  await page.getByLabel(/allow external literature search/).check();
  await page.getByRole("button", { name: "Create task" }).click();

  await expect(page.getByText("Grounded portfolio answer")).toBeVisible();
  await expect(page.getByText("Verified claim")).toBeVisible();
  await expect(page.getByText("chunk-e2e", { exact: true })).toBeVisible();
  expect(submittedAuthorization).toBe("Bearer dev:tenant-e2e:user-e2e:admin");
  expect(submittedBody).toMatchObject({
    input: "Show grounded evidence",
    allow_external: true,
    ingest_external: false,
  });
});
