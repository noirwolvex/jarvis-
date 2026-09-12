import { mkdir, writeFile } from 'node:fs/promises';
import { contractNames, schemaDocument, schemaFor } from '../src/schemas.js';
const directory = new URL('../schemas/', import.meta.url);
await mkdir(directory, { recursive: true });
await writeFile(new URL('contracts.schema.json', directory), JSON.stringify(schemaDocument, null, 2) + '\n');
for (const name of contractNames) await writeFile(new URL(`${name}.schema.json`, directory), JSON.stringify(schemaFor(name), null, 2) + '\n');
console.log(`Exported ${contractNames.length} strict JSON Schema 2020-12 contracts.`);
