# B-Control Apple Silicon Fix (BCR2000 / BCF2000)

A firmware patch that makes the Behringer BCR2000 and BCF2000 work over USB on Apple silicon Macs. It needs no kernel extension, no driver, and no change to macOS security settings.

| Model   | Firmware | Status                                                              |
| ------- | -------- | ------------------------------------------------------------------- |
| BCR2000 | 1.10     | Tested on a real device: works                                      |
| BCF2000 | 1.10     | **Not tested on a real device yet.** Analysed and patched the same way |

## The problem

On Apple silicon Macs (M1 and later), the BCR2000 and BCF2000 send MIDI to the computer over USB, but nothing sent from the computer reaches the device. Editors get no reply, presets can't be uploaded, and LED rings or motor faders don't follow incoming MIDI. The same devices work on Intel Macs, and through a DIN MIDI interface on any Mac.

The cause is a bug in the devices' USB descriptor. The bulk OUT endpoint (`0x02`) declares `wMaxPacketSize = 4`, but USB 2.0 §5.8.3 only allows 8, 16, 32 or 64 for full-speed bulk endpoints. The USB controller in Apple silicon Macs refuses to create that endpoint. On a BCR2000, the kernel log shows:

```text
IOUSBHostFamily::validateEndpointMaxPacketSize: USB 2.0 5.[5-8].3: endpoint 0x02 invalid wMaxPacketSize 0x0004
AppleUSBXHCICommandRing::addEndpoint: completed with result code 17
```

Intel Macs and Linux tolerate or correct the bad value, which is why the problem only appears on Apple silicon.

## The fix

The descriptor value is the only error. The firmware already configures the USB hardware for 64-byte packets on that endpoint, and its receive routine already handles several MIDI events per packet. The patch changes the advertised value from 4 to 8 in the four descriptor sets the firmware uses for USB modes U-1 to U-4. Nothing else changes, and the device still reports firmware 1.10.

The BCF2000 firmware has the same bug. Its USB descriptor block is byte-identical to the BCR2000's, including the product ID `0x00BC`. Its code sets the same 64-byte hardware limit and contains the same multi-event receive loop. Only the location in the image differs. The BCF2000 patch is therefore the same 4 → 8 change, but it **has not yet been tried on a real BCF2000**. If you try it, please report the result.

`patch_bc2000_usb.py`:

- accepts only the official BCR2000 or BCF2000 firmware 1.10 files, checked by SHA-256;
- re-encodes the unmodified firmware first and confirms the result is byte-identical to the original;
- after patching, decodes its own output from scratch and confirms that exactly four bytes changed;
- confirms the output matches a known SHA-256.

Behringer's firmware is not included in this repository. Download it from Behringer (see below).

## Requirements

