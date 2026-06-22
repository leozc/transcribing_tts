/**
 * Example: drive the tts_serve API with the GENERATED, typed TypeScript types.
 *
 *   npm i openapi-fetch        # tiny typed fetch wrapper
 *   npx tsx clients/typescript/example.ts 'https://youtu.be/3Amlu4y94Ho'
 *
 * `paths` is generated from /openapi.json (clients/typescript/api.ts) — every
 * request/response below is fully typed and checked at compile time.
 *
 * Auth: register a client_id once -> secret client_key; send it as X-Client-Key to
 * enqueue and to LIST YOUR OWN tasks. A per-task pull_token (returned at create)
 * also reaches a single task (handy for sharing one result).
 */
import createClient from "openapi-fetch";
import type { paths } from "./api";

const client = createClient<paths>({ baseUrl: "http://localhost:39999" });
const CLIENT_ID = "alice";

async function main() {
  const source = process.argv[2] ?? "https://youtu.be/3Amlu4y94Ho";

  // 0. register once -> secret client_key (store it; this is your identity)
  const { data: creds } = await client.POST("/v1/clients", { body: { client_id: CLIENT_ID } });
  const key = creds!.client_key;
  console.log("registered:", creds!.client_id);

  // 1. enqueue as the authenticated client (X-Client-Key)
  const { data: ref, error } = await client.POST("/v1/tasks", {
    params: { header: { "x-client-key": key } },
    body: { source, client_id: CLIENT_ID, clip: "0-20" },
  });
  if (error || !ref) throw new Error(`create failed: ${JSON.stringify(error)}`);
  const token = ref.pull_token!; // shares this one task; the client key reaches all yours
  console.log("queued:", ref.task_id, ref.status);

  // list YOUR OWN jobs anytime with the client key
  const { data: mine } = await client.GET("/v1/tasks", {
    params: { header: { "x-client-key": key } },
  });
  console.log("my jobs:", mine?.tasks.map((t) => [t.id, t.status]));

  // 2. poll — either the client key or the per-task token works; we use the token here
  let status = "queued";
  while (!["done", "failed", "cancelled"].includes(status)) {
    await new Promise((r) => setTimeout(r, 5000));
    const { data } = await client.GET("/v1/tasks/{tid}", {
      params: { path: { tid: ref.task_id }, header: { "x-task-token": token } },
    });
    status = data?.status ?? "failed";
    console.log(`  status=${status} stage=${data?.stage}`);
  }

  if (status === "done") {
    // 3. GET /v1/tasks/{tid}/artifact -> zip bytes
    const res = await fetch(`http://localhost:39999/v1/tasks/${ref.task_id}/artifact`,
                            { headers: { "X-Task-Token": token } });
    const buf = Buffer.from(await res.arrayBuffer());
    require("fs").writeFileSync(`/tmp/${ref.task_id}.zip`, buf);
    console.log(`artifact saved (${buf.length} bytes)`);
  }
}

main().catch((e) => { console.error(e); process.exit(1); });
