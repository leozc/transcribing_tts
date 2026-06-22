#!/usr/bin/env python
"""Example: drive the tts_serve API with the GENERATED, typed Python client.

    pip install httpx attrs python-dateutil   # generated client runtime deps
    python clients/python_example.py 'https://youtu.be/3Amlu4y94Ho' --clip 0-20

Everything below is typed — ClientCreate, CreateTaskRequest, TaskRef, TaskStatus are
generated from /openapi.json (see clients/gen.sh).

Auth model: register a client_id once -> secret client_key. Send the key as
X-Client-Key to enqueue and to LIST YOUR OWN tasks. A single task is also reachable
by the per-task pull_token returned at create (handy for sharing one result).
"""
import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from tts_serve_client.client import Client                                  # noqa: E402
from tts_serve_client.models import ClientCreate, CreateTaskRequest         # noqa: E402
from tts_serve_client.api.default import (                                  # noqa: E402
    register_client_v1_clients_post as register_client,
    create_task_v1_tasks_post as create_task,
    list_tasks_v1_tasks_get as list_tasks,
    get_task_v1_tasks_tid_get as get_task,
    get_artifact_v1_tasks_tid_artifact_get as get_artifact,
)

BASE = "http://localhost:39999"
CLIENT_ID = "alice"


def main():
    source = sys.argv[1] if len(sys.argv) > 1 else "https://youtu.be/3Amlu4y94Ho"
    clip = sys.argv[3] if len(sys.argv) > 3 and sys.argv[2] == "--clip" else "0-20"
    client = Client(base_url=BASE)

    # 0. register once -> secret client_key (in practice: do this once, store the key)
    creds = register_client.sync(client=client, body=ClientCreate(client_id=CLIENT_ID))
    key = creds.client_key
    print("registered:", creds.client_id)

    # 1. enqueue as the authenticated client (X-Client-Key)
    ref = create_task.sync(client=client, x_client_key=key,
                           body=CreateTaskRequest(source=source, client_id=CLIENT_ID, clip=clip))
    token = ref.pull_token  # shares this one task; the client key reaches all your tasks
    print("queued:", ref.task_id, ref.status)

    # list YOUR OWN jobs anytime with the client key
    mine = list_tasks.sync(client=client, x_client_key=key)
    print("my jobs:", [(t.id, t.status) for t in mine.tasks])

    # 2. poll — either the client key or the per-task token works; we use the token here
    while True:
        st = get_task.sync(client=client, tid=ref.task_id, x_task_token=token)  # typed TaskStatus
        print(f"  status={st.status} stage={st.stage}")
        if st.status in ("done", "failed", "cancelled"):
            break
        time.sleep(5)

    if st.status != "done":
        print("failed:", st.error)
        return
    # 3. download the artifact zip
    resp = get_artifact.sync_detailed(client=client, tid=ref.task_id, x_task_token=token)
    out = Path(f"/tmp/{ref.task_id}.zip")
    out.write_bytes(resp.content)
    with zipfile.ZipFile(out) as z:
        print("artifact files:", z.namelist())
        print("transcript head:\n", z.read("transcript.txt").decode()[:300])


if __name__ == "__main__":
    main()
