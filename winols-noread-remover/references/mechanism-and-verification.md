# Mechanism and verification

## Two different NOREAD mechanisms

EVC documents two BIN-level mechanisms:

1. **Patch-Tag-Block text**: a checksum-module or manually added tag area contains visible identifiers such as `NOREAD`. A hex editor can remove the text, but the replacement must match the surrounding native fill or an exact ORI.
2. **WinOLS hexdump signature**: `Edit -> Sign Hexdump` invisibly alters values in user-selected maps. The NOREAD checkbox makes the signature an import protection. WinOLS can install the text several times. EVC warns against small maps, skip-byte maps, and float maps because the signature changes real values.

WinOLS `Remove Signatures` is not an ORI restoration tool: EVC states that it overwrites signatures but does not restore the original values. For a foreign-customer signature, use an exact ORI to recover the carrier values.

Official references:

- EVC WinOLS 5.91 manual: `https://www.evc.de/ftp/Winols/WinOLS%20HelpEn.pdf`
- EVC Patch-Tag-Block FAQ: `https://www.evc.de/en/service/q1308.asp`

## Reference-assisted hidden-signature method

1. Confirm ORI and MOD have equal length and matching firmware identity. Filename and size alone are insufficient; compare visible Cal ID/part number where available and keep hashes.
2. Diff ORI/MOD and cluster changed offsets.
3. Rank a hidden-signature carrier only when the changes show a signature-like word pattern: many changes, almost all in one byte lane of 16-bit words, low median absolute byte delta, and no mixed-lane tuning structure.
4. Require one unique candidate for automatic restoration. Otherwise use a manually reviewed range or stop.
5. Restore only candidate positions from ORI into a copy of MOD.
6. Import the copy into the same WinOLS version that rejected the source. Passing means the import wizard appears without MSG1872/MSG2383.
7. If the error remains, do not keep erasing ranked blocks. Reassess whether there are multiple signatures, a Patch-Tag-Block, or an encrypted/slave container.

The heuristic is deliberately conservative. A small repeated calibration change can look signature-like; only the WinOLS import result closes the loop.

## Case-proven pattern (89663-58830)

In the validated Toyota/DENSO case, the protected Stage1 differed from ORI by 5,745 bytes. No plain marker existed. One unique carrier at `0x00693E:0x006A05` had 93 changed bytes, all in the even byte lane of a 16-bit-looking table, median absolute delta 4 and maximum 14. Restoring those 93 bytes removed MSG1872 in WinOLS 5.91 while retaining 5,652 other Stage1 differences.

These offsets are case-specific and must never be reused on another firmware.

## Status language

- `NOREAD_PRESENT`: WinOLS rejection or a safe marker/signature candidate is observed.
- `EDITABLE_CANDIDATE`: the script produced a bounded restoration but WinOLS has not yet tested it.
- `EDITABLE_NOT_FLASH_READY`: the installed WinOLS accepts a new raw import; checksum and calibration validation remain separate.
- `NO_EDIT`: evidence is insufficient or the file is not a raw BIN.
