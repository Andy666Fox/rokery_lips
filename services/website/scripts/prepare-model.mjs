// Compress delivery bytes while verifying that geometry and textures stay identical.
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { dirname, resolve } from 'node:path';
import { NodeIO } from '@gltf-transform/core';
import { EXTMeshoptCompression } from '@gltf-transform/extensions';
import { MeshoptEncoder, MeshoptDecoder } from 'meshoptimizer';

if (process.argv.length !== 4) throw new Error('Usage: npm run prepare:model -- SOURCE.glb OUTPUT.glb');
const source = resolve(process.argv[2]);
const output = resolve(process.argv[3]);
if (source === output) throw new Error('Keep the original model separate from the web copy');
await Promise.all([MeshoptEncoder.ready, MeshoptDecoder.ready]);
const io = new NodeIO().registerExtensions([EXTMeshoptCompression]).registerDependencies({
	'meshopt.encoder': MeshoptEncoder,
	'meshopt.decoder': MeshoptDecoder,
});
const original = await io.read(source);
original.createExtension(EXTMeshoptCompression).setRequired(true).setEncoderOptions({
	method: EXTMeshoptCompression.EncoderMethod.QUANTIZE,
});
// No quantize(), simplification or texture transform is applied: raw accessor bytes stay intact.
await mkdir(dirname(output), { recursive: true });
await io.write(output, original);
const decoded = await io.read(output);
const expected = original.getRoot().listAccessors();
const actual = decoded.getRoot().listAccessors();
if (expected.length !== actual.length) throw new Error('Accessor count changed during compression');
for (let index = 0; index < expected.length; index++) {
	const left = expected[index].getArray();
	const right = actual[index].getArray();
	if (!left || !right || expected[index].getType() !== actual[index].getType()
		|| !Buffer.from(left.buffer, left.byteOffset, left.byteLength).equals(Buffer.from(right.buffer, right.byteOffset, right.byteLength))) {
		throw new Error(`Model data changed during compression: accessor ${index}`);
	}
}
const textures = original.getRoot().listTextures();
const restoredTextures = decoded.getRoot().listTextures();
if (textures.length !== restoredTextures.length) throw new Error('Texture count changed');
for (let index = 0; index < textures.length; index++) {
	if (!Buffer.from(textures[index].getImage()).equals(Buffer.from(restoredTextures[index].getImage()))) {
		throw new Error(`Texture changed during compression: ${index}`);
	}
}
const originalBytes = await readFile(source);
const compressedBytes = await readFile(output);
const receipt = {
	sourceSha256: createHash('sha256').update(originalBytes).digest('hex'),
	outputSha256: createHash('sha256').update(compressedBytes).digest('hex'),
	sourceBytes: originalBytes.length,
	outputBytes: compressedBytes.length,
	geometryAndTextures: 'verified identical after decoding',
};
await writeFile(output + '.json', JSON.stringify(receipt, null, 2) + '\n');
console.log(JSON.stringify(receipt));
