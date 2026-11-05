from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


@dataclass(frozen=True, slots=True)
class WeComCallbackEvent:
    callback_token: str
    open_kfid: str
    event_type: str
    raw_xml: str


class WeComCallbackCrypto:
    def __init__(self, token: str, encoding_aes_key: str, receive_id: str) -> None:
        if not token:
            raise ValueError("WeCom callback token is required")
        if len(encoding_aes_key) != 43:
            raise ValueError("WeCom EncodingAESKey must contain 43 characters")
        if not receive_id:
            raise ValueError("WeCom receive id is required")
        try:
            aes_key = base64.b64decode(encoding_aes_key + "=", validate=True)
        except ValueError as error:
            raise ValueError("WeCom EncodingAESKey is not valid base64") from error
        if len(aes_key) != 32:
            raise ValueError("WeCom EncodingAESKey must decode to 32 bytes")
        self.token = token
        self.aes_key = aes_key
        self.receive_id = receive_id

    def decrypt_url_verification(
        self,
        msg_signature: str,
        timestamp: str,
        nonce: str,
        echo_str: str,
    ) -> str:
        self._verify_signature(msg_signature, timestamp, nonce, echo_str)
        return self._decrypt(echo_str).decode("utf-8")

    def decrypt_callback(
        self,
        msg_signature: str,
        timestamp: str,
        nonce: str,
        encrypted_xml: bytes,
    ) -> WeComCallbackEvent:
        if len(encrypted_xml) > 1024 * 1024:
            raise ValueError("WeCom callback body is too large")
        root = ET.fromstring(encrypted_xml)
        encrypted = (root.findtext("Encrypt") or "").strip()
        if not encrypted:
            raise ValueError("WeCom callback Encrypt field is missing")
        self._verify_signature(msg_signature, timestamp, nonce, encrypted)
        raw_xml = self._decrypt(encrypted).decode("utf-8")
        payload = ET.fromstring(raw_xml)
        return WeComCallbackEvent(
            callback_token=(payload.findtext("Token") or "").strip(),
            open_kfid=(payload.findtext("OpenKfId") or "").strip(),
            event_type=(payload.findtext("Event") or "").strip(),
            raw_xml=raw_xml,
        )

    def _verify_signature(
        self,
        signature: str,
        timestamp: str,
        nonce: str,
        encrypted: str,
    ) -> None:
        expected = hashlib.sha1(
            "".join(sorted((self.token, timestamp, nonce, encrypted))).encode("utf-8")
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise ValueError("WeCom callback signature is invalid")

    def _decrypt(self, encrypted: str) -> bytes:
        try:
            ciphertext = base64.b64decode(encrypted, validate=True)
        except ValueError as error:
            raise ValueError("WeCom callback ciphertext is invalid base64") from error
        decryptor = Cipher(
            algorithms.AES(self.aes_key),
            modes.CBC(self.aes_key[:16]),
        ).decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
        plaintext = _pkcs7_unpad(padded)
        if len(plaintext) < 20:
            raise ValueError("WeCom callback plaintext is truncated")
        message_length = struct.unpack("!I", plaintext[16:20])[0]
        message_end = 20 + message_length
        if message_end > len(plaintext):
            raise ValueError("WeCom callback message length is invalid")
        message = plaintext[20:message_end]
        receive_id = plaintext[message_end:].decode("utf-8")
        if receive_id != self.receive_id:
            raise ValueError("WeCom callback receive id does not match")
        return message


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data:
        raise ValueError("WeCom callback plaintext is empty")
    padding = data[-1]
    if padding < 1 or padding > 32 or data[-padding:] != bytes([padding]) * padding:
        raise ValueError("WeCom callback padding is invalid")
    return data[:-padding]
