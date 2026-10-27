# Browser assets

The shipped files in `html/vendor/model-viewer/` are rebuilt by
`scripts/vendor-model-viewer.mjs` from the npm lockfile. `checksums.json` records the
SHA-256 of each shipped file. Sources and licenses:

- Google `<model-viewer>`: Apache-2.0. See `LICENSE-model-viewer.txt` and
  https://github.com/google/model-viewer.
- Three.js: MIT. See `LICENSE-three.txt` and https://github.com/mrdoob/three.js.
- Lit: BSD-3-Clause. See `LICENSE-lit.txt` and https://github.com/lit/lit.
- MONOGRID gainmap-js: MIT. See `LICENSE-gainmap.txt` and
  https://github.com/MONOGRID/gainmap-js.
- Draco decoder: Apache-2.0. See `LICENSE-model-viewer.txt` for the license terms and
  https://github.com/google/draco for source and notices. Copyright Google LLC.
- Basis Universal transcoder: Apache-2.0. See `LICENSE-model-viewer.txt` for the license
  terms and https://github.com/BinomialLLC/basis_universal. Copyright Binomial LLC.
- Meshoptimizer decoder: MIT, Copyright (c) 2016-2025 Arseny Kapoulkine.
  Source: https://github.com/zeux/meshoptimizer. The original ES module from Three.js
  is converted to a classic script by removing its final export declaration.
  Its license terms follow.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
