#!/usr/bin/env python
"""Example: drive the tts_serve API with the GENERATED, typed Python client.

    pip install httpx attrs python-dateutil   # generated client runtime deps
    python clients/python_example.py 'https://youtu.be/3Amlu4y94Ho' --clip 0-20

Everything below is typed — CreateTaskRequest, TaskRef, TaskStatus are generated
from /openapi.json (see clients/gen.sh).
"""
import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from tts_serve_client.client import Client                                  # noqa: E402
from tts_serve_client.models import CreateTaskRequest                       # noqa: E402
from tts_serve_client.api.default import (                                  # noqa: E402
    create_task_v1_tasks_post as create_task,
    get_task_v1_tasks_tid_get as get_task,
    get_artifact_v1_tasks_tid_artifact_get as get_artifact,
)

BASE = "http://localhost:8090"


def main():
    source = sys.argv[1] if len(sys.argv) > 1 else "https://youtu.be/3Amlu4y94Ho"
    clip = sys.argv[3] if len(sys.argv) > 3 and sys.argv[2] == "--clip" else "0-20"
    client = Client(base_url=BASE)

    ref = create_task.sync(client=client, body=CreateTaskRequest(source=source, clip=clip))
    print("queued:", ref.task_id, ref.status)  # ref is a typed TaskRef

    while True:
        st = get_task.sync(client=client, tid=ref.task_id)  # typed TaskStatus
        print(f"  status={st.status} stage={st.stage}")
        if st.status in ("done", "failed", "cancelled"):
            break
        time.sleep(5)

    if st.status != "done":
        print("failed:", st.error)
        return
    resp = get_artifact.sync_detailed(client=client, tid=ref.task_id)  # bytes (zip)
    out = Path(f"/tmp/{ref.task_id}.zip")
    out.write_bytes(resp.content)
    with zipfile.ZipFile(out) as z:
        print("artifact files:", z.namelist())
        print("transcript head:\n", z.read("transcript.txt").decode()[:300])


if __name__ == "__main__":
    main()
