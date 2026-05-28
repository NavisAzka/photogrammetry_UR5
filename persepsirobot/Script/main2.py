import os
import time
import cv2
import requests

# =========================================================
# CONFIG
# =========================================================

ESP32_IP    = os.environ.get("ESP32_IP",     "http://192.168.200.219")
DEVICE      = os.environ.get("DEVICE",       "/dev/video0")
SAVE_DIR    = os.environ.get("SAVE_DIR",     "../datasets")
FRAME_WIDTH = int(os.environ.get("FRAME_WIDTH",  "1280"))
FRAME_HEIGHT= int(os.environ.get("FRAME_HEIGHT", "720"))
RELAY_ID    = int(os.environ.get("RELAY_ID",     "0"))

POLLING_DELAY = 0.1

# =========================================================
# GLOBAL
# =========================================================

frame_id = 0
photo_ready = True

# =========================================================
# SETUP
# =========================================================

os.makedirs(SAVE_DIR, exist_ok=True)

# =========================================================
# CAMERA SETUP
# =========================================================

def setup_camera():
    """
    Setup camera parameters using V4L2
    """

    print("[INFO] Configuring camera...")

    # -----------------------------------------
    # DISABLE AUTO FOCUS
    # -----------------------------------------

    os.system(
        f"v4l2-ctl -d {DEVICE} "
        f"-c focus_automatic_continuous=0"
    )

    os.system(
        f"v4l2-ctl -d {DEVICE} "
        f"-c focus_absolute=85"
    )

    # -----------------------------------------
    # ENABLE AUTO WHITE BALANCE
    # -----------------------------------------

    os.system(
        f"v4l2-ctl -d {DEVICE} "
        f"-c white_balance_automatic=1"
    )

    # -----------------------------------------
    # IMAGE PARAMETERS
    # -----------------------------------------

    os.system(
        f"v4l2-ctl -d {DEVICE} "
        f"-c brightness=44"
    )

    os.system(
        f"v4l2-ctl -d {DEVICE} "
        f"-c contrast=88"
    )

    os.system(
        f"v4l2-ctl -d {DEVICE} "
        f"-c saturation=128"
    )

    os.system(
        f"v4l2-ctl -d {DEVICE} "
        f"-c sharpness=90"
    )

    print("[SUCCESS] Camera configured")


# =========================================================
# ESP32 API
# =========================================================

def set_relay_active(relay_id, state):
    """
    state:
        on
        off
    """

    url = f"{ESP32_IP}/relay/{relay_id}"

    try:
        response = requests.post(
            url,
            data={"state": state},
            timeout=4
        )

        return response.status_code == 200

    except Exception as e:
        print(f"[ERROR] Relay request failed: {e}")
        return False


def get_input_states():

    url = f"{ESP32_IP}/input"

    try:
        response = requests.get(url, timeout=4)

        if response.status_code != 200:
            return None

        data = response.json()

        return data.get("inputs", [])

    except Exception as e:
        print(f"[ERROR] Failed reading inputs: {e}")
        return None


# =========================================================
# UTILITIES
# =========================================================

def clear_dataset_folder():

    for file in os.listdir(SAVE_DIR):

        path = os.path.join(SAVE_DIR, file)

        if os.path.isfile(path):
            os.remove(path)

    print("[INFO] Dataset folder cleared")


def get_pin_state(inputs, pin_number):

    pin = next(
        (item for item in inputs if item["pin"] == pin_number),
        None
    )

    if pin is None:
        return 0

    return pin.get("state", 0)


# =========================================================
# CAMERA CAPTURE
# =========================================================

def initialize_camera():

    cap = cv2.VideoCapture(DEVICE)

    if not cap.isOpened():
        print("[ERROR] Failed opening camera")
        return None

    # Resolution
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)

    # MJPEG
    cap.set(
        cv2.CAP_PROP_FOURCC,
        cv2.VideoWriter_fourcc(*'MJPG')
    )

    return cap


def take_photo(cap):

    global frame_id

    ret, frame = cap.read()

    if not ret:
        print("[ERROR] Failed grabbing frame")
        return False

    filename = os.path.join(
        SAVE_DIR,
        f"frame_{frame_id:04d}.jpg"
    )

    cv2.imwrite(filename, frame)

    print(f"[SUCCESS] Saved: {filename}")

    frame_id += 1

    return True


# =========================================================
# MAIN
# =========================================================

def main():

    global photo_ready

    print("===================================")
    print(" ESP32 + OpenCV Capture System ")
    print("===================================")

    # -----------------------------------------
    # CLEAN DATASET
    # -----------------------------------------

    clear_dataset_folder()

    # -----------------------------------------
    # SETUP CAMERA
    # -----------------------------------------

    # setup_camera()

    # -----------------------------------------
    # OPEN CAMERA
    # -----------------------------------------

    cap = initialize_camera()

    if cap is None:
        return

    # Give camera stabilization time
    time.sleep(2)

    # -----------------------------------------
    # ENABLE RELAY
    # -----------------------------------------

    if set_relay_active(RELAY_ID, "on"):
        print("[INFO] Relay activated")

    try:

        while True:

            inputs = get_input_states()

            if inputs is None:
                time.sleep(POLLING_DELAY)
                continue

            pin_33 = get_pin_state(inputs, 33)
            pin_32 = get_pin_state(inputs, 32)

            # =====================================
            # PHOTO TRIGGER
            # =====================================

            if pin_33 == 1 and photo_ready:

                print("[TRIGGER] Capturing image...")

                success = take_photo(cap)

                if success:
                    photo_ready = False

            elif pin_33 == 0:

                if not photo_ready:

                    photo_ready = True

                    print("[READY] Waiting next trigger")

            # =====================================
            # FINISH CONDITION
            # =====================================

            if pin_32 == 1:

                print("[INFO] Movement completed")

                set_relay_active(RELAY_ID, "off")

                break

            time.sleep(POLLING_DELAY)

    except KeyboardInterrupt:

        print("\n[INFO] Program interrupted")

    finally:

        cap.release()

        cv2.destroyAllWindows()

        print("[INFO] Camera released")


# =========================================================
# ENTRY POINT
# =========================================================

if __name__ == "__main__":
    main()