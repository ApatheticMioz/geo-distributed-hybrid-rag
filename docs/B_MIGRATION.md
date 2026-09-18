# Node B one-time migration — repo restructure cutover

Commit `1d76c8e` (2026-09-18) moved `node_B/` to `systems/node_b/` in the
repository. Git only moves **tracked** files. Node B's clone carries large
**untracked** state inside the old directory, so its next `git pull` would
leave that state behind at `node_B\` while the code appears under
`systems\node_b\` — and the gateway would then run from a stale half-empty
tree (or fail: the tracked sources it needs are gone from the old path).

Do the steps below **in order, on Node B**, from PowerShell. Stop between
steps if anything errors. Run from the clone root
`D:\FAST\Semester6\NLP\Project_Laptop`.

## 1. Stop services first (they hold the dirs we move)

```powershell
schtasks /End /TN pdc_gateway
docker stop qdrant_node_b
```

## 2. Pull the restructure

```powershell
git pull
```

After this, `node_B\` still exists on disk with only untracked leftovers;
tracked code now lives under `systems\node_b\`.

## 3. Move the untracked state into the new tree

```powershell
Move-Item node_B\implementation\.venv systems\node_b\implementation\.venv
Move-Item node_B\implementation\data systems\node_b\implementation\data
Move-Item node_B\qdrant_storage systems\node_b\qdrant_storage
Move-Item node_B\snapshots systems\node_b\snapshots -ErrorAction SilentlyContinue
```

(Any "already exists" or missing-dir error: inspect before continuing —
do not copy blindly. `benchmarks\` leftovers under
`node_B\implementation\` can be deleted; telemetry there is non-authoritative.)

## 4. Restart services from the new tree

`start_gateway.bat` (repo root, updated by the pull) already points at
`systems\node_b\implementation`. The scheduled task runs it by its fixed
path, so no schtasks change is needed.

```powershell
docker compose -f systems\node_b\docker-compose.yml up -d
schtasks /Run /TN pdc_gateway
```

Note: compose mounts are relative (`./qdrant_storage`), so the container
must be recreated from the **new** compose path (step 4 does that).

## 5. Verify

```powershell
curl.exe -s http://127.0.0.1:8000/health
docker ps --filter name=qdrant_node_b
git log --oneline -1
```

Expect: health JSON with `"role":"hybrid_retrieval_gateway"`, the qdrant
container `Up`, and the latest pushed commit.

### Fallback: if the moved venv misbehaves

Windows venvs usually survive a directory move, but if the gateway fails to
start with import errors:

```powershell
Remove-Item -Recurse -Force systems\node_b\implementation\.venv
python -m venv systems\node_b\implementation\.venv
systems\node_b\implementation\.venv\Scripts\python.exe -m pip install -r systems\node_b\implementation\requirements.txt
schtasks /Run /TN pdc_gateway
```

Then re-verify step 5.
