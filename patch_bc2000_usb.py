#!/usr/bin/env python3
"""
Patch Behringer BCR2000 / BCF2000 firmware 1.10 so the device works over USB on Apple silicon Macs.

The BCR2000 and BCF2000 advertise wMaxPacketSize=4 on its bulk OUT endpoint 0x02, which is illegal
for full-speed bulk endpoints (USB 2.0 section 5.8.3: 8, 16, 32 or 64). The USB controller in
Apple silicon Macs refuses to create that endpoint, so nothing sent from the Mac reaches the
device. This script changes the advertised value to 8 in all four USB-mode descriptor sets
(U-1..U-4). The device hardware is already configured for 64-byte packets and its firmware
already handles several MIDI events per packet, so only the advertised value changes.

Usage:
    python3 patch_bc2000_usb.py bcr2000_1-10.syx [bcr2000_1-10-usbfix.syx]
    python3 patch_bc2000_usb.py bcf2000_1-10.syx [bcf2000_1-10-usbfix.syx]

Firmware container format (sysex framing, ciphers, checksums) ported to Python 3 from
bcfwconvert.py, Copyright (C) 2010 Willem van Engen, as published in
https://github.com/TheKikGen/BCR2000-dev. Licensed under the GNU GPL v2 or later.
"""
import hashlib
import struct
import sys

# --- known inputs/outputs ---------------------------------------------------------------

EP_OUT_DESCRIPTOR = bytes.fromhex("09 05 02 02 04 00 00")  # bulk OUT 0x02, wMaxPacketSize=4


class Firmware:
    def __init__(self, name, sha256, model, offsets, patched_sha256, tested):
        self.name, self.sha256, self.offsets = name, sha256, offsets
        self.header = bytes([0xF0, 0x00, 0x20, 0x32, 0x7F, model, 0x34])  # Behringer firmware packet
        self.patched_sha256, self.tested = patched_sha256, tested


# Offsets are positions of EP_OUT_DESCRIPTOR in the decoded OS image (U-1..U-4 descriptor sets).
KNOWN_FIRMWARE = [
    Firmware("BCR2000 1.10",
             "9ebef1c75aa07d00613a20fb8cd9d58dbcfa9f076f1d8657904e3a6a669bae1d", 0x15,
             [0xDD25, 0xDD75, 0xDDD6, 0xDE46],
             "3402425ca2f8d6d0934c50aa30a66e7d1ae909b66abd09d2f502a91b64437f8c", tested=True),
    Firmware("BCF2000 1.10",
             "9c9a79cf54e0bcee6827db2c77f491663b4fdb9cd25006207c476418c67a98d3", 0x14,
             [0xE601, 0xE651, 0xE6B2, 0xE722],
             "fba0c7c8550bcd30e3f243175512a03587462f109a1e08a50658f8c79594389c", tested=False),
]
NEW_MAX_PACKET_SIZE = 0x08

# --- sysex layer --------------------------------------------------------------------------

SYX_CIPHER = [0x54, 0x5A, 0x27, 0x30, 0x33, 0x42, 0x43, 0x4F, 0x4E, 0x54, 0x52, 0x4F, 0x4C]


def implode(data):
    """7-bit sysex payload -> 8-bit bytes (every 8th byte holds the high bits of the previous 7)."""
    out = []
    for i in range(0, len(data), 8):
        block = list(data[i:i + 7])
        high = data[i + 7]
        for k in range(7):
            if high & (1 << k):
                block[6 - k] |= 0x80
        out += block
    return out


def explode(data):
    """8-bit bytes -> 7-bit sysex payload."""
    out = []
    for i in range(0, len(data), 7):
        block = data[i:i + 7]
        high = 0
        for j, b in enumerate(block):
            if b & 0x80:
                high |= 1 << (6 - j)
        out += [b & 0x7F for b in block] + [high]
    return out


def xor_syx(data):
    return [b ^ SYX_CIPHER[i % len(SYX_CIPHER)] for i, b in enumerate(data)]


def page_checksum(page):
    c = 0
    for byte in page:
        for i in range(8):
            if not ((byte >> i) ^ c) & 1:
                c ^= 0x19
            if c & 1:
                c |= 0x100
            c >>= 1
    return c


def flash_cipher(data, sector):
    """Cipher applied per 4 KB flash sector; xor, so it works both ways."""
    out = []
    magic = sector or 0x545A
    for i in range(0, len(data), 2):
        if magic & 1:
            magic ^= 0x8005
        magic >>= 1
        out += [data[i] ^ (magic & 0xFF), data[i + 1] ^ ((magic >> 8) & 0xFF)]
    return out