- A BCR2000 or BCF2000, plus the official firmware 1.10 file for that model. Behringer distributes both files in one archive: [bcf2000_1-10.zip](https://cdn-media.empowertribe.com/67d19225f7884456b2ba3274c20cc602/bcf2000_1-10.zip).

  | File               | SHA-256                                                            |
  | ------------------ | ------------------------------------------------------------------ |
  | `bcr2000_1-10.syx` | `9ebef1c75aa07d00613a20fb8cd9d58dbcfa9f076f1d8657904e3a6a669bae1d` |
  | `bcf2000_1-10.syx` | `9c9a79cf54e0bcee6827db2c77f491663b4fdb9cd25006207c476418c67a98d3` |

- Python 3. On macOS, run `xcode-select --install` if `python3` is missing.
- A SysEx sender, for example [SysEx Librarian](https://www.snoize.com/sysexlibrarian/) on macOS.
- One way to get the file to the device:
  - **USB from a computer where USB output to the device already works** (an Intel Mac, Windows or Linux), or
  - **a DIN MIDI interface** connected to the device's MIDI IN and OUT. This works from any computer, including an Apple silicon Mac.

USB output to an unpatched device does not work on Apple silicon, so you can't flash it over USB from that Mac.

## 1. Create the patched firmware

Run the script on the file for your model:

```sh
python3 patch_bc2000_usb.py bcr2000_1-10.syx   # BCR2000
python3 patch_bc2000_usb.py bcf2000_1-10.syx   # BCF2000
```

Expected output:

```text
Input: official BCR2000 1.10 firmware
Patched 4 USB descriptors (bulk OUT wMaxPacketSize 4 -> 8).
Wrote bcr2000_1-10-usbfix.syx
sha256 3402425ca2f8d6d0934c50aa30a66e7d1ae909b66abd09d2f502a91b64437f8c
```

```text
Input: official BCF2000 1.10 firmware
Patched 4 USB descriptors (bulk OUT wMaxPacketSize 4 -> 8).
Wrote bcf2000_1-10-usbfix.syx
sha256 fba0c7c8550bcd30e3f243175512a03587462f109a1e08a50658f8c79594389c
WARNING: this patch has not been tested on a real BCF2000 yet. See README.
```

If the script reports an error, stop. Don't send anything to the device.

## 2. Flash it

Only send a file made for your model: a BCR2000 file to a BCR2000, a BCF2000 file to a BCF2000.

1. Keep the original `.syx` file for your model. You need it to undo the patch.
2. Connect the device by USB to a computer where USB output works, or by DIN MIDI IN/OUT through an interface. For the untested BCF2000, DIN is the safer choice, because recovery also uses DIN.
3. Send the `-usbfix.syx` file to the device with your SysEx tool, the same way you would send an official Behringer firmware update. If the transfer fails or stalls, increase the delay between messages in the tool and try again.
4. Wait until the transfer is complete, then power-cycle the device.

## 3. Check it on the Apple silicon Mac

Connect the device by USB in mode U-1 to U-4, then send a request from your editor (for example [BCEditor](https://bceditor.com)). The device should answer, and its LED rings or motor faders should follow incoming MIDI.

The endpoint rejection should also be gone from the kernel log:

```sh
log show --last 5m --info --debug --predicate 'eventMessage CONTAINS "validateEndpointMaxPacketSize"'
```

## If something goes wrong

Firmware updates only write the operating-system area of flash and never touch the bootloader. If the device doesn't start properly after an update, use the bootloader's rescue mode:

1. Switch the device off and wait a few seconds.
2. Hold **STORE** and **LEARN** while switching it on. The display shows `load`.
3. Send the original `.syx` file for your model through the **DIN MIDI IN** port. Rescue mode doesn't work over USB.

To undo the patch on a working device, flash the original `.syx` file the normal way.

## Disclaimer

You flash this patch at your own risk. The BCR2000 patch has been tested on one device. The BCF2000 patch has not been tested on any device. This project is not affiliated with or endorsed by Behringer.

## Sources and credits

- [midiq/bcdriver](https://github.com/midiq/bcdriver): diagnosed the root cause on Apple silicon (kernel log, descriptor capture, failed user-space and DriverKit routes) and provides an alternative fix: a codeless kernel extension that overrides the descriptor on the Mac. That fix requires lowering macOS security (`csrutil enable --without kext`); this firmware patch doesn't.
- [TheKikGen/BCR2000-dev](https://github.com/TheKikGen/BCR2000-dev): unofficial B-Control development kit, originally by Willem van Engen ([willem.engen.nl/projects/bc2000-dev](https://willem.engen.nl/projects/bc2000-dev/)). It documents the firmware container format, the OS image cipher and checksums, the memory map, the bootloader rescue mode and the ML671000 CPU. The encoder and decoder in `patch_bc2000_usb.py` are a Python 3 port of its `bcfwconvert.py`. Firmware page addresses are now decoded as 16-bit values, because the original reads them wrongly above 64 KB.
- OKI ML671000 User's Manual (included in BCR2000-dev): USB controller registers. Both firmwares write 64 to `EP2PLD`, so the hardware accepts 64-byte packets on endpoint 2.

## License

GPL v2 or later (see `LICENSE`), because the firmware format code is derived from `bcfwconvert.py`.
