# GaussianCircuit

Convert indoor room captures into collision-aware racing environments, generate closed circuits, and evaluate autonomous laps.

The current implementation includes the phase-one planning core, phase-two laptop-to-phone capture, and the first phase-three reconstruction pipeline. The planner reads a metric scene, treats unknown regions as blocked, searches a deterministic baseline family of circuits, validates complete-path clearance, and writes a JSON report plus SVG preview.

## Run locally

Requires Python 3.12+.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m gaussiancircuit examples/central_table.json --output outputs/central_table
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Open `outputs/central_table/preview.svg` after running the demo.

The baseline intentionally reports `no_candidate_found` rather than claiming a room is globally infeasible. It searches nested rounded rectangles, uses a conservative vehicle footprint, and does not yet validate dynamics.

## Phase 2: laptop and phone capture

The laptop creates a short room code such as `SJ8-K19`. The phone opens `/capture`, joins with that code, takes overlapping room photos, and uploads them into the desktop session. The desktop receives status changes over SSE. Finishing a capture writes an immutable reconstruction manifest for the later vision pipeline.

Run the API:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[api,dev]"
.\.venv\Scripts\python.exe -m uvicorn gaussiancircuit.api.app:app --reload --host 0.0.0.0 --port 8000
```

Run the web client in a second terminal:

```powershell
cd web
npm install
npm run dev
```

Use `http://localhost:5173` on the laptop. For a real phone on the same Wi-Fi network, open `http://<laptop-LAN-IP>:5173/capture`; the Vite development server proxies API calls to the local FastAPI process.

Local development uses SQLite and `data/captures`. The API is structured so hosted deployments can replace these with PostgreSQL and private S3-compatible storage. Pairing codes are temporary discovery codes; separate random credentials authorize desktop controls and phone uploads.

## Phase 3: reconstruction preparation

Finishing a capture now queues reconstruction automatically. JPEG, PNG, HEIC, and HEIF inputs are decoded, EXIF orientation is applied, and ordered high-quality JPEGs are written to `data/reconstructions/<capture-id>/images`. The preflight records dimensions, exposure, a lightweight sharpness signal, and warnings in `status.json`.

If COLMAP is installed under `tools/colmap`, configured through `GAUSSIANCIRCUIT_COLMAP`, or available on `PATH`, the worker runs CPU-safe feature extraction, sequential matching, and sparse mapping. The proof-of-concept then hands the aligned, undistorted images to OpenMVS for CPU dense reconstruction and meshing. OpenMVS is auto-detected under `tools/openmvs` or can be set with `GAUSSIANCIRCUIT_OPENMVS`. Its conservative defaults cap images at 1600 pixels and use four threads so ordinary laptops remain responsive. Without COLMAP the job enters `awaiting_colmap`; without OpenMVS or a GPU-capable COLMAP build it honestly stops at `sparse_complete`.

The current verified Windows toolchain is COLMAP 4.2.1 CPU plus OpenMVS 2.4.0 CPU. Both installations and all private capture/reconstruction data are ignored by Git. On the 12-photo prototype capture, 9 views registered; OpenMVS produced 32,722 dense points and an 18,369-face mesh. Image undistortion took 19 seconds, dense reconstruction 101 seconds, and meshing 4 seconds on the development laptop. Larger captures and higher-quality settings will take longer.
