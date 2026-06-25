"""Minimal Apple File Conduit client — GPL-free implementation.

Protocol stack:
  usbmuxd (unix socket) → lockdown (plist + TLS) → AFC (binary)

References:
  - https://theapplewiki.com/wiki/Usbmux
  - https://docs.libimobiledevice.org/libimobiledevice/latest/afc_8h.html
  - Protocol reverse-engineered from public documentation
"""
