# PERSEPSI

PERSEPSI is a web-based control and processing pipeline for close-range 3D reconstruction, designed around an ESP32-controlled turntable, a USB camera, and OpenDroneMap (ODM).

The project provides:

- A Flask control panel for starting and stopping capture and reconstruction jobs
- A camera capture worker that listens for ESP32 input pins and saves images
- Optional foreground mask generation for ODM
- A Blender helper to convert OBJ reconstructions into GLB
- A 3D model viewer for generated outputs

## Project Layout

```text
{Workspace}
├── persepsirobot/
│   ├── Script/
│   │   ├── app.py
│   │   ├── main2.py
│   │   ├── preprocess_masks.py
│   │   ├── convert_to_glb.py
│   │   └── templates/
│   │       ├── index.html
│   │       └── viewer.html
│   ├── Dockerfile
│   └── docker-compose.yml
├── datasets/
├── output/
└── README.md
```

## How It Works

1. The Flask app in `persepsirobot/Script/app.py` exposes routes for starting/stopping capture and ODM jobs.
2. `main2.py` polls the ESP32, watches the trigger input, and captures images from the USB camera into `datasets/`.
3. `run_odm.sh` submits the captured images to NodeODM and writes reconstruction output to `output/`.
4. `preprocess_masks.py` can generate ODM-compatible foreground masks beside the images.
5. `convert_to_glb.py` converts an OBJ model into GLB using Blender.
6. The templates provide a control panel and a browser-based 3D viewer.

## Requirements

### Python packages

The Python app uses:

- `flask`
- `opencv-python-headless`
- `requests`
- `numpy`

### System tools

Depending on how you run the project, you may also need:

- `v4l2-ctl` for camera configuration
- `curl`, `jq`, and `unzip` for ODM automation
- `bash`
- Blender for OBJ to GLB conversion

## Running With Docker

The recommended setup is inside `persepsirobot/`.

This Docker configuration runs the Flask app in a container, but it expects NodeODM to already be running on the host machine at port `6969`.

```bash
cd persepsirobot
docker compose up --build -d
```

The compose file starts:

- `persepsi` on port `12345`
- it connects to an external NodeODM at `host.docker.internal:6969`

The web UI is available at:

```text
http://localhost:12345
```

### Setup on another machine

If you want to move this project to a different computer, follow these steps:

1. Install Docker and Docker Compose on the new machine.
2. Copy the entire `persepsirobot/` folder to that machine.
3. Find the camera device path on the new machine:

```bash
ls /dev/video*
# or
v4l2-ctl --list-devices
```

4. If the camera is not `/dev/video0`, set `CAMERA_DEVICE` before starting:

```bash
export CAMERA_DEVICE=/dev/video2
```

5. Start the containers from inside `persepsirobot/`:

```bash
docker compose up --build -d
```

6. Open the web UI in your browser:

```text
http://localhost:12345
```

The Docker setup starts both the Flask control panel and NodeODM:

- `persepsi` on port `12345`
- external NodeODM must already be running on port `6969`

The compose file also mounts these host folders so your data persists:

- `./datasets:/app/datasets`
- `./output:/app/output`

This Docker setup expects NodeODM to already be running on the host machine at:

```text
http://localhost:6969
```

The `persepsi` container reaches that service through `host.docker.internal:6969`.

### Camera device

The container expects a camera device mapped through `CAMERA_DEVICE`:

```bash
ls /dev/video*
```

If needed, set it before starting Docker Compose:

```bash
export CAMERA_DEVICE=/dev/video0
docker compose up --build -d
```

## Running Without Docker

You can also run the Flask app directly:

```bash
cd persepsirobot
python3 Script/app.py
```

For this mode, NodeODM must be running separately on `localhost:6969`.

## Capture Pipeline

The capture worker reads these ESP32 pins:

- Pin `33`: capture trigger
- Pin `32`: movement complete, used to stop the loop

Captured photos are written as:

```text
datasets/frame_0000.jpg
datasets/frame_0001.jpg
```

## Mask Generation

Generate foreground masks for ODM:

```bash
python3 persepsirobot/Script/preprocess_masks.py /path/to/datasets
```

Useful options:

```bash
python3 persepsirobot/Script/preprocess_masks.py /path/to/datasets --mode chroma
python3 persepsirobot/Script/preprocess_masks.py /path/to/datasets --debug
python3 persepsirobot/Script/preprocess_masks.py /path/to/datasets --overwrite
```

Mask files are written next to the source images as:

```text
frame_0000_mask.JPG
```

## OBJ to GLB Conversion

Run the Blender helper in headless mode:

```bash
blender --background --python persepsirobot/Script/convert_to_glb.py -- input.obj output.glb
```

## Data Folders

- `datasets/` stores captured source images
- `output/` stores ODM reconstruction results

These folders are created at runtime and are safe to keep out of version control.

## Notes

- The camera, ESP32 address, frame size, relay ID, and output folders can be overridden through environment variables when the Flask app launches the capture and ODM workers.
- The web UI streams process logs in real time so you can monitor capture and reconstruction from the browser.