def syx_to_flash(raw):
    """Parse a firmware .syx file into (header, base address, flash bytes)."""
    messages, i = [], 0
    while i < len(raw):
        if raw[i] == 0xF0:
            j = raw.index(0xF7, i)
            messages.append(raw[i:j + 1])
            i = j + 1
        else:
            i += 1
    header, base, flash = None, None, []
    for n, msg in enumerate(messages):
        if msg[1:4] != b"\x00\x20\x32" or msg[6] != 0x34:
            raise ValueError("message %d is not a Behringer firmware packet" % n)
        if header is None:
            header = msg[:7]
        elif msg[:7] != header:
            raise ValueError("mixed packet headers in file")
        body = xor_syx(implode(list(msg[7:-1])))
        # 16-bit page address (the original tool used <<7, which breaks above 64 KB)
        address = ((body[0] << 8) | body[1]) * 0x100
        if base is None:
            base = address
        if address != base + 0x100 * n:
            raise ValueError("non-contiguous firmware address 0x%x" % address)
        if page_checksum(body[3:]) != body[2]:
            raise ValueError("packet checksum error at 0x%x" % address)
        flash += body[3:]
    for i in range(0, len(flash), 0x1000):
        flash[i:i + 0x1000] = flash_cipher(flash[i:i + 0x1000], (i + base) // 0x1000)
    return header, base, flash


def flash_to_syx(flash, base, header):
    out = bytearray()
    for page in range(0, len(flash), 0x1000):
        sector = flash_cipher(flash[page:page + 0x1000], (base + page) // 0x1000)
        for sub in range(0, 0x1000, 0x100):
            a = (base + page + sub) // 0x100
            body = [a >> 8, a & 0xFF, 0] + sector[sub:sub + 0x100]
            body[2] = page_checksum(body[3:])
            out += header + bytes(explode(xor_syx(body))) + b"\xf7"
    return bytes(out)

# --- OS image layer -----------------------------------------------------------------------

OS_CIPHER1 = [0x2726534C, 0x3971212D, 0x71167B24, 0x69272171, 0x3B652F24, 0x30293644, 0x541A183F,
              0x2C3F7C31, 0x29287D28, 0x5648553D, 0x4C4F1257, 0x31677518, 0x2E2E186A, 0x00554400,
              0x44554D4D]
OS_CIPHER2 = [0x75697361, 0x77386664, 0x33363765, 0x20756934, 0x6920686A, 0x74667564, 0x7437387A,
              0x756F3372, 0x616F347A, 0x667A7569, 0x616F2067, 0x74203738, 0x747A3738, 0x00756920]


def os_word(w, index):
    return w ^ OS_CIPHER1[index % len(OS_CIPHER1)] ^ OS_CIPHER2[index % len(OS_CIPHER2)]


def os_checksum(image):
    magic, total = 0x42474552, 0
    padded = image + b"\0" * (-len(image) % 4)
    for off in range(0, len(image), 4):
        if magic & 1:
            magic |= 1 << 32
        magic >>= 1
        total += magic ^ struct.unpack_from("<I", padded, off)[0]
    return total & 0xFFFFFFFF


def flash_to_os(flash):
    data = bytes(flash)
    size = os_word(struct.unpack_from("<I", data, 0)[0], 0)
    stored = os_word(struct.unpack_from("<I", data, 4)[0], 1)
    image = bytearray()
    for off in range(8, size + 8, 4):
        image += struct.pack("<I", os_word(struct.unpack_from("<I", data, off)[0], off // 4))
    image = bytes(image[:size])
    if os_checksum(image) != stored:
        raise ValueError("OS image checksum mismatch")
    return image


def os_to_flash(image, flash_len):
    data = bytearray(image) + b"\xff" * (flash_len - 8 - len(image))
    out = bytearray(struct.pack("<I", os_word(len(image), 0)))
    out += struct.pack("<I", os_word(os_checksum(image), 1))
    for off in range(0, len(data), 4):
        out += struct.pack("<I", os_word(struct.unpack_from("<I", data, off)[0], off // 4 + 2))
    return list(out)

# --- main ---------------------------------------------------------------------------------


def die(msg):
    sys.exit("error: " + msg)


def main(argv):
    if len(argv) not in (2, 3):
        usage = __doc__.split("Usage:")[1].split("Firmware container")[0]
        sys.exit("usage:\n" + "\n".join(l.rstrip() for l in usage.strip("\n").splitlines()))
    src = argv[1]
    dst = argv[2] if len(argv) == 3 else src.rsplit(".", 1)[0] + "-usbfix.syx"

    try:
        raw = open(src, "rb").read()
    except OSError as e:
        die("cannot read %s: %s" % (src, e.strerror))
    digest = hashlib.sha256(raw).hexdigest()
    for fw in KNOWN_FIRMWARE:
        if digest == fw.patched_sha256:
            die("%s is an already patched %s firmware" % (src, fw.name))
    fw = next((f for f in KNOWN_FIRMWARE if f.sha256 == digest), None)
    if fw is None:
        die("%s is not an official BCR2000 or BCF2000 firmware 1.10 file (sha256 %s)" % (src, digest))
    print("Input: official %s firmware" % fw.name)

    header, base, flash = syx_to_flash(raw)
    if header != fw.header or base != 0x2000:
        die("unexpected firmware layout")
    image = flash_to_os(flash)
    found = [i for i in range(len(image)) if image.startswith(EP_OUT_DESCRIPTOR, i)]
    if found != fw.offsets:
        die("USB descriptors not where expected")
    if flash_to_syx(os_to_flash(image, len(flash)), base, header) != raw:
        die("re-encoding check failed")

    patched = bytearray(image)
    for off in fw.offsets:
        patched[off + 4] = NEW_MAX_PACKET_SIZE
    out = flash_to_syx(os_to_flash(bytes(patched), len(flash)), base, header)

    # Decode the result from scratch and confirm only the four intended bytes changed.
    _, _, check_flash = syx_to_flash(out)
    check = flash_to_os(check_flash)
    changed = [i for i in range(len(image)) if image[i] != check[i]]
    if len(check) != len(image) or changed != [o + 4 for o in fw.offsets]:
        die("verification of patched file failed")
    out_digest = hashlib.sha256(out).hexdigest()
    if out_digest != fw.patched_sha256:
        die("patched output does not match the expected checksum")

    with open(dst, "wb") as f:
        f.write(out)
    print("Patched %d USB descriptors (bulk OUT wMaxPacketSize 4 -> 8)." % len(changed))
    print("Wrote %s" % dst)
    print("sha256 %s" % out_digest)
    if not fw.tested:
        print("WARNING: this patch has not been tested on a real %s yet. See README." % fw.name.split()[0])

if __name__ == "__main__":
    main(sys.argv)
