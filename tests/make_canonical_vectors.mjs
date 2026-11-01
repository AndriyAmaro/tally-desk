// Gera tests/canonical_vectors.json com o JavaScript como referencia (Node).
// Uso: node tests/make_canonical_vectors.mjs > tests/canonical_vectors.json
import { createRequire } from "node:module";
const { canonical } = createRequire(import.meta.url)("../web/canonical.js");

const inputs = [
  '{"b":1,"a":[1.0,2.50,-0.0]}',
  '[100.0,5.8e-05,1e21,1e-7,1e-6,0.30000000000000004,1.2345678901234568e20,1.7976931348623157e308,5e-324,4.35,0.000001,-1.5e-10,1e22,12345.6789]',
  '{"z":null,"y":true,"x":false,"w":"text"}',
  '{"é":1,"e":2,"E":3,"😀":4,"～":5}',
  '"quote \\" backslash \\\\ tab \\t newline \\n control \\u0001 \\u001f del \\u007f"',
  '{"nested":{"b":[{"d":1,"c":2}],"a":{}},"empty":[]}',
  '[0,-0,0.0,1e0,10,100,1000000,1e6,1.0e+2]',
  '{"t":1760659200000,"v":20.40735,"edge":-1.0,"stop":81611.324799}',
  '"日本語 and emoji 😀 and accents é"',
];
const vectors = inputs.map((input) => ({ input, expected: canonical(JSON.parse(input)) }));
process.stdout.write(JSON.stringify(vectors, null, 1) + "\n");
