# ES200 2017 — DENSO 89663-33L93 pedal-map reverse-engineering log

Source files:
- `AAL_(89663-33L93-).bin` — 1,572,864 bytes (0x180000) factory bin, 2.0L NA (6AR-FSE), DENSO.
- `AAL_(89663-33L93-PE).ols` — WinOLS 5.0 project, 2,787,016 bytes. "PE" = Pedal.

## OLS container facts
- Format: WinOLS 5.0, length-prefixed strings (`<u32 len><ascii>`).
- The full original bin is embedded inside the OLS at base offset **0x1286C8**, i.e.
  `OLS_offset = 0x1286C8 + eprom_addr`. Verified: bin bytes equal OLS-embedded bytes at the
  pedal addresses.
- WinOLS auto-detected the pedal curves and named them all generically **`Map "Denso 16"`**
  (16 = column count). 27 such entries: 24 in the main set + 3 under "My maps".
- Per-record fields are proprietary; the data pointer at record `+0xD7/+0xEB` points to WinOLS's
  (empty) modification buffer, NOT the eprom data. The real values live in the embedded bin.

## Pedal table block
- Located at eprom **0x54C98 – 0x552D4** (curves actually begin at the axis @0x54C78).
- Packed unit = `0x44` (68 bytes) = `[X-axis 16w][output 16w][len = uint32 16]`.
- 48 curves total in the block: 24 identical **X-axis** copies + 24 **output** curves.
- Encoding: **uint16-LE, 1x16, raw/256 = %**, full scale **25600 = 0x6400 = 100%**,
  curves saturate with a doubled final value (..., 25600, 25600).

### Shared X-axis (pedal opening %)
raw: `0, 614, 1408, 2560, 5120, 7680, 9344, 10112, 10752, 11520, 12800, 14592, 17562, 21222, 25600, 25600`
%  : `0, 2.4, 5.5, 10, 20, 30, 36.5, 39.5, 42, 45, 50, 57, 68.6, 82.9, 100, 100`

### The 4 stock output shapes (requested %), among the 24 output curves
| shape | count | output % @ breakpoints |
|---|---|---|
| S4 ECO / softest | 3 | 0,1.0,1.6,2.8,4.7,8.2,10.7,14,18.4,23.8,33.5,46,62,78,100,100 |
| S2 soft          | 6 | 0,1.1,1.7,3.6,9.0,14.4,19.2,21.8,24,26.8,32.2,45,60,83,100,100 |
| S1 normal (common)| 9 | 0,1.4,2.1,4.5,11.2,18,24,27.3,30,33.5,40.2,51.4,66.5,82.6,100,100 |
| S3 sport / steepest| 6 | 0,2.0,5.0,9.0,20,32,40,43,46,49,54,60,70,83,100,100 |

24 output-curve addresses (sorted): 0x54C98, 0x54CDC, 0x54D20, 0x54D64, 0x54DA8, 0x54DEC, 0x54E30,
0x54E74, 0x54EB8, 0x54EFC, 0x54F40, 0x54F84, 0x54FC8, 0x5500C, 0x55050, 0x55094, 0x550D8, 0x5511C,
0x55160, 0x551A4, 0x551E8, 0x5522C, 0x55270, 0x552B4.
(The interleaved X-axis copies sit at 0x54C78, 0x54CBC, 0x54D00, ... — leave untouched.)

## Tuning result (K=0.25 light-throttle)
- Modifying only the 24 output curves changed 564 bytes, all within 0x54C98–0x552D4; file size unchanged.
- Example (S1 normal): pedal 20% output 11.2→13.7, 30% 18→21.7, ~40% 27.3→32.3. WOT unchanged.
- Checksum must be Auto-Fixed (ECM Titanium) before flashing.
