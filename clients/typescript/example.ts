/**
 * Example: drive the tts_serve API with the GENERATED, typed TypeScript types.
 *
 *   npm i openapi-fetch        # tiny typed fetch wrapper
 *   npx tsx clients/typescript/example.ts 'https://youtu.be/3Amlu4y94Ho'
 *
 * `paths` is generated from /openapi.json (clients/typescript/api.ts) — every
 * request/response below is fully typed and checked at compile time.
 */
import createClient from "openapi-fetch";
import type { paths } from "./api";

const client = createClient<paths>({ baseUrl: "http://localhost:8090" });

async function main() {
  const source = process.argv[2] ?? "https://youtu.be/3Amlu4y94Ho";

  // POST /v1/tasks — body type is CreateTaskRequest, response is TaskRef
  const { data: ref, error } = await client.POST("/v1/tasks", {
    body: { source, clip: "0-20" },
  });
  if (error || !ref) throw new Error(`create failed: ${JSON.stringify(error)}`);
  console.log("queued:", ref.task_id, ref.status);

  // poll GET /v1/tasks/{tid} — response is TaskStatus
  let status = "queued";
  while (!["done", "failed", "cancelled"].includes(status)) {
    await new Promise((r) => setTimeout(r, 5000));
    const { data } = await client.GET("/v1/tasks/{tid}", {
      params: { path: { tid: ref.task_id } },
    });
    status = data?.status ?? "failed";
    console.log(`  status=${status} stage=${data?.stage}`);
  }

  if (status === "done") {
    // GET /v1/tasks/{tid}/artifact -> zip bytes
    const res = await fetch(`http://localhost:8090/v1/tasks/${ref.task_id}/artifact`);
    const buf = Buffer.from(await res.arrayBuffer());
    require("fs").writeFileSync(`/tmp/${ref.task_id}.zip`, buf);
    console.log(`artifact saved (${buf.length} bytes)`);
  }
}

main().catch((e) => { console.error(e); process.exit(1); });
