"""High-level AFC client — connects to iPhone and provides file operations."""

from photofetch.afc.usbmux import list_devices, connect_to_port
from photofetch.afc.lockdown import LockdownClient, LOCKDOWN_PORT
from photofetch.afc.protocol import AfcClient

AFC_SERVICE_NAME = "com.apple.afc"


def get_device() -> dict:
    """Get the first connected iOS device."""
    devices = list_devices()
    if not devices:
        raise RuntimeError("NO_DEVICE")
    return devices[0]


def connect() -> AfcClient:
    """Connect to AFC service on the first available device.

    Handles: usbmuxd → lockdown → pair → session → start AFC service → connect.
    """
    device = get_device()
    device_id = device["DeviceID"]
    udid = device["SerialNumber"]

    # Connect to lockdownd
    sock = connect_to_port(device_id, LOCKDOWN_PORT)
    try:
        lockdown = LockdownClient(sock, udid)
    except FileNotFoundError:
        raise RuntimeError("NOT_PAIRED")

    # Start session (validates pairing implicitly)
    lockdown.query_type()
    if not lockdown.start_session():
        raise RuntimeError("SESSION_FAILED")

    # Start AFC service
    afc_port, use_ssl = lockdown.start_service(AFC_SERVICE_NAME)
    lockdown.close()

    # Connect to AFC port
    afc_sock = connect_to_port(device_id, afc_port)
    return AfcClient(afc_sock)
