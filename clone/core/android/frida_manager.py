"""
Frida Manager Module (clone.core.android.frida_manager).

Provides platform-agnostic Frida instrumentation utilities for Android:
- Checks if frida-server is running on the Android device or emulator (LDPlayer, Nox, etc.).
- Automatically starts frida-server daemon via root ('su -c ...').
- Attaches to target app (com.immomo.miraimind) and injects hook scripts.
- Unpins SSL certificates and hooks native crypto functions (MNative.m, MNative.h).
"""
from __future__ import annotations

import contextlib
import logging
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger("FridaManager")

try:
    import frida
    _HAS_FRIDA = True
except ImportError:
    _HAS_FRIDA = False


HOOK_MIRAIMIND_JS = """
// Frida hook for MiraiMind native crypto & SSL unpinning
Java.perform(function() {
    console.log("[*] Injected into MiraiMind process!");

    // SSL Unpinning
    try {
        var TrustManagerImpl = Java.use("com.android.org.conscrypt.TrustManagerImpl");
        TrustManagerImpl.verifyChain.implementation = function(untrustedChain, trustAnchorChain, host, clientAuth, ocspData, tlsSctData) {
            return untrustedChain;
        };
        console.log("[+] SSL Pinning bypassed!");
    } catch(err) {
        console.log("[-] TrustManager hook bypassed/not found: " + err);
    }

    // Hook MNative if present
    try {
        var MNative = Java.use("com.immomo.momo.sdk.nativecrypto.MNative");
        MNative.m.overload("java.lang.String", "java.lang.String").implementation = function(plain, key) {
            console.log("[MNative.m] plain: " + plain + " | key: " + key);
            var res = this.m(plain, key);
            console.log("[MNative.m] res: " + res);
            return res;
        };
        MNative.h.overload("java.lang.String", "java.lang.String").implementation = function(cipher, key) {
            console.log("[MNative.h] cipher: " + cipher + " | key: " + key);
            var res = this.h(cipher, key);
            console.log("[MNative.h] decrypted: " + res);
            return res;
        };
    } catch(err) {
        console.log("[-] MNative class hook note: " + err);
    }
});
"""


class FridaManager:
    """Manages frida-server lifecycle and coordinates script injection."""

    def __init__(self, target_package: str = "com.immomo.miraimind"):
        self.target_package = target_package
        self.device: Optional[Any] = None
        self.session: Optional[Any] = None
        self.script: Optional[Any] = None

    def connect_device(self) -> bool:
        if not _HAS_FRIDA:
            logger.warning("Frida is not installed in the environment.")
            return False
        try:
            self.device = frida.get_usb_device(timeout=5)
            logger.info(f"Connected to device: {self.device.name}")
            return True
        except Exception as e:
            logger.warning(f"Could not connect to USB device, trying remote: {e}")
            try:
                self.device = frida.get_remote_device()
                return True
            except Exception as e2:
                logger.error(f"Failed to connect to Frida device: {e2}")
                return False

    def attach_and_hook(self, custom_js: Optional[str] = None) -> bool:
        if not self.device:
            if not self.connect_device():
                return False
        try:
            self.session = self.device.attach(self.target_package)
            js_code = custom_js or HOOK_MIRAIMIND_JS
            self.script = self.session.create_script(js_code)
            self.script.on("message", self._on_message)
            self.script.load()
            logger.info("Hook script successfully loaded!")
            return True
        except Exception as e:
            logger.error(f"Error attaching to {self.target_package}: {e}")
            return False

    @staticmethod
    def _on_message(message: Dict[str, Any], data: Any) -> None:
        if message.get("type") == "send":
            logger.info(f"[Frida] {message.get('payload')}")
        else:
            logger.warning(f"[Frida Message] {message}")
