Third-party libraries for Scan VIN (index.html, the vin-scan block)
====================================================================

Served by the app at /vendor/<path> (server.py) and loaded by the page only
when someone scans a VIN. Nothing is fetched from the internet at run time.
Files are byte for byte as published on npm (.gitattributes: -text); the
version is in each folder name, so the page can cache them for good.

zxing-wasm-3.1.4/   barcode reader (Code 39, Data Matrix, QR)
  from   npm zxing-wasm@3.1.4 (2026-09-10)
         integrity sha512-1+v8p7sauddySVNG+ye7GD/0TS95BG2ycdkrC+EnBrFBFkHeIghzUt2yVjtx3NucJeNrQnhT8yGAIz60Vx9o0w==
  files  zxing-reader.js     = dist/iife/reader/index.js
         zxing_reader.wasm   = dist/reader/zxing_reader.wasm
                               (sha256 e8af31edb56d0522..., as ZXING_WASM_SHA256 in the .js)
  licence MIT (LICENSE). It is a WebAssembly build of zxing-cpp, Apache-2.0
         (https://github.com/zxing-cpp/zxing-cpp/blob/master/LICENSE).

tesseract-7.0.0/    OCR (Tesseract, LSTM engine only)
  from   npm tesseract.js@7.0.0 (2025-12-15)
         integrity sha512-exPBkd+z+wM1BuMkx/Bjv43OeLBxhL5kKWsz/9JY+DXcXdiBjiAch0V49QR3oAJqCaL5qURE0vx9Eo+G5YE7mA==
           tesseract.min.js, worker.min.js (+ their .LICENSE.txt) = dist/
         npm tesseract.js-core@7.0.0 (2025-12-15)
         integrity sha512-WnNH518NzmbSq9zgTPeoF8c+xmilS8rFIl1YKbk/ptuuc7p6cLNELNuPAzcmsYw450ca6bLa8j3t0VAtq435Vw==
           tesseract-core-simd-lstm.wasm.js (phones with WebAssembly SIMD)
           tesseract-core-lstm.wasm.js      (older phones without it)
         npm @tesseract.js-data/eng@1.0.0 (2023-09-04)
         integrity sha512-mbTumm6KQPUHyzTPQaF3ObXYnx0SqqfV2nabqFVQBwD6Kl7PhGSLSzOlfFTWy0P3BjghaSKA2W9GB19Jk+ZcTg==
           eng.traineddata.gz = 4.0.0_best_int/eng.traineddata.gz
  licence tesseract.js and tesseract.js-core: Apache-2.0 (LICENSE.md,
         LICENSE-core). Tesseract itself and its eng traineddata (Tesseract
         4.0.0 English LSTM model, integer version): Apache-2.0. Leptonica (inside the core):
         BSD-2-Clause. The bundles' own notices are in *.LICENSE.txt.

Sizes: 11.4 MB on disk (and in the MSI). A phone downloads ~0.4 MB (gzipped)
the first time it reads a barcode, ~4.5 MB more the first time it reads text.

To update: download the new npm tarballs, check their integrity, copy the
same files into a folder named for the new version, change SCAN_ZX /
SCAN_TS in index.html, and run python tests/test_vin_scan.py.
